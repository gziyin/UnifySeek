from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ai_dev_researcher.api import artifacts, runs, sessions, uploads, websocket
from ai_dev_researcher.api.dependencies import AppState
from ai_dev_researcher.core.config import Settings, get_settings
from ai_dev_researcher.core.errors import AppError
from ai_dev_researcher.repositories.artifacts import ArtifactRepository
from ai_dev_researcher.repositories.events import EventRepository
from ai_dev_researcher.repositories.evidence import EvidenceRepository
from ai_dev_researcher.repositories.runs import RunRepository
from ai_dev_researcher.repositories.sessions import SessionRepository
from ai_dev_researcher.repositories.sqlite import connect, init_db
from ai_dev_researcher.services.agent_executor import AgentResearchExecutor
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.fake_executor import FakeResearchExecutor
from ai_dev_researcher.services.run_service import RunService
from ai_dev_researcher.services.session_service import SessionService
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.services.upload_service import UploadService
from ai_dev_researcher.storage.paths import WorkspacePaths

logger = logging.getLogger(__name__)


def _try_build_vector_store(settings: Settings, provider=None):
    """依赖可用时构建 RAG 向量库；不可用返回 None（上传/检索优雅降级）。"""
    try:
        if provider is None:
            from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider

            provider = SentenceTransformersProvider(
                model_name=settings.embedding_model,
                hf_hub_cache=settings.hf_hub_cache,
                embedding_offline=settings.embedding_offline,
            )
        from ai_dev_researcher.storage.vector_store import VectorStore

        persist_dir = settings.workspace_root / "vector_store"
        store = VectorStore(persist_dir=persist_dir, embedding_provider=provider)
        if not store.available:
            return None
        return store
    except Exception as exc:  # noqa: BLE001 - RAG 可选，失败不阻塞启动
        logger.warning("vector store unavailable, RAG search disabled: %s", exc)
        return None


def _try_build_knowledge_index(settings: Settings, provider=None) -> object | None:
    """构建知识库语义索引并注册；不可用/未就绪时返回 None。

    索引在后台异步重建（见 lifespan），不阻塞 API 启动；未就绪时
    ``search_knowledge_base`` 工具返回 ``{"results": [], "note": "indexing"}``。
    """
    try:
        if settings.fake_agent_mode:
            return None
        kb_root = settings.knowledge_base_root
        if kb_root is None or not kb_root.exists():
            return None
        if provider is None:
            from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider

            provider = SentenceTransformersProvider(
                model_name=settings.embedding_model,
                hf_hub_cache=settings.hf_hub_cache,
                embedding_offline=settings.embedding_offline,
            )
        from ai_dev_researcher.storage.knowledge_index import KnowledgeIndex

        index = KnowledgeIndex(
            kb_root=kb_root,
            persist_dir=settings.workspace_root / "vector_store",
            embedding_provider=provider,
        )
        return index
    except Exception as exc:  # noqa: BLE001 - KB RAG 可选，失败不阻塞启动
        logger.warning("knowledge index unavailable, KB search disabled: %s", exc)
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        settings.sessions_root.mkdir(parents=True, exist_ok=True)
        conn = await connect(str(settings.db_path))
        await init_db(conn)

        paths = WorkspacePaths(settings.sessions_root, knowledge_base_root=settings.knowledge_base_root)
        sessions_repo = SessionRepository(conn)
        runs_repo = RunRepository(conn)
        artifacts_repo = ArtifactRepository(conn)
        events_repo = EventRepository(conn)
        evidence_repo = EvidenceRepository(conn)
        publisher = EventPublisher(events_repo, queue_size=settings.ws_send_queue_size)

        interrupted = await runs_repo.mark_stale_interrupted()
        if interrupted:
            # No publisher fanout needed for historical interrupted runs on boot.
            pass

        provider = None
        try:
            from ai_dev_researcher.storage.embedding_provider import SentenceTransformersProvider

            provider = SentenceTransformersProvider(
                model_name=settings.embedding_model,
                hf_hub_cache=settings.hf_hub_cache,
                embedding_offline=settings.embedding_offline,
            )
        except Exception as exc:  # noqa: BLE001 - embedding 可选，失败不影响启动
            logger.warning("embedding provider unavailable: %s", exc)

        vector_store = _try_build_vector_store(settings, provider)
        knowledge_index = _try_build_knowledge_index(settings, provider)

        # 索引后台异步构建，不阻塞 API 启动；失败仅置为未就绪（工具返回 indexing 提示）。
        kb_build_task: asyncio.Task | None = None
        if knowledge_index is not None:

            async def _build_kb_background() -> None:
                try:
                    await asyncio.to_thread(knowledge_index.rebuild)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("knowledge index background build failed: %s", exc)

            kb_build_task = asyncio.create_task(_build_kb_background())

        def executor_factory():
            if settings.fake_agent_mode or not settings.deepseek_api_key:
                return FakeResearchExecutor(
                    runs=runs_repo,
                    artifacts=artifacts_repo,
                    evidence=evidence_repo,
                    publisher=publisher,
                    paths=paths,
                )
            return AgentResearchExecutor(
                settings=settings,
                runs=runs_repo,
                artifacts=artifacts_repo,
                evidence=evidence_repo,
                publisher=publisher,
                paths=paths,
                vector_store=vector_store,
                knowledge_index=knowledge_index,
            )

        task_manager = TaskManager(executor_factory)
        container = AppState(
            settings=settings,
            conn=conn,
            paths=paths,
            sessions=sessions_repo,
            runs=runs_repo,
            artifacts=artifacts_repo,
            events=events_repo,
            evidence=evidence_repo,
            publisher=publisher,
            session_service=SessionService(sessions_repo, paths),
            upload_service=UploadService(
                sessions=sessions_repo,
                artifacts=artifacts_repo,
                paths=paths,
                settings=settings,
                vector_store=vector_store,
            ),
            run_service=RunService(
                sessions=sessions_repo,
                runs=runs_repo,
                artifacts=artifacts_repo,
                paths=paths,
                publisher=publisher,
                task_manager=task_manager,
            ),
            task_manager=task_manager,
            vector_store=vector_store,
            knowledge_index=knowledge_index,
        )
        app.state.container = container
        try:
            yield
        finally:
            if kb_build_task is not None:
                kb_build_task.cancel()
            await task_manager.shutdown()
            await conn.close()

    app = FastAPI(
        title="AI Dev Researcher",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(sessions.router)
    app.include_router(uploads.router)
    app.include_router(runs.router)
    app.include_router(artifacts.router)
    app.include_router(websocket.router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "ai_dev_researcher.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
