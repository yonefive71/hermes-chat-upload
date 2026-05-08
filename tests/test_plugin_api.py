"""Tests for chat-upload plugin backend (plugin_api.py).

Covers:
- Health endpoint returns v1.4.0
- Tool label mapping covers all documented vocabulary entries
- Status frame construction (smoke test for _tool_label)
- WebSocket auth rejection on bad token
- WebSocket closes cleanly on malformed handshake message
"""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Load the plugin module without depending on the live hermes stack.
# We patch the imports it needs before loading.
# ---------------------------------------------------------------------------

PLUGIN_DIR = Path(__file__).parent.parent / "dashboard"
API_FILE = PLUGIN_DIR / "plugin_api.py"


def _load_plugin_api():
    """Import plugin_api.py isolated from the real hermes-agent environment."""
    # Stub out hermes imports that aren't present in test context.
    for mod_name in (
        "run_agent",
        "hermes_cli",
        "hermes_cli.config",
        "hermes_cli.web_server",
    ):
        if mod_name not in sys.modules:
            stub = types.ModuleType(mod_name)
            if mod_name == "hermes_cli.config":
                stub.load_config = lambda: {}
                stub.cfg_get = lambda cfg, *a, default=None, **kw: default
            if mod_name == "hermes_cli.web_server":
                stub._SESSION_TOKEN = "test-token-abc123"
            sys.modules[mod_name] = stub

    spec = importlib.util.spec_from_file_location("plugin_api_under_test", API_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_api = _load_plugin_api()

# Build a minimal FastAPI app with the plugin router for testing.
from fastapi import FastAPI

test_app = FastAPI()
test_app.include_router(_api.router)
client = TestClient(test_app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_version_140(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "1.4.0"
        assert data["ok"] is True
        assert data["plugin"] == "chat-upload"


# ---------------------------------------------------------------------------
# Tool label mapping
# ---------------------------------------------------------------------------

class TestToolLabelMapping:
    """The status vocabulary from the task spec — every row must resolve."""

    CASES = [
        # (tool_name, expected_label)
        ("web_search",       "searching"),
        ("web_extract",      "searching"),
        ("terminal",         "running"),
        ("process",          "running"),
        ("browser_navigate", "browsing"),
        ("browser_click",    "browsing"),
        ("browser_type",     "browsing"),
        ("browser_scroll",   "browsing"),
        ("browser_snapshot", "browsing"),
        ("browser_back",     "browsing"),
        ("browser_vision",   "browsing"),
        ("delegate_task",    "delegating"),
        ("write_file",       "writing"),
        ("patch",            "writing"),
        ("read_file",        "reading"),
        ("search_files",     "reading"),
        ("unknown_tool_xyz", "working"),
    ]

    @pytest.mark.parametrize("tool_name,expected", CASES)
    def test_label(self, tool_name, expected):
        got = _api._tool_label(tool_name)
        assert got == expected, f"_tool_label({tool_name!r}) = {got!r}, want {expected!r}"


# ---------------------------------------------------------------------------
# WebSocket: bad token → close 4401
# ---------------------------------------------------------------------------

class TestWsAuth:
    def test_bad_token_rejected(self):
        # Server closes immediately with code 4401 — TestClient raises on connect.
        from starlette.websockets import WebSocketDisconnect as _WSDc
        with pytest.raises(_WSDc) as exc_info:
            with client.websocket_connect("/stream?token=wrong"):
                pass
        assert exc_info.value.code == 4401

    def test_good_token_accepted_then_bad_payload(self):
        """Connecting with valid token succeeds; sending garbage closes cleanly."""
        token = sys.modules["hermes_cli.web_server"]._SESSION_TOKEN
        with client.websocket_connect(f"/stream?token={token}") as ws:
            ws.send_text(json.dumps({"type": "notamessage"}))
            # Server should send error frame and close.
            data = ws.receive_text()
            frame = json.loads(data)
            assert frame["type"] == "error"
