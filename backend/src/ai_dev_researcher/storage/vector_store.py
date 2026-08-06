from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ai_dev_researcher.storage.embedding_provider import EmbeddingProvider

logger = logging.getLogger(__name__)

_ADD_BATCH_SIZE = 1000

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    CHROMA_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖未安装时的降级路径
    chromadb = None  # type: ignore[assignment]
    ChromaSettings = None  # type: ignore[assignment,misc]
    CHROMA_AVAILABLE = False


@dataclass(frozen=True)
class Chunk:
    text: str
    artifact_id: str
    chunk_index: int
    start_char: int
    end_char: int
    score: float = 0.0


def split_text_into_chunks(text: str, *, max_tokens: int = 512) -> list[tuple[str, int, int]]:
    """按段落优先分块；超长段落按 token 近似（4 字符/token）滑动窗口切分。

    返回 [(text, start_char, end_char)]，start/end 是相对原始文本的字符偏移。
    """
    if not text:
        return []
    window_chars = max_tokens * 4
    # 段落优先：以空行切分。
    paragraphs = text.split("\n\n")
    chunks: list[tuple[str, int, int]] = []
    cursor = 0
    for paragraph in paragraphs:
        start = cursor
        cursor += len(paragraph) + 2  # 加上 \n\n
        if len(paragraph) <= window_chars:
            if paragraph.strip():
                chunks.append((paragraph, start, start + len(paragraph)))
            continue
        # 超长段落：滑动窗口切分，overlap 1/8。
        step = window_chars * 7 // 8
        offset = 0
        while offset < len(paragraph):
            end = min(offset + window_chars, len(paragraph))
            piece = paragraph[offset:end]
            if piece.strip():
                chunks.append((piece, start + offset, start + end))
            if end == len(paragraph):
                break
            offset = end - step
    return chunks


class VectorStore:
    """Chroma 持久化向量索引：按 artifact 分文档索引，支持多文档检索。"""

    def __init__(
        self,
        *,
        persist_dir: Path,
        embedding_provider: EmbeddingProvider,
        collection_name: str = "ai_dev_researcher_docs",
    ):
        self._persist_dir = persist_dir
        self._provider = embedding_provider
        self._collection_name = collection_name
        self._client = None
        self._collection = None

    def _ensure_client(self):
        if self._collection is not None:
            return
        if not CHROMA_AVAILABLE:
            raise RuntimeError("chromadb is not installed; install with: uv sync --extra rag")
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self._persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def available(self) -> bool:
        return CHROMA_AVAILABLE

    def index_document(self, *, artifact_id: str, text: str) -> int:
        """Chunk + embed + write to Chroma. Returns chunk count."""
        self._ensure_client()
        chunks = split_text_into_chunks(text)
        if not chunks:
            return 0
        # upsert: delete existing entries first so re-upload/re-parse stays consistent.
        try:
            self._collection.delete(where={"artifact_id": artifact_id})
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete failed for %s: %s; skipping index", artifact_id, exc)
            return 0

        # Chroma add has a per-call batch limit; embed + add in batches of
        # _ADD_BATCH_SIZE. On a batch failure, roll back inserted ids for this
        # artifact to avoid a half-indexed state.
        inserted_ids: list[str] = []
        try:
            for start in range(0, len(chunks), _ADD_BATCH_SIZE):
                batch = chunks[start : start + _ADD_BATCH_SIZE]
                texts = [chunk[0] for chunk in batch]
                vectors = self._provider.embed(texts)
                ids = [f"{artifact_id}#{idx}" for idx in range(start, start + len(batch))]
                metadatas = [
                    {
                        "artifact_id": artifact_id,
                        "chunk_index": idx,
                        "start_char": chunk[1],
                        "end_char": chunk[2],
                    }
                    for idx, chunk in enumerate(batch, start=start)
                ]
                self._collection.add(
                    ids=ids,
                    documents=texts,
                    embeddings=vectors,
                    metadatas=metadatas,
                )
                inserted_ids.extend(ids)
        except Exception as exc:  # noqa: BLE001
            if inserted_ids:
                try:
                    self._collection.delete(ids=inserted_ids)
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.warning(
                        "rollback delete failed for %s: %s", artifact_id, rollback_exc
                    )
            logger.warning(
                "index_document failed for %s after %d chunks: %s; rolled back",
                artifact_id,
                len(inserted_ids),
                exc,
            )
            return 0
        logger.info("indexed %d chunks for artifact %s", len(chunks), artifact_id)
        return len(chunks)

    def retrieve(
        self,
        *,
        query: str,
        artifact_ids: list[str],
        top_k: int = 5,
    ) -> list[Chunk]:
        """语义检索：限定在指定 artifact_ids 范围内，返回 top_k 个 Chunk。"""
        self._ensure_client()
        if not artifact_ids:
            return []
        try:
            query_vector = self._provider.embed([query])[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("query embedding failed: %s", exc)
            return []
        where = {"artifact_id": {"$in": artifact_ids}}
        try:
            result = self._collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                where=where,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("chroma query failed: %s", exc)
            return []
        ids = result.get("ids", [[]])[0]
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        chunks: list[Chunk] = []
        for idx, doc_id in enumerate(ids):
            meta = metadatas[idx] if idx < len(metadatas) else {}
            text = documents[idx] if idx < len(documents) else ""
            if not text:
                continue
            distance = distances[idx] if idx < len(distances) else 0.0
            # cosine 距离越小越相似；转成 0-1 的相似度得分便于展示。
            try:
                score = max(0.0, 1.0 - float(distance))
            except (TypeError, ValueError):
                score = 0.0
            chunks.append(
                Chunk(
                    text=text,
                    artifact_id=str(meta.get("artifact_id", artifact_ids[0])),
                    chunk_index=int(meta.get("chunk_index", 0)),
                    start_char=int(meta.get("start_char", 0)),
                    end_char=int(meta.get("end_char", 0)),
                    score=score,
                )
            )
        return chunks
