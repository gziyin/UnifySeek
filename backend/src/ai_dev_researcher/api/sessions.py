from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from ai_dev_researcher.api.dependencies import AppState, get_app_state
from ai_dev_researcher.api.schemas import SessionResponse

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(state: AppState = Depends(get_app_state)) -> SessionResponse:
    session = await state.session_service.create_session()
    return SessionResponse(
        session_id=session.session_id,
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: UUID,
    state: AppState = Depends(get_app_state),
) -> SessionResponse:
    session = await state.session_service.get_session(session_id)
    return SessionResponse(
        session_id=session.session_id,
        status=session.status,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
