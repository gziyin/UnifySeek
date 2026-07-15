from __future__ import annotations

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
from ai_dev_researcher.services.event_publisher import EventPublisher
from ai_dev_researcher.services.executor_factory import create_run_executor
from ai_dev_researcher.services.run_service import RunService
from ai_dev_researcher.services.session_service import SessionService
from ai_dev_researcher.services.task_manager import TaskManager
from ai_dev_researcher.services.upload_service import UploadService
from ai_dev_researcher.storage.paths import WorkspacePaths


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        settings.sessions_root.mkdir(parents=True, exist_ok=True)
        conn = await connect(str(settings.db_path))
        await init_db(conn)

        paths = WorkspacePaths(settings.sessions_root)
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

        def executor_factory():
            return create_run_executor(
                settings=settings,
                runs=runs_repo,
                artifacts=artifacts_repo,
                evidence=evidence_repo,
                publisher=publisher,
                paths=paths,
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
        )
        # Wire run_service after construction to avoid circular init issues.
        container.run_service = RunService(
            sessions=sessions_repo,
            runs=runs_repo,
            artifacts=artifacts_repo,
            paths=paths,
            publisher=publisher,
            task_manager=task_manager,
        )
        app.state.container = container
        try:
            yield
        finally:
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
