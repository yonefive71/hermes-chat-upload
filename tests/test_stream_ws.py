import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "dashboard" / "plugin_api.py"

STUB_RESPONSE = "stubbed websocket response"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    shim = types.ModuleType("hermes_constants")
    shim.get_hermes_home = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "hermes_constants", shim)

    spec = importlib.util.spec_from_file_location("plugin_api_stream_under_test", API)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["plugin_api_stream_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def client(api, monkeypatch):
    # Starlette TestClient reports ws.client.host as "testclient" instead of loopback.
    monkeypatch.setattr(api, "_check_ws_token", lambda provided, ws=None: True)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


@pytest.fixture()
def stub_agent_modules(monkeypatch):
    created = []

    class StubAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._session_messages = []
            created.append(self)

        def run_conversation(self, message, conversation_history=None):
            self.kwargs["tool_start_callback"]("tool-1", "web_search", {"query": message})
            self.kwargs["stream_delta_callback"]("stubbed ")
            self.kwargs["tool_complete_callback"]("tool-1", "web_search", {"query": message}, {"ok": True})
            self.kwargs["stream_delta_callback"]("websocket response")
            self._session_messages = list(conversation_history or []) + [
                {"role": "user", "content": message},
                {"role": "assistant", "content": STUB_RESPONSE},
            ]
            return {"final_response": STUB_RESPONSE}

    run_agent = types.ModuleType("run_agent")
    run_agent.AIAgent = StubAgent
    monkeypatch.setitem(sys.modules, "run_agent", run_agent)

    hermes_cli = types.ModuleType("hermes_cli")
    hermes_config = types.ModuleType("hermes_cli.config")
    hermes_config.load_config = lambda: {}
    hermes_config.cfg_get = lambda cfg, section, key, default=None: default
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", hermes_config)
    return created


def _recv_until_closed(ws):
    frames = []
    while True:
        try:
            frames.append(json.loads(ws.receive_text()))
        except WebSocketDisconnect:
            return frames


def test_stream_websocket_happy_path_emits_frames_and_persists_session(client, stub_agent_modules):
    with client.websocket_connect("/stream") as ws:
        ws.send_text(json.dumps({"type": "message", "text": "hello websocket", "session_id": "ws-happy", "profile": "default"}))
        frames = _recv_until_closed(ws)

    assert [frame["type"] for frame in frames[:2]] == ["session", "status"]
    assert frames[0]["session_id"] == "ws-happy"
    assert frames[1] == {"type": "status", "label": "thinking"}
    assert frames[-2]["type"] == "done"
    assert frames[-2]["text"] == STUB_RESPONSE
    assert frames[-1] == {"type": "clear"}

    middle = frames[2:-2]
    assert any(frame == {"type": "status", "label": "searching"} for frame in middle), frames
    assert any(frame["type"] == "delta" and frame["text"] for frame in middle)
    assert len(stub_agent_modules) == 1

    saved = client.get("/sessions/ws-happy")
    assert saved.status_code == 200
    messages = saved.json()["messages"]
    assert [(msg["role"], msg["text"]) for msg in messages] == [
        ("user", "hello websocket"),
        ("assistant", STUB_RESPONSE),
    ]


def test_stream_websocket_agent_error_frame_and_clean_close(client, monkeypatch):
    class RaisingAgent:
        def __init__(self, **kwargs):
            pass

        def run_conversation(self, message, conversation_history=None):
            raise RuntimeError("stub boom")

    run_agent = types.ModuleType("run_agent")
    run_agent.AIAgent = RaisingAgent
    monkeypatch.setitem(sys.modules, "run_agent", run_agent)

    hermes_cli = types.ModuleType("hermes_cli")
    hermes_config = types.ModuleType("hermes_cli.config")
    hermes_config.load_config = lambda: {}
    hermes_config.cfg_get = lambda cfg, section, key, default=None: default
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", hermes_config)

    with client.websocket_connect("/stream") as ws:
        ws.send_text(json.dumps({"type": "message", "text": "explode", "session_id": "ws-error"}))
        frames = _recv_until_closed(ws)

    assert any(frame["type"] == "error" and "stub boom" in frame["text"] for frame in frames)


def test_stream_websocket_bad_handshake_errors_and_does_not_spawn_agent(client, monkeypatch):
    created = []

    class ShouldNotSpawnAgent:
        def __init__(self, **kwargs):
            created.append(kwargs)
            raise AssertionError("agent should not be spawned for bad handshake")

    run_agent = types.ModuleType("run_agent")
    run_agent.AIAgent = ShouldNotSpawnAgent
    monkeypatch.setitem(sys.modules, "run_agent", run_agent)

    with client.websocket_connect("/stream") as ws:
        ws.send_text(json.dumps({"type": "nope"}))
        frames = _recv_until_closed(ws)

    assert frames == [{"type": "error", "text": "Expected {type:message, text:...}"}]
    assert created == []
