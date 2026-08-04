"""KnowledgeIndex service: semantic index over the local knowledge_base tree.

WP-A (A1): scans ``knowledge_base/`` (skipping .venv/node_modules/.git/
__pycache__/dist), routes code through the structure-aware chunker
(:mod:`ai_dev_researcher.storage.code_chunker`) and documents/notes through
the existing paragraph chunker, and persists chunks into a dedicated Chroma
collection (``ai_dev_researcher_kb``) kept fully separate from uploaded
documents.

P0 dual encoding: each chunk is embedded from a lightweight heuristic summary
(docstring / first comment / symbol name) while the original source text is
stored for retrieval, so similarity search is guided by natural-language
semantics without calling an LLM.

Delete sync: incremental rebuilds compare the currently scanned file path set
against the previously indexed set and delete entries whose file no longer
exists (or whose mtime changed, via delete + re-add).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ai_dev_researcher.storage.code_chunker import (
    SUPPORTED_EXTENSIONS,
    chunk_file,
    generate_summary,
)
from ai_dev_researcher.storage.embedding_provider import EmbeddingProvider

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    CHROMA_AVAILABLE = True
except ImportError:  # pragma: no cover - 依赖未安装时的降级路径
    chromadb = None  # type: ignore[assignment]
    ChromaSettings = None  # type: ignore[assignment,misc]
    CHROMA_AVAILABLE = False

KB_COLLECTION_NAME = "ai_dev_researcher_kb"
SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__", "dist", ".pytest_cache"}
_METADATA_BATCH = 5000


@dataclass(frozen=True)
class KbChunk:
    """A retrieved knowledge base chunk (contract with WP-A search tool)."""

    file_path: str
    symbol: str
    parent_symbol: str
    kind: str
    line_start: int
    line_end: int
    score: float
    text: str

    def to_dict(self) -> dict:
        """Serialize to the tool-facing result dict."""
        return {
            "file_path": self.file_path,
            "symbol": self.symbol,
            "parent_symbol": self.parent_symbol,
            "kind": self.kind,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "score": self.score,
            "text": self.text,
        }


class KnowledgeIndex:
    """Chroma-backed semantic index over the local knowledge base tree."""

    def __init__(
        self,
        *,
        kb_root: Path,
        persist_dir: Path,
        embedding_provider: EmbeddingProvider,
        collection_name: str = KB_COLLECTION_NAME,
        max_tokens: int = 512,
    ):
        self._kb_root = Path(kb_root).resolve()
        self._persist_dir = Path(persist_dir)
        self._provider = embedding_provider
        self._collection_name = collection_name
        self._max_tokens = max_tokens
        self._client = None
        self._collection = None
        self._ready = False
        self._lock = threading.Lock()
        self._last_chunk_count = 0

    # -- readiness ---------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """True once at least one successful rebuild completed."""
        return self._ready

    @property
    def kb_root(self) -> Path:
        return self._kb_root

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def last_chunk_count(self) -> int:
        return self._last_chunk_count

    # -- chroma ------------------------------------------------------------

    @property
    def available(self) -> bool:
        return CHROMA_AVAILABLE

    def _ensure_client(self) -> None:
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

    # -- scanning ----------------------------------------------------------

    def _iter_files(self) -> Iterator[Path]:
        if not self._kb_root.exists():
            return
        for root, dirs, files in os.walk(self._kb_root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(files):
                if name.startswith("."):
                    continue
                path = Path(root) / name
                if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    yield path

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._kb_root).as_posix()

    # -- indexing ----------------------------------------------------------

    def _index_file(self, path: Path) -> list[tuple[str, dict, str]]:
        """Return ``[(text, metadata, summary)]`` for a single file."""
        rel = self._relative(path)
        mtime = float(path.stat().st_mtime)
        source = path.read_text(encoding="utf-8", errors="replace")
        chunks = chunk_file(source, path.name, max_tokens=self._max_tokens)
        items: list[tuple[str, dict, str]] = []
        for idx, chunk in enumerate(chunks):
            summary = generate_summary(
                chunk.text,
                chunk.symbol,
                chunk.kind,
                chunk.parent_symbol,
                path.name,
            )
            metadata = {
                "file_path": rel,
                "symbol": chunk.symbol,
                "parent_symbol": chunk.parent_symbol,
                "kind": chunk.kind,
                "line_start": chunk.start_line,
                "line_end": chunk.end_line,
                "chunk_index": idx,
                "mtime": mtime,
            }
            items.append((chunk.text, metadata, summary))
        return items

    def _existing_state(self) -> tuple[set[str], dict[str, float]]:
        """Load previously indexed file paths and their mtimes from Chroma."""
        stored_paths: set[str] = set()
        stored_mtimes: dict[str, float] = {}
        offset = 0
        paginate = True
        while True:
            try:
                if paginate:
                    result = self._collection.get(  # type: ignore[union-attr]
                        include=["metadatas"], limit=_METADATA_BATCH, offset=offset
                    )
                else:
                    result = self._collection.get(include=["metadatas"])  # type: ignore[union-attr]
            except TypeError:
                paginate = False
                result = self._collection.get(include=["metadatas"])  # type: ignore[union-attr]
            metas = result.get("metadatas") or []
            if not metas:
                break
            for meta in metas:
                fp = meta.get("file_path")
                if fp and fp not in stored_paths:
                    stored_paths.add(fp)
                    try:
                        stored_mtimes[fp] = float(meta.get("mtime", 0.0))
                    except (TypeError, ValueError):
                        stored_mtimes[fp] = 0.0
            if not paginate or len(metas) < _METADATA_BATCH:
                break
            offset += _METADATA_BATCH
        return stored_paths, stored_mtimes

    def rebuild(self) -> int:
        """Incremental rebuild: index new/changed files, delete stale entries.

        Returns the number of chunks indexed/updated in this pass.
        """
        with self._lock:
            if not self._kb_root.exists():
                logger.warning("knowledge base root missing: %s", self._kb_root)
                return 0
            try:
                self._ensure_client()
            except RuntimeError as exc:
                logger.warning("knowledge index unavailable: %s", exc)
                return 0

            files = list(self._iter_files())
            stored_paths, stored_mtimes = self._existing_state()
            current_paths = {self._relative(p) for p in files}

            # Delete-sync: remove entries whose file no longer exists.
            for stale in sorted(stored_paths - current_paths):
                try:
                    self._collection.delete(where={"file_path": stale})  # type: ignore[union-attr]
                    logger.info("deleted stale KB entries for %s", stale)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("failed to delete stale KB entries for %s: %s", stale, exc)

            total = 0
            for path in files:
                rel = self._relative(path)
                try:
                    mtime = float(path.stat().st_mtime)
                except OSError:
                    continue
                if rel in stored_paths and abs(stored_mtimes.get(rel, -1.0) - mtime) < 0.001:
                    continue  # unchanged since last index
                try:
                    items = self._index_file(path)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("failed to index %s: %s", rel, exc)
                    continue
                if not items:
                    continue
                try:
                    vectors = self._provider.embed([item[2] for item in items])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("embedding failed for %s: %s; skipping", rel, exc)
                    continue
                texts = [item[0] for item in items]
                metadatas = [item[1] for item in items]
                ids = [f"{rel}#{idx}" for idx in range(len(items))]
                try:
                    self._collection.delete(where={"file_path": rel})  # type: ignore[union-attr]
                    self._collection.add(  # type: ignore[union-attr]
                        ids=ids,
                        documents=texts,
                        embeddings=vectors,
                        metadatas=metadatas,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("chroma add failed for %s: %s", rel, exc)
                    continue
                total += len(items)

            self._ready = True
            self._last_chunk_count = total
            logger.info(
                "knowledge base rebuild complete: %d files scanned, %d chunks upserted",
                len(files),
                total,
            )
            return total

    # -- retrieval ---------------------------------------------------------

    def retrieve(
        self,
        query: str,
        path: str | None = None,
        top_k: int = 10,
        score_threshold: float = 0.0,
    ) -> list[KbChunk]:
        """Semantic retrieval over the knowledge base; returns scored chunks."""
        if not self._ready:
            return []
        try:
            self._ensure_client()
        except RuntimeError:
            return []
        try:
            query_vector = self._provider.embed([query])[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("query embedding failed: %s", exc)
            return []
        where = {"file_path": path} if path else None
        try:
            result = self._collection.query(  # type: ignore[union-attr]
                query_embeddings=[query_vector],
                n_results=max(1, int(top_k)),
                where=where,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("chroma query failed: %s", exc)
            return []
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        distances = result.get("distances") or []
        ids = ids[0] if ids else []
        documents = documents[0] if documents else []
        metadatas = metadatas[0] if metadatas else []
        distances = distances[0] if distances else []

        chunks: list[KbChunk] = []
        for idx, doc_id in enumerate(ids):
            text = documents[idx] if idx < len(documents) else ""
            if not text:
                continue
            meta = metadatas[idx] if idx < len(metadatas) else {}
            distance = distances[idx] if idx < len(distances) else 0.0
            try:
                score = max(0.0, 1.0 - float(distance))
            except (TypeError, ValueError):
                score = 0.0
            if score < score_threshold:
                continue
            chunks.append(
                KbChunk(
                    file_path=str(meta.get("file_path", "")),
                    symbol=str(meta.get("symbol", "")),
                    parent_symbol=str(meta.get("parent_symbol", "")),
                    kind=str(meta.get("kind", "")),
                    line_start=int(meta.get("line_start", 0)),
                    line_end=int(meta.get("line_end", 0)),
                    score=score,
                    text=text,
                )
            )
        return chunks
