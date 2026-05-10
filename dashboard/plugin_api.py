"""chat-upload dashboard plugin v2.0.0.

Mounted by the Hermes dashboard at /api/plugins/chat-upload/.
Local-only personal sidebar chat with disk-backed sessions and uploads.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import uuid
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

try:
    from hermes_constants import get_hermes_home
except Exception:  # test fallback
    def get_hermes_home():  # type: ignore
        return Path.home() / ".hermes"

log = logging.getLogger(__name__)
router = APIRouter()
PLUGIN_VERSION = "2.0.2"
_MAX_FILE_BYTES = int(os.getenv("HERMES_UPLOAD_MAX_BYTES", str(100 * 1024 * 1024)))
_MAX_BULK_FILES = 100
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_LAST_CLEANUP = 0.0
_ACTIVE_AGENT_MAX = int(os.getenv("HERMES_CHAT_UPLOAD_ACTIVE_AGENTS", "16"))
_active_agents: "OrderedDict[str, Any]" = OrderedDict()
_active_agent_profiles: dict[str, Optional[str]] = {}
_session_locks: dict[str, asyncio.Lock] = {}

_TOOL_STATUS_MAP = {
    "web_search": "searching", "web_extract": "searching",
    "terminal": "running", "process": "running", "execute_code": "running",
    "browser_navigate": "browsing", "browser_click": "browsing", "browser_type": "browsing",
    "browser_scroll": "browsing", "browser_snapshot": "browsing", "browser_press": "browsing",
    "browser_back": "browsing", "browser_vision": "browsing", "browser_get_images": "browsing",
    "browser_console": "browsing", "delegate_task": "delegating",
    "write_file": "writing", "patch": "writing", "read_file": "reading", "search_files": "reading",
}

_ALLOWED_EXTS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff"},
    "pdf": {".pdf"},
    "html": {".html", ".htm"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"},
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi"},
    "code": {".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".sql", ".sh"},
    "data": {".json", ".yaml", ".yml", ".csv", ".md", ".txt", ".toml", ".xml"},
    "archive": {".zip", ".tar", ".gz", ".tgz", ".7z"},
}
_EXECUTABLE_EXTS = {".sh", ".bash", ".zsh", ".fish", ".exe", ".bat", ".cmd", ".ps1"}

class BulkRequest(BaseModel):
    paths: list[str]

class SessionSaveRequest(BaseModel):
    title: Optional[str] = None
    messages: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}


def _root() -> Path:
    return Path(get_hermes_home()).expanduser() / "plugins" / "chat-upload"

def _sessions_root() -> Path:
    p = _root() / "sessions"; p.mkdir(parents=True, exist_ok=True); return p

def _uploads_root() -> Path:
    p = _root() / "uploads"; p.mkdir(parents=True, exist_ok=True); return p

def _valid_session_id(session_id: str) -> bool:
    return bool(session_id and _SESSION_RE.match(session_id) and ".." not in session_id and "/" not in session_id and "\\" not in session_id)

def _new_session_id() -> str:
    return str(uuid.uuid4())

def _safe_session_id(session_id: Optional[str]) -> str:
    if not session_id:
        return _new_session_id()
    if not _valid_session_id(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    return session_id

def _sanitize_filename(name: str) -> str:
    base = Path(name or "upload.bin").name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return base[:160] or "upload.bin"

def _file_type(path_or_name: str, content_type: str | None = None) -> str:
    ext = Path(path_or_name).suffix.lower()
    for typ, exts in _ALLOWED_EXTS.items():
        if ext in exts:
            return typ
    if content_type:
        if content_type.startswith("image/"): return "image"
        if content_type.startswith("audio/"): return "audio"
        if content_type.startswith("video/"): return "video"
        if content_type == "application/pdf": return "pdf"
    return "file"

def _unique_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 10000):
        cand = directory / f"{stem}-{i}{suffix}"
        if not cand.exists():
            return cand
    raise HTTPException(status_code=409, detail="could not allocate unique filename")

def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False

def _path_allowed(path_s: str) -> Optional[Path]:
    try:
        p = Path(path_s).expanduser().resolve()
    except Exception:
        return None
    root = _root().resolve()
    return p if _is_inside(p, root) else None

def _session_file(session_id: str) -> Optional[Path]:
    if not _valid_session_id(session_id):
        return None
    return _sessions_root() / f"{session_id}.json"

def _derive_title(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m.get("role") == "user":
            text = str(m.get("text") or m.get("content") or "").strip().replace("\n", " ")
            if text:
                return text[:60]
    return "New chat"

def _load_chat_session(session_id: str) -> Optional[dict[str, Any]]:
    f = _session_file(session_id)
    if not f or not f.exists(): return None
    try:
        return json.loads(f.read_text())
    except Exception:
        log.exception("failed to load chat-upload session %s", session_id)
        return None

def _save_chat_session(session_id: str, profile: Optional[str] = None, history: Optional[list[dict[str, Any]]] = None, *, title: Optional[str] = None, messages: Optional[list[dict[str, Any]]] = None, metadata: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    if not _valid_session_id(session_id): return None
    now = time.time()
    existing = _load_chat_session(session_id) or {}
    msg_list = messages if messages is not None else history if history is not None else existing.get("messages") or existing.get("history") or []
    doc = {
        "session_id": session_id,
        "profile": profile if profile is not None else existing.get("profile"),
        "title": title or existing.get("title") or _derive_title(msg_list),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "messages": msg_list,
        "history": msg_list,  # compatibility with v1.x tests/session files
        "metadata": metadata if metadata is not None else existing.get("metadata", {}),
    }
    f = _session_file(session_id)
    if not f: return None
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    tmp.replace(f)
    return doc

def _list_sessions() -> list[dict[str, Any]]:
    out = []
    for f in _sessions_root().glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        messages = d.get("messages") or d.get("history") or []
        preview = ""
        for m in reversed(messages):
            preview = str(m.get("text") or m.get("content") or "").strip().replace("\n", " ")
            if preview: break
        out.append({
            "session_id": d.get("session_id") or f.stem,
            "title": d.get("title") or _derive_title(messages),
            "preview": preview[:120],
            "created_at": d.get("created_at", f.stat().st_ctime),
            "updated_at": d.get("updated_at", f.stat().st_mtime),
            "message_count": len(messages),
        })
    out.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
    return out

def _tool_label(tool_name: str) -> str:
    if tool_name in _TOOL_STATUS_MAP: return _TOOL_STATUS_MAP[tool_name]
    for prefix, label in (("browser_", "browsing"), ("web_", "searching"), ("terminal", "running"), ("process", "running"), ("write_", "writing"), ("patch", "writing"), ("read_", "reading"), ("search_", "reading")):
        if tool_name.startswith(prefix): return label
    return "working"


def _session_lock(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


def _agent_history_from_chat_upload(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert UI session messages into OpenAI-style AIAgent history."""
    out: list[dict[str, Any]] = []
    for msg in messages or []:
        role = msg.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content", msg.get("text", ""))
        if isinstance(content, list):
            content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        content = str(content or "").strip()
        if content:
            out.append({"role": role, "content": content})
    return out


def _refresh_agent_callbacks(agent: Any, callbacks: dict[str, Callable | None]) -> None:
    for attr, cb in callbacks.items():
        if hasattr(agent, attr):
            setattr(agent, attr, cb)


def _get_active_agent(session_id: str, profile: Optional[str], agent_factory: Callable[..., Any], *, history: list[dict[str, Any]], callbacks: dict[str, Callable | None]) -> Any:
    agent = _active_agents.get(session_id)
    if agent is not None and _active_agent_profiles.get(session_id) != profile:
        _active_agents.pop(session_id, None)
        _active_agent_profiles.pop(session_id, None)
        agent = None
    if agent is None:
        agent = agent_factory(
            session_id=session_id,
            platform="dashboard-plugin:chat-upload",
            tool_start_callback=callbacks.get("tool_start_callback"),
            tool_complete_callback=callbacks.get("tool_complete_callback"),
            stream_delta_callback=callbacks.get("stream_delta_callback"),
            quiet_mode=True,
        )
        if history and not getattr(agent, "_session_messages", None):
            agent._session_messages = list(history)
        _active_agents[session_id] = agent
        _active_agent_profiles[session_id] = profile
    else:
        _active_agents.move_to_end(session_id)
        _refresh_agent_callbacks(agent, callbacks)
    while len(_active_agents) > max(1, _ACTIVE_AGENT_MAX):
        old_sid, old_agent = _active_agents.popitem(last=False)
        _active_agent_profiles.pop(old_sid, None)
        close = getattr(old_agent, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    return agent


def _run_agent_turn(agent: Any, user_text: str) -> str:
    conversation_history = list(getattr(agent, "_session_messages", []) or [])
    if hasattr(agent, "run_conversation"):
        result = agent.run_conversation(user_text, conversation_history=conversation_history)
        if isinstance(result, dict):
            return str(result.get("final_response") or "")
        return str(result or "")
    return str(agent.chat(user_text) or "")

def _check_ws_token(provided: Optional[str], ws: WebSocket | None = None) -> bool:
    # Dashboard commonly runs --insecure; allow loopback/no-token in local test/dev.
    if ws is not None:
        host = getattr(ws.client, "host", "") if ws.client else ""
        if host in {"127.0.0.1", "::1", "localhost"}: return True
    try:
        from hermes_cli import web_server as _ws
        expected = getattr(_ws, "_SESSION_TOKEN", None)
    except Exception:
        expected = None
    if not expected: return True
    return bool(provided) and hmac.compare_digest(str(provided), str(expected))

def _cleanup_old_uploads() -> None:
    global _LAST_CLEANUP
    now = time.time()
    if now - _LAST_CLEANUP < 86400: return
    _LAST_CLEANUP = now
    try:
        days = int(os.getenv("HERMES_UPLOAD_RETENTION_DAYS", "30"))
        cutoff = now - days * 86400
        for child in _uploads_root().iterdir():
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
            except Exception:
                pass
    except Exception:
        log.exception("upload cleanup failed")

class ChatSession:
    def __init__(self, ws_id: str, session_id: str, profile: Optional[str] = None):
        self.ws_id = ws_id; self.session_id = session_id; self.profile = profile; self.history: list[dict[str, Any]] = []
    def load_history(self, history: list[dict[str, Any]]) -> None:
        self.history = history or []
    def get_displayable_history(self) -> list[dict[str, str]]:
        out = []
        for m in self.history:
            if m.get("role") not in {"user", "assistant"}: continue
            content = m.get("text", m.get("content", ""))
            if isinstance(content, list):
                content = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
            content = str(content).strip()
            if content: out.append({"role": m.get("role"), "content": content})
        return out

@router.get("/health")
async def health():
    return {"plugin": "chat-upload", "version": PLUGIN_VERSION, "ok": True}

@router.get("/profiles")
async def profiles():
    prof_root = Path(get_hermes_home()).expanduser() / "profiles"
    names = []
    if prof_root.exists():
        names = sorted([p.name for p in prof_root.iterdir() if p.is_dir()])
    return {"profiles": names, "default": os.getenv("HERMES_PROFILE") or "default"}

@router.get("/sessions")
async def sessions_list():
    return {"sessions": _list_sessions()}

@router.post("/sessions")
async def sessions_create(req: SessionSaveRequest):
    sid = _new_session_id()
    doc = _save_chat_session(sid, messages=req.messages, title=req.title, metadata=req.metadata) or {}
    return doc

@router.get("/sessions/{session_id}")
async def sessions_get(session_id: str):
    doc = _load_chat_session(session_id)
    if not doc:
        raise HTTPException(status_code=404, detail="session not found")
    return doc

@router.put("/sessions/{session_id}")
async def sessions_put(session_id: str, req: SessionSaveRequest):
    if not _valid_session_id(session_id): raise HTTPException(status_code=400, detail="invalid session_id")
    doc = _save_chat_session(session_id, messages=req.messages, title=req.title, metadata=req.metadata)
    return doc or {"ok": False}

@router.delete("/sessions/{session_id}")
async def sessions_delete(session_id: str):
    if not _valid_session_id(session_id): raise HTTPException(status_code=400, detail="invalid session_id")
    f = _session_file(session_id)
    if f and f.exists(): f.unlink()
    up = _uploads_root() / session_id
    if up.exists() and _is_inside(up, _uploads_root()): shutil.rmtree(up)
    return {"ok": True}

@router.post("/upload")
async def upload(file: UploadFile = File(...), session_id: str = Form(...)):
    _cleanup_old_uploads()
    sid = _safe_session_id(session_id)
    raw = await file.read()
    if len(raw) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    filename = _sanitize_filename(file.filename or "upload.bin")
    typ = _file_type(filename, file.content_type)
    dest_dir = _uploads_root() / sid
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_path(dest_dir, filename)
    dest.write_bytes(raw)
    warning = None
    if dest.suffix.lower() in _EXECUTABLE_EXTS:
        warning = "Potentially executable file uploaded; inspect before running."
    return {"ok": True, "session_id": sid, "filename": dest.name, "original_filename": file.filename, "path": str(dest), "url": f"/api/plugins/chat-upload/file?path={dest}", "type": typ, "content_type": file.content_type, "size": len(raw), "warning": warning}

@router.get("/resolve")
async def resolve(path: str):
    p = _path_allowed(path)
    if not p or not p.exists() or not p.is_file():
        return {"exists": False, "path": path}
    return {"exists": True, "path": str(p), "filename": p.name, "type": _file_type(p.name), "size": p.stat().st_size, "url": f"/api/plugins/chat-upload/file?path={p}"}

@router.get("/file")
async def serve_file(path: str, inline: bool = False):
    p = _path_allowed(path)
    if not p or not p.exists() or not p.is_file():
        raise HTTPException(status_code=403, detail="file not allowed")
    media_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
    disposition = "inline" if inline else "attachment"
    return FileResponse(str(p), media_type=media_type, filename=p.name, headers={"Content-Disposition": f'{disposition}; filename="{p.name}"'})

@router.post("/bulk-download")
async def bulk_download(req: BulkRequest):
    if len(req.paths) > _MAX_BULK_FILES: raise HTTPException(status_code=400, detail="too many files")
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path_s in req.paths:
            p = _path_allowed(path_s)
            if p and p.exists() and p.is_file():
                zf.write(p, arcname=p.name); count += 1
    if count == 0: raise HTTPException(status_code=404, detail="no valid files")
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="chat-upload-files.zip"'})

@router.delete("/uploads/{session_id}")
async def clear_uploads(session_id: str):
    if not _valid_session_id(session_id): raise HTTPException(status_code=400, detail="invalid session_id")
    up = _uploads_root() / session_id
    if up.exists() and _is_inside(up, _uploads_root()): shutil.rmtree(up)
    return {"ok": True}

@router.websocket("/stream")
async def stream_ws(ws: WebSocket) -> None:
    token = ws.query_params.get("token", "")
    if not _check_ws_token(token, ws):
        await ws.close(code=4401); return
    await ws.accept()
    loop = asyncio.get_event_loop()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=60)
        msg = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect) as exc:
        await ws.send_text(json.dumps({"type": "error", "text": f"Bad handshake: {exc}"})); await ws.close(); return
    if msg.get("type") != "message" or not msg.get("text"):
        await ws.send_text(json.dumps({"type": "error", "text": "Expected {type:message, text:...}"})); await ws.close(); return
    user_text = str(msg["text"])
    session_id = _safe_session_id(msg.get("session_id"))
    profile = msg.get("profile")
    attachments = msg.get("attachments") or []
    await ws.send_text(json.dumps({"type": "session", "session_id": session_id}))
    await ws.send_text(json.dumps({"type": "status", "label": "thinking"}))
    q: asyncio.Queue = asyncio.Queue(); result_holder: list[str] = []; error_holder: list[str] = []
    def _push(frame: dict[str, Any]):
        fut = asyncio.run_coroutine_threadsafe(q.put(frame), loop)
        fut.result(timeout=1)
    def _on_tool_start(tool_call_id: str, name: str, args: dict):
        if tool_call_id and not name.startswith("_"): _push({"type": "status", "label": _tool_label(name)})
    def _on_tool_complete(tool_call_id: str, name: str, args: dict, result):
        if tool_call_id and not name.startswith("_"): _push({"type": "status", "label": "thinking"})
    def _on_delta(delta: str):
        _push({"type": "status", "label": "responding"}); _push({"type": "delta", "text": delta})

    async with _session_lock(session_id):
        prior = _load_chat_session(session_id) or {"messages": []}
        history = list(prior.get("messages") or prior.get("history") or [])
        agent_history = _agent_history_from_chat_upload(history)
        history.append({"role": "user", "text": user_text, "content": user_text, "timestamp": time.time(), "attachments": attachments})
        _save_chat_session(session_id, profile=profile, messages=history)

        def _run_agent():
            try:
                ha_path = str(Path(get_hermes_home()).expanduser() / "hermes-agent")
                if ha_path not in sys.path: sys.path.insert(0, ha_path)
                from run_agent import AIAgent
                from hermes_cli.config import cfg_get, load_config
                cfg = load_config(); model = cfg_get(cfg, "model", "default", default=""); provider = cfg_get(cfg, "model", "provider", default=None)
                def _agent_factory(**kwargs):
                    return AIAgent(model=model, provider=provider, **kwargs)
                agent = _get_active_agent(
                    session_id,
                    profile,
                    _agent_factory,
                    history=agent_history,
                    callbacks={
                        "tool_start_callback": _on_tool_start,
                        "tool_complete_callback": _on_tool_complete,
                        "stream_delta_callback": _on_delta,
                    },
                )
                response = _run_agent_turn(agent, user_text)
                result_holder.append(response or "")
            except Exception as exc:
                log.exception("chat-upload agent error"); error_holder.append(str(exc))
        thread = threading.Thread(target=_run_agent, daemon=True); thread.start()
        full_parts: list[str] = []
        try:
            while thread.is_alive() or not q.empty():
                try: frame = await asyncio.wait_for(q.get(), timeout=0.1)
                except asyncio.TimeoutError: continue
                if frame.get("type") == "delta": full_parts.append(frame.get("text", ""))
                await ws.send_text(json.dumps(frame))
        except WebSocketDisconnect:
            return
        thread.join(timeout=5)
        if error_holder:
            await ws.send_text(json.dumps({"type": "error", "text": error_holder[0]}))
        else:
            full = result_holder[0] if result_holder else "".join(full_parts)
            history.append({"role": "assistant", "text": full, "content": full, "timestamp": time.time(), "attachments": []})
            doc = _save_chat_session(session_id, profile=profile, messages=history, title=prior.get("title")) or {}
            await ws.send_text(json.dumps({"type": "done", "text": full, "session_id": session_id, "session": {"title": doc.get("title"), "updated_at": doc.get("updated_at")}}))
            await ws.send_text(json.dumps({"type": "clear"}))
    await ws.close()
