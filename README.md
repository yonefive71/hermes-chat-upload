# hermes-chat-upload

Reference dashboard plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — a full-featured in-browser chat with file uploads, inline image rendering, persistent sessions, and live tool-status indicators.

![Hermes chat plugin — black hole generation example](docs/chat-overview.png)

## What this plugin adds

When installed, the Hermes Agent dashboard gets a new **Chat** tab that lets you talk to your Hermes agent directly from the browser, with all of the agent's tools (search, image generation, code execution, file analysis, etc.) wired in.

It is intended as a reference implementation: a small, readable codebase that demonstrates how to write a Hermes dashboard plugin (FastAPI backend routes + React IIFE frontend using the `__HERMES_PLUGIN_SDK__`).

## Features

### Conversation
- **Streaming responses** — agent tokens stream in over WebSocket as they are produced
- **Live tool-status indicator** — `thinking…`, `searching…`, `browsing…`, `running code…`, `generating image…` based on what the agent is currently doing
- **Markdown rendering** — GFM markdown including tables, fenced code blocks, and lists
- **HTML sanitization** — markdown is sanitized via DOMPurify before render; links open in a new tab with `rel="noopener noreferrer"`
- **Inline image rendering** — agent-emitted images via markdown `![alt](url)` or `MEDIA:/path` render directly in the chat bubble

![Full dashboard view — Hermes agent orchestrating a multi-agent workflow, with session sidebar showing chat history](docs/chat-screenshot-2.png)

### Uploads
- **Drag-and-drop** anywhere in the chat pane
- **File-picker** via the Attach button
- **Paste-from-clipboard** — screenshots and copied files paste directly into the input
- **Per-type chips** for PDFs, HTML, code, and other binaries with the original filename preserved
- **Bulk download as ZIP** when the agent produces 3+ artifacts in a turn
- **Executable warning** when an upload has an executable extension (.sh, .exe, .ps1, etc.)
- Configurable per-file size cap (default 100 MB, override via `HERMES_UPLOAD_MAX_BYTES`)

### Sessions
- **Persistent sessions** — chats survive tab close, browser restart, and dashboard restart
- **Sidebar session list** — every chat is browsable from a left-rail history
- **New chat** button for clean starts; per-session delete
- **Filename-safe IDs** — session IDs are sandboxed to `~/.hermes/uploads/<session-id>/`

### Profile & agent control
- **Profile switcher** — pick which Hermes profile (model + system prompt + tool set) handles the conversation
- **Per-turn agent invocation** — each turn runs `AIAgent.run_conversation()` so model/profile changes take effect immediately

### Security & integration
- **Session-token auth** — every plugin endpoint requires `X-Hermes-Session-Token`, matching the dashboard's auth middleware (see [README in source](https://github.com/yonefive71/hermes-chat-upload) for the plugin auth contract)
- **Path sandboxing** — uploads and downloads are restricted to `~/.hermes/uploads/`
- **Auto-cleanup** of old session directories
- **Mobile-responsive** layout (iPad-tested)

## Requirements

- Hermes Agent v0.13.0+
- Python 3.11+
- Linux or macOS host (Windows untested)

## Install

```bash
git clone https://github.com/yonefive71/hermes-chat-upload ~/.hermes/plugins/chat-upload
sudo systemctl restart hermes-dashboard
```

If you run the dashboard via a different launcher, just restart it after cloning. The dashboard auto-discovers plugins in `~/.hermes/plugins/`.

Verify the plugin is loaded:

```bash
curl -s http://localhost:9119/api/dashboard/plugins | python3 -m json.tool | grep chat-upload
```

## Usage

1. Open the Hermes dashboard and click the **Chat** tab in the left sidebar.
2. Type a message, drag a file onto the chat pane, use the **Attach** button, or paste a screenshot.
3. The agent receives the file path (and a short description of its type) as part of your message.
4. Watch the streaming response — tool calls appear as ephemeral status indicators (`thinking…`, `searching…`, etc.) until the final text/image arrives.
5. Images emitted by the agent render inline; downloadable files appear as chips. With 3+ files, a **Download all (ZIP)** button appears.
6. Switch profiles to change which agent persona (and toolset) handles the conversation.
7. Sessions persist across tab reloads and dashboard restarts — pick any past chat from the sidebar to resume.

## Limitations

- **Single-user** — no per-user session isolation if the dashboard is shared. Designed for personal use.
- **Not security-hardened** — designed for localhost/Tailscale personal use, not open-internet exposure.
- **Local filesystem storage only** — `~/.hermes/uploads/`, no S3 or remote storage.
- **In-process agent** — one `AIAgent` per WebSocket connection; fine for personal use, would need pooling for team scale.
- **Tested on** Linux x86_64 with Hermes v0.13.0.

## Architecture

- **Backend:** FastAPI `plugin_api.py` with REST endpoints for sessions/uploads/files + a WebSocket `/stream` endpoint that runs `AIAgent.run_conversation()` per turn and pushes streaming frames.
- **Frontend:** Vanilla IIFE React bundle (no JSX runtime, no module loader) that registers itself via `window.__HERMES_PLUGINS__.register()`. Uses `__HERMES_PLUGIN_SDK__` for React, hooks, and shadcn UI primitives provided by the host dashboard.
- **Auth:** Every `/api/*` plugin call carries `X-Hermes-Session-Token` from `window.__HERMES_SESSION_TOKEN__`; the WebSocket carries the same token as a query param.
- **Persistence:** Sessions are written to disk after each agent turn and reloaded on reconnect.

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for expectations and scope. See [NON_GOALS.md](NON_GOALS.md) before requesting a new feature.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Developed using the Hermes Agent Kanban workflow.
