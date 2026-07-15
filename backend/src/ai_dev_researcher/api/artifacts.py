from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ai_dev_researcher.api.dependencies import AppState, get_app_state
from ai_dev_researcher.api.schemas import ArtifactContentResponse, ArtifactResponse
from ai_dev_researcher.core.errors import ArtifactAccessError, ArtifactNotFoundError
from ai_dev_researcher.core.security import ensure_within_root
from ai_dev_researcher.domain.artifacts import ArtifactKind

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


def _to_response(artifact) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=artifact.artifact_id,
        session_id=artifact.session_id,
        run_id=artifact.run_id,
        kind=artifact.kind.value,
        display_name=artifact.display_name,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        parse_status=artifact.parse_status.value,
        created_at=artifact.created_at,
    )


def _resolve_readable_path(artifact, sessions_root: Path) -> Path:
    if artifact.kind == ArtifactKind.REPORT:
        raw = artifact.original_storage_path
    elif artifact.normalized_storage_path:
        raw = artifact.normalized_storage_path
    else:
        raw = artifact.original_storage_path
    if not raw:
        raise ArtifactAccessError("artifact has no readable content")
    path = ensure_within_root(Path(raw), sessions_root)
    if not path.exists():
        raise ArtifactNotFoundError("artifact file missing")
    return path


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: UUID,
    state: AppState = Depends(get_app_state),
) -> ArtifactResponse:
    artifact = await state.artifacts.get(artifact_id)
    if artifact is None:
        raise ArtifactNotFoundError(f"artifact not found: {artifact_id}")
    return _to_response(artifact)


@router.get("/{artifact_id}/content", response_model=ArtifactContentResponse)
async def get_artifact_content(
    artifact_id: UUID,
    state: AppState = Depends(get_app_state),
) -> ArtifactContentResponse:
    artifact = await state.artifacts.get(artifact_id)
    if artifact is None:
        raise ArtifactNotFoundError(f"artifact not found: {artifact_id}")
    path = _resolve_readable_path(artifact, state.paths.sessions_root)
    if path.suffix.lower() not in {".md", ".txt"} and artifact.kind != ArtifactKind.REPORT:
        # normalized docs are .txt; reports are .md
        if artifact.kind == ArtifactKind.UPLOAD and artifact.normalized_storage_path:
            path = ensure_within_root(
                Path(artifact.normalized_storage_path),
                state.paths.sessions_root,
            )
        else:
            raise ArtifactAccessError("artifact content is not text")
    content = path.read_text(encoding="utf-8")
    return ArtifactContentResponse(
        artifact_id=artifact.artifact_id,
        kind=artifact.kind.value,
        content=content,
    )


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: UUID,
    state: AppState = Depends(get_app_state),
):
    artifact = await state.artifacts.get(artifact_id)
    if artifact is None:
        raise ArtifactNotFoundError(f"artifact not found: {artifact_id}")
    path = _resolve_readable_path(artifact, state.paths.sessions_root)
    return FileResponse(
        path=path,
        media_type=artifact.mime_type,
        filename=artifact.display_name,
        content_disposition_type="attachment",
    )
