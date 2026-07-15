from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile

from ai_dev_researcher.api.dependencies import AppState, get_app_state
from ai_dev_researcher.api.schemas import ArtifactResponse
from ai_dev_researcher.core.errors import InvalidUploadError

router = APIRouter(tags=["artifacts"])


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


@router.post("/api/sessions/{session_id}/uploads", response_model=ArtifactResponse, status_code=201)
async def upload_file(
    session_id: UUID,
    file: UploadFile = File(...),
    state: AppState = Depends(get_app_state),
) -> ArtifactResponse:
    data = await file.read()
    if not data:
        raise InvalidUploadError("empty file")
    artifact = await state.upload_service.upload(
        session_id=session_id,
        filename=file.filename or "upload.bin",
        content_type=file.content_type,
        data=data,
    )
    return _to_response(artifact)


@router.get("/api/sessions/{session_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(
    session_id: UUID,
    state: AppState = Depends(get_app_state),
) -> list[ArtifactResponse]:
    await state.session_service.get_session(session_id)
    items = await state.artifacts.list_for_session(session_id)
    return [_to_response(item) for item in items]
