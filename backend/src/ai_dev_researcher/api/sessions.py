from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ai_dev_researcher.api.dependencies import AppState, get_app_state
from ai_dev_researcher.api.schemas import SessionResponse
from ai_dev_researcher.core.errors import SessionNotFoundError

# Additive response model: exposes the new ``display_name`` field without
# modifying the shared ``api/schemas.py`` (owned outside this WP).
class SessionDetailResponse(SessionResponse):
    display_name: str | None = None


def _to_detail_response(session) -> SessionDetailResponse:
    return SessionDetailResponse(
        session_id=session.session_id,
        display_name=session.display_name,
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionDetailResponse, status_code=201)
async def create_session(state: AppState = Depends(get_app_state)) -> SessionDetailResponse:
    session = await state.session_service.create_session()
    return _to_detail_response(session)


@router.get("", response_model=list[SessionDetailResponse])
async def list_sessions(state: AppState = Depends(get_app_state)) -> list[SessionDetailResponse]:
    sessions = await state.session_service.list_sessions()
    return [_to_detail_response(session) for session in sessions]


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: UUID,
    state: AppState = Depends(get_app_state),
) -> SessionDetailResponse:
    session = await state.session_service.get_session(session_id)
    return _to_detail_response(session)


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: UUID,
    state: AppState = Depends(get_app_state),
) -> None:
    deleted = await state.session_service.delete_session(session_id)
    if not deleted:
        raise SessionNotFoundError(f"session not found: {session_id}")
    return None
