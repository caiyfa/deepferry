"""WebSocket event-push module for the deepferry agent monitor.

Provides ``AgentConnectionManager`` which manages connected WebSocket clients,
broadcasts trace events to all of them, and handles disconnects cleanly.
A heartbeat ping keeps connections alive every 30 s.

The manager subscribes to a ``TraceSink`` event broker so real trace mutations
fan out to all clients.  ``subscribe_to_sink`` wires the manager into the sink;
the orchestrator calls this during startup.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import starlette.routing
from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from deepferry.core.trace import TraceSink

_HEARTBEAT_INTERVAL: float = 30.0


class AgentConnectionManager:
    """Manages WebSocket clients subscribed to agent/trace events.

    Registered as a ``TraceSink`` subscriber so every trace mutation is
    broadcast to all connected clients.  Heartbeat pings every 30 s detect
    dead clients.

    Usage::

        manager = AgentConnectionManager()
        subscribe_to_sink(manager, trace_sink)
        # In Starlette routing:
        routes = [build_agent_ws_route(manager)]
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._logger = logging.getLogger(__name__)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept *websocket*, register it, and lazily start heartbeat.

        The heartbeat task is started once (on the first connection) and
        lives for the lifetime of the manager, even if connections drop
        to zero.
        """
        await websocket.accept()
        self._connections.add(websocket)

        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(),
                name="agent-ws-heartbeat",
            )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove *websocket* from the active set (idempotent)."""
        self._connections.discard(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Best-effort fan-out of *event* to every connected client.

        Connections that fail to send are automatically disconnected.
        """
        for ws in list(self._connections):
            try:
                await ws.send_text(json.dumps(event, default=str))
            except Exception:
                self.disconnect(ws)

    async def _heartbeat_loop(self) -> None:
        """Send a ``ping`` message to every client every 30 s.

        Dead clients are pruned when a send fails.  The loop continues even
        when the connection set is empty — it is cheap to keep running.
        """
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            for ws in list(self._connections):
                try:
                    await ws.send_text(
                        json.dumps(
                            {"type": "ping", "ts": int(time.time() * 1000)}
                        )
                    )
                except Exception:
                    self.disconnect(ws)

    async def aclose(self) -> None:
        """Cancel the heartbeat task and clear all connections."""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        self._connections.clear()

    async def on_trace_event(self, event: dict[str, Any]) -> None:
        """``TraceEventCallback``-compatible subscriber callback.

        Forwards *event* to :meth:`broadcast` so every connected client
        receives real-time trace mutations.
        """
        await self.broadcast(event)


def subscribe_to_sink(
    manager: AgentConnectionManager,
    sink: TraceSink,
) -> None:
    """Register *manager.on_trace_event* as a subscriber of *sink*.

    This is a pure helper — the orchestrator calls it during startup
    wiring.
    """
    sink.add_subscriber(manager.on_trace_event)


def _make_agent_ws_handler(
    manager: AgentConnectionManager,
) -> Callable[[WebSocket], Awaitable[None]]:
    """Return an async Starlette WebSocket handler bound to *manager*."""

    async def handler(websocket: WebSocket) -> None:
        await _agent_ws_endpoint(websocket, manager)

    return handler


async def _agent_ws_endpoint(
    websocket: WebSocket,
    manager: AgentConnectionManager,
) -> None:
    """Starlette WebSocket route handler.

    Accepts the connection, then loops on ``receive_text()`` to detect
    client disconnects.  Incoming messages are ignored (clients may
    optionally send ``pong`` replies).
    """
    await manager.connect(websocket)
    try:
        while True:
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


def build_agent_ws_route(
    manager: AgentConnectionManager,
) -> starlette.routing.WebSocketRoute:
    """Return a Starlette ``WebSocketRoute`` that wires *manager* to ``/ws/agents``.

    The returned route is ready to be mounted in a ``Starlette.routes`` list.
    """
    return starlette.routing.WebSocketRoute(
        "/ws/agents",
        _make_agent_ws_handler(manager),
        name="agent_ws",
    )
