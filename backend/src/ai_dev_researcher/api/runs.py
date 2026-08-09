from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from ai_dev_researcher.api.dependencies import AppState, get_app_state
from ai_dev_researcher.api.schemas import (
    CreateRunRequest,
    EventResponse,
    EventsListResponse,
    RunResponse,
)

router = APIRouter(tags=["runs"])


async def _to_run_response(state: AppState, run) -> RunResponse:
    last_seq = await state.events.high_seq(run.run_id)
    return RunResponse(
        run_id=run.run_id,
        session_id=run.session_id,
        status=run.status,
        question=run.request.question,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error_code=run.error_code,
        error_message=run.error_message,
        report_artifact_id=run.report_artifact_id,
        last_seq=last_seq,
    )


@router.post(
    "/api/sessions/{session_id}/runs",
    response_model=RunResponse,
    status_code=202,
)
async def create_run(
    session_id: UUID,
    body: CreateRunRequest,
    response: Response,
    state: AppState = Depends(get_app_state),
) -> RunResponse:
    run = await state.run_service.create_run(session_id, body)
    response.status_code = 202
    return await _to_run_response(state, run)


@router.get("/api/sessions/{session_id}/runs", response_model=list[RunResponse])
async def list_session_runs(
    session_id: UUID,
    limit: int = 50,
    offset: int = 0,
    state: AppState = Depends(get_app_state),
) -> list[RunResponse]:
    runs = await state.runs.list_for_session(session_id, limit=limit, offset=offset)
    return [await _to_run_response(state, run) for run in runs]


@router.get("/api/runs/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: UUID,
    state: AppState = Depends(get_app_state),
) -> RunResponse:
    run = await state.run_service.get_run(run_id)
    return await _to_run_response(state, run)


@router.post("/api/runs/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: UUID,
    state: AppState = Depends(get_app_state),
) -> RunResponse:
    run = await state.run_service.cancel_run(run_id)
    return await _to_run_response(state, run)


@router.get("/api/runs/{run_id}/events", response_model=EventsListResponse)
async def list_events(
    run_id: UUID,
    after_seq: int = 0,
    state: AppState = Depends(get_app_state),
) -> EventsListResponse:
    await state.run_service.get_run(run_id)
    events = await state.events.list_after(run_id, after_seq)
    last_seq = await state.events.high_seq(run_id)
    return EventsListResponse(
        run_id=run_id,
        events=[
            EventResponse(
                protocol_version=event.protocol_version,
                event_id=event.event_id,
                seq=event.seq,
                session_id=event.session_id,
                run_id=event.run_id,
                type=event.type,
                occurred_at=event.occurred_at,
                actor=event.actor,
                payload=event.payload,
            )
            for event in events
        ],
        last_seq=last_seq,
    )
