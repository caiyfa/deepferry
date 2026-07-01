"""Unit tests for the WebSocket event-push module (``deepferry.web.ws``).

Uses a ``FakeWebSocket`` to exercise ``AgentConnectionManager`` without
requiring a real ASGI server handshake.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from deepferry.core.trace import TraceSink
from deepferry.web.ws import (
    AgentConnectionManager,
    build_agent_ws_route,
    subscribe_to_sink,
)


class FakeWebSocket:
    """A minimal WebSocket stand-in that records sent messages.

    Parameters
    ----------
    fail_on_send : bool
        When ``True`` every ``send_text()`` call raises ``RuntimeError``,
        simulating a dead / disconnected client.
    """

    def __init__(self, *, fail_on_send: bool = False) -> None:
        self.accepted = False
        self.sent: list[str] = []
        self.disconnected = False
        self.fail_on_send = fail_on_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, data: str) -> None:
        if self.fail_on_send:
            raise RuntimeError("fake send failure")
        self.sent.append(data)

    async def receive_text(self) -> str:
        return ""


@pytest.fixture
def manager() -> AgentConnectionManager:
    return AgentConnectionManager()


@pytest.mark.asyncio
async def test_connect_adds_to_connections(manager: AgentConnectionManager) -> None:
    ws = FakeWebSocket()
    await manager.connect(ws)
    try:
        assert ws.accepted is True
        assert ws in manager._connections
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_connect_starts_heartbeat_once(manager: AgentConnectionManager) -> None:
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()

    await manager.connect(ws1)
    try:
        first_task = manager._heartbeat_task
        assert first_task is not None

        await asyncio.sleep(0)
        await manager.connect(ws2)
        assert manager._heartbeat_task is first_task
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_disconnect_removes_from_set(manager: AgentConnectionManager) -> None:
    ws = FakeWebSocket()
    await manager.connect(ws)
    try:
        assert ws in manager._connections
        manager.disconnect(ws)
        assert ws not in manager._connections
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_disconnect_idempotent(manager: AgentConnectionManager) -> None:
    ws = FakeWebSocket()
    await manager.connect(ws)
    try:
        manager.disconnect(ws)
        manager.disconnect(ws)
        assert ws not in manager._connections
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_broadcast_sends_to_all(manager: AgentConnectionManager) -> None:
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()
    await manager.connect(ws1)
    await manager.connect(ws2)
    try:
        await manager.broadcast({"type": "execution_started", "id": 1})

        assert len(ws1.sent) == 1
        assert len(ws2.sent) == 1
        payload = json.loads(ws1.sent[0])
        assert payload == {"type": "execution_started", "id": 1}
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_broadcast_disconnects_failing_client(
    manager: AgentConnectionManager,
) -> None:
    ws_good = FakeWebSocket()
    ws_bad = FakeWebSocket(fail_on_send=True)
    await manager.connect(ws_good)
    await manager.connect(ws_bad)
    try:
        await manager.broadcast({"type": "ping"})

        assert len(ws_good.sent) == 1
        assert ws_bad not in manager._connections
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_on_trace_event_forwards_to_broadcast(
    manager: AgentConnectionManager,
) -> None:
    ws = FakeWebSocket()
    await manager.connect(ws)
    try:
        event: dict[str, Any] = {
            "type": "execution_started",
            "execution_id": 7,
            "source_id": "pg-prod",
        }
        await manager.on_trace_event(event)

        assert len(ws.sent) == 1
        parsed = json.loads(ws.sent[0])
        assert parsed["type"] == "execution_started"
        assert parsed["execution_id"] == 7
        assert parsed["source_id"] == "pg-prod"
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_subscribe_to_sink_registers_callback(
    manager: AgentConnectionManager,
) -> None:
    import aiosqlite

    db = await aiosqlite.connect(":memory:")
    await TraceSink.init_schema(db)
    sink = TraceSink(db)

    subscribe_to_sink(manager, sink)
    assert manager.on_trace_event in sink._subscribers

    await db.close()


@pytest.mark.asyncio
async def test_aclose_cancels_heartbeat_and_clears_connections(
    manager: AgentConnectionManager,
) -> None:
    ws = FakeWebSocket()
    await manager.connect(ws)
    assert manager._heartbeat_task is not None
    assert len(manager._connections) == 1

    await manager.aclose()

    assert manager._heartbeat_task is None or manager._heartbeat_task.done()
    assert len(manager._connections) == 0


@pytest.mark.asyncio
async def test_heartbeat_sends_ping(manager: AgentConnectionManager) -> None:
    import deepferry.web.ws as ws_mod

    orig_interval = ws_mod._HEARTBEAT_INTERVAL
    ws_mod._HEARTBEAT_INTERVAL = 0.02
    try:
        ws = FakeWebSocket()
        await manager.connect(ws)
        await asyncio.sleep(0.1)

        pings = [m for m in ws.sent if json.loads(m).get("type") == "ping"]
        assert len(pings) >= 1
    finally:
        ws_mod._HEARTBEAT_INTERVAL = orig_interval
        await manager.aclose()


@pytest.mark.asyncio
async def test_heartbeat_disconnects_dead_client(
    manager: AgentConnectionManager,
) -> None:
    import deepferry.web.ws as ws_mod

    orig_interval = ws_mod._HEARTBEAT_INTERVAL
    ws_mod._HEARTBEAT_INTERVAL = 0.02
    try:
        ws_good = FakeWebSocket()
        ws_dead = FakeWebSocket(fail_on_send=True)
        await manager.connect(ws_good)
        await manager.connect(ws_dead)
        await asyncio.sleep(0.1)

        assert ws_dead not in manager._connections
        assert ws_good in manager._connections
    finally:
        ws_mod._HEARTBEAT_INTERVAL = orig_interval
        await manager.aclose()


def test_build_agent_ws_route_returns_websocket_route(
    manager: AgentConnectionManager,
) -> None:
    route = build_agent_ws_route(manager)
    assert route.path == "/ws/agents"
    assert route.name == "agent_ws"


@pytest.mark.asyncio
async def test_endpoint_handles_connect_and_disconnect(
    manager: AgentConnectionManager,
) -> None:
    from deepferry.web.ws import _agent_ws_endpoint

    ws = FakeWebSocket()
    call_count = 0

    async def _receive_text() -> str:
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return "client message"
        from starlette.websockets import WebSocketDisconnect

        raise WebSocketDisconnect(code=1000)

    ws.receive_text = _receive_text  # type: ignore[method-assign]

    await _agent_ws_endpoint(ws, manager)  # type: ignore[arg-type]

    assert ws.accepted is True
    assert ws not in manager._connections
    await manager.aclose()
