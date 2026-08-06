"""One-shot knowledge base rebuild script.

Reads the same .env-based Settings as the server, builds the embedding
provider + KnowledgeIndex, and runs a full (optionally reset) rebuild.

Usage:
    python scripts/rebuild_kb.py [--reset]
"""
from __future__ import annotations

import argparse
import logging
import sys

from ai_dev_researcher.core.config import Settings, get_settings
from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider
from ai_dev_researcher.storage.knowledge_index import KnowledgeIndex


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild the local knowledge base semantic index.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate the KB Chroma collection before rescanning.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    settings: Settings = get_settings()
    kb_root = settings.knowledge_base_root
    if kb_root is None or not kb_root.exists():
        print(f"knowledge base root missing: {kb_root}")
        return 1

    provider = SentenceTransformersProvider(
        model_name=settings.embedding_model,
        hf_hub_cache=settings.hf_hub_cache,
        embedding_offline=settings.embedding_offline,
    )
    index = KnowledgeIndex(
        kb_root=kb_root,
        persist_dir=settings.workspace_root / "vector_store",
        embedding_provider=provider,
    )
    if not index.available:
        print("chromadb is not installed; run: uv sync --extra rag")
        return 2

    file_count = len(list(index._iter_files()))
    print(f"knowledge base root: {kb_root}")
    print(f"files to scan (after exclusions): {file_count}")
    print(f"reset collection: {args.reset}")

    try:
        chunk_count = index.rebuild(reset=args.reset)
    except Exception as exc:  # noqa: BLE001
        print(f"rebuild failed: {exc}")
        return 3

    print(f"chunks upserted: {chunk_count}")
    print("rebuild complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
