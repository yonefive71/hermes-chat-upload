# hermes-chat-upload

Reference dashboard plugin for Hermes Agent — chat with file upload/download, frontend-aware persona, session persistence, tool-aware status indicator.

A plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that adds a full-featured chat interface to the dashboard. See also the [hermes-dashboard-plugin skill](https://github.com/NousResearch/hermes-agent) for building your own plugins.

![demo](docs/demo.gif)

## Features

- Drag-drop, file-picker, and paste-from-clipboard upload
- Inline rendering of agent-emitted images (markdown `![alt](url)` → `<img>`)
- Per-type preview chips for PDFs, HTML, code files, and other binaries
- Bulk download as zip when 2+ artifacts are present
- Profile switcher (select which Hermes profile the agent runs as)
- Session persistence across tab close and reload
- Tool-aware ephemeral status indicator (thinking… / searching… / browsing… / etc.)
- Mobile-responsive layout (iPad-tested)

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

## Usage

1. Open the Hermes dashboard and navigate to the **Chat** tab.
2. Drag a file onto the drop zone, use the file picker button, or paste a screenshot from the clipboard.
3. The agent receives the file path (and a short description of its type) as part of your message.
4. Images emitted by the agent via markdown `![alt](url)` or `MEDIA:/path` syntax render inline in the chat.
5. When the agent produces 2+ downloadable files, a **Download all (zip)** button appears.
6. Use the profile switcher to select which Hermes profile (agent persona) handles the conversation.
7. Sessions persist across tab reloads — your conversation continues where you left off.

## Limitations

- **Single-user:** no per-user session isolation if the dashboard is shared. Designed for personal use.
- **Not security-hardened:** designed for Tailscale-localhost personal use, not open-internet exposure.
- **Local filesystem storage only:** `~/.hermes/uploads/` only, no S3 or remote storage.
- **In-process agent:** one AIAgent per WebSocket connection; fine for personal use, will need pooling for team-scale.
- **Tested on:** Linux x86_64 with Hermes v0.13.0.

## Architecture

FastAPI `plugin_api.py` for backend routes; vanilla IIFE React frontend bundle using `window.__HERMES_PLUGIN_SDK__` from the dashboard host; per-turn `AIAgent.run_conversation()` calls in a thread pool. Sessions are persisted to disk after each agent turn and reloaded on reconnect.

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for expectations and scope. See [NON_GOALS.md](NON_GOALS.md) before requesting a new feature.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

Built for [Hermes Agent](https://github.com/NousResearch/hermes-agent). Developed using the Hermes Agent Kanban workflow.
