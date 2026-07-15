from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ai_dev_researcher.api.dependencies import AppState
from ai_dev_researcher.core.errors import RunNotFoundError
from ai_dev_researcher.domain.sessions import utc_now

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/runs/{run_id}")
async def run_events_ws(websocket: WebSocket, run_id: UUID, after_seq: int = 0) -> None:
    state: AppState = websocket.app.state.container
    await websocket.accept()

    run = await state.runs.get(run_id)
    if run is None:
        await websocket.close(code=4404)
        return

    queue = await state.publisher.subscribe(run_id)
    try:
        high_seq = await state.events.high_seq(run_id)
        replay = await state.events.list_range(run_id, after_seq, high_seq)
        for event in replay:
            await websocket.send_json(event.model_dump(mode="json"))

        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=state.settings.heartbeat_interval_seconds,
                )
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "protocol_version": "1.0",
                        "event_id": str(uuid4()),
                        "seq": -1,
                        "session_id": str(run.session_id),
                        "run_id": str(run_id),
                        "type": "heartbeat",
                        "occurred_at": utc_now().isoformat(),
                        "actor": "system",
                        "payload": {"server_time": utc_now().isoformat()},
                    }
                )
                continue

            if item is None:
                await websocket.close(code=1013)
                return
            if item.seq <= high_seq:
                continue
            await websocket.send_json(item.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    except RunNotFoundError:
        await websocket.close(code=4404)
    finally:
        await state.publisher.unsubscribe(run_id, queue)
