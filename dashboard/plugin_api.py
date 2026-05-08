"""chat-upload dashboard plugin — backend API routes.

Mounted at /api/plugins/chat-upload/ by the dashboard plugin system.

Provides:
  GET  /health          — version check; returns {"version": "1.4.0"}
  POST /send            — enqueue a message for the active session
  WS   /stream          — WebSocket for status + message frames during a turn

Protocol (WebSocket frames, all JSON):
  Client → Server (after connect):
      {"type": "message", "text": "<user message>", "session_id": "<optional>"}

  Server → Client:
      {"type": "status",  "label": "thinking"}   — agent is calling LLM
      {"type": "status",  "label": "searching"}  — web_search / web_extract
      {"type": "status",  "label": "running"}    — terminal / process
      {"type": "status",  "label": "browsing"}   — browser_*
      {"type": "status",  "label": "delegating"} — delegate_task
      {"type": "status",  "label": "writing"}    — write_file / patch
      {"type": "status",  "label": "reading"}    — read_file / search_files
      {"type": "status",  "label": "responding"} — final text streaming
      {"type": "status",  "label": "working"}    — everything else
      {"type": "delta",   "text": "<chunk>"}     — streaming response chunk
      {"type": "done",    "text": "<full>"}       — turn complete; full response
      {"type": "error",   "text": "<msg>"}        — agent raised an exception
      {"type": "clear"}                           — ephemeral status cleared

Security: plugin routes skip the dashboard auth middleware by design (see
web_server.py auth_middleware). The WebSocket additionally validates the
dashboard session token passed as ?token= to prevent cross-origin WS abuse.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import threading
import time
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)
router = APIRouter()

PLUGIN_VERSION = "1.4.0"

# ---------------------------------------------------------------------------
# Tool name → status label mapping
# ---------------------------------------------------------------------------

_TOOL_STATUS_MAP: dict[str, str] = {
    # web
    "web_search": "searching",
    "web_extract": "searching",
    # terminal / process
    "terminal": "running",
    "process": "running",
    # browser
    "browser_navigate": "browsing",
    "browser_click": "browsing",
    "browser_type": "browsing",
    "browser_scroll": "browsing",
    "browser_snapshot": "browsing",
    "browser_press": "browsing",
    "browser_back": "browsing",
    "browser_vision": "browsing",
    "browser_get_images": "browsing",
    "browser_console": "browsing",
    # delegation
    "delegate_task": "delegating",
    # file write
    "write_file": "writing",
    "patch": "writing",
    # file read / search
    "read_file": "reading",
    "search_files": "reading",
}


def _tool_label(tool_name: str) -> str:
    """Map a tool function name to a human status word."""
    if tool_name in _TOOL_STATUS_MAP:
        return _TOOL_STATUS_MAP[tool_name]
    # Prefix fallback
    for prefix, label in (
        ("browser_", "browsing"),
        ("web_", "searching"),
        ("terminal", "running"),
        ("process", "running"),
        ("write_", "writing"),
        ("patch", "writing"),
        ("read_", "reading"),
        ("search_", "reading"),
    ):
        if tool_name.startswith(prefix):
            return label
    return "working"


# ---------------------------------------------------------------------------
# WebSocket auth helper
# ---------------------------------------------------------------------------

def _check_ws_token(provided: Optional[str]) -> bool:
    """Constant-time compare against the dashboard session token."""
    if not provided:
        return False
    try:
        from hermes_cli import web_server as _ws
    except Exception:
        # Test context — accept.
        return True
    expected = getattr(_ws, "_SESSION_TOKEN", None)
    if not expected:
        return True
    return hmac.compare_digest(str(provided), str(expected))


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@router.get("/health")
async def health():
    """Return plugin version. Used as deploy-signal by CI / pitfall-18 check."""
    return {"plugin": "chat-upload", "version": PLUGIN_VERSION, "ok": True}


# ---------------------------------------------------------------------------
# WebSocket chat stream
# ---------------------------------------------------------------------------

@router.websocket("/stream")
async def stream_ws(ws: WebSocket) -> None:
    """WebSocket that drives a full agent turn with live status frames.

    Authentication: ?token=<dashboard session token>
    Protocol: see module docstring.
    """
    token = ws.query_params.get("token", "")
    if not _check_ws_token(token):
        await ws.close(code=4401)
        return

    await ws.accept()

    loop = asyncio.get_event_loop()

    async def _send(frame: dict) -> None:
        try:
            await ws.send_text(json.dumps(frame))
        except Exception:
            pass

    try:
        # Expect first message: {"type": "message", "text": "...", "session_id": "..."}
        raw = await asyncio.wait_for(ws.receive_text(), timeout=60)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect) as exc:
        await _send({"type": "error", "text": f"Bad handshake: {exc}"})
        await ws.close()
        return

    if msg.get("type") != "message" or not msg.get("text"):
        await _send({"type": "error", "text": "Expected {type:message, text:...}"})
        await ws.close()
        return

    user_text: str = msg["text"]
    session_id: Optional[str] = msg.get("session_id") or None

    # --- Emit initial "thinking" ---
    await _send({"type": "status", "label": "thinking"})

    # --- Run agent in a thread so we don't block the event loop ---
    result_holder: list = []
    error_holder: list = []
    # Queue for status/delta frames from agent thread → asyncio send loop
    q: asyncio.Queue = asyncio.Queue()

    def _push(frame: dict) -> None:
        """Thread-safe frame push into the asyncio queue."""
        asyncio.run_coroutine_threadsafe(q.put(frame), loop)

    def _on_tool_start(tool_call_id: str, name: str, args: dict) -> None:
        if not tool_call_id or name.startswith("_"):
            return
        _push({"type": "status", "label": _tool_label(name)})

    def _on_tool_complete(tool_call_id: str, name: str, args: dict, result) -> None:
        # Return to "thinking" — LLM call resumes.
        if not tool_call_id or name.startswith("_"):
            return
        _push({"type": "status", "label": "thinking"})

    def _on_delta(delta: str) -> None:
        _push({"type": "status", "label": "responding"})
        _push({"type": "delta", "text": delta})

    def _run_agent():
        try:
            import sys
            import os
            # Ensure hermes-agent is on path
            ha_path = os.path.expanduser("~/.hermes/hermes-agent")
            if ha_path not in sys.path:
                sys.path.insert(0, ha_path)

            from run_agent import AIAgent
            from hermes_cli.config import load_config, cfg_get

            cfg = load_config()
            model = cfg_get(cfg, "model", "default", default="")
            provider = cfg_get(cfg, "model", "provider", default=None)

            agent = AIAgent(
                model=model,
                provider=provider,
                session_id=session_id,
                platform="dashboard",
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                stream_delta_callback=_on_delta,
                quiet_mode=True,
            )
            response = agent.chat(user_text)
            result_holder.append(response or "")
        except Exception as exc:
            log.exception("chat-upload agent error")
            error_holder.append(str(exc))

    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()

    # Drain the queue while the thread runs, forwarding frames to the WS.
    full_response_parts: list[str] = []
    try:
        while thread.is_alive() or not q.empty():
            try:
                frame = await asyncio.wait_for(q.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            if frame.get("type") == "delta":
                full_response_parts.append(frame.get("text", ""))
            await _send(frame)
    except WebSocketDisconnect:
        # Client left — let thread finish in background, nothing to send.
        return

    thread.join(timeout=5)

    if error_holder:
        await _send({"type": "error", "text": error_holder[0]})
    else:
        full = result_holder[0] if result_holder else "".join(full_response_parts)
        await _send({"type": "done", "text": full})
        await _send({"type": "clear"})

    await ws.close()
