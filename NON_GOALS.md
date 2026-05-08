# Non-goals

Things this plugin won't do, with reasoning, so you don't have to ask. If you need any of these, fork it.

## Multi-tenant / multi-user
The plugin assumes one user on one dashboard. No per-user session isolation, no auth beyond the dashboard's existing auth model (Tailscale-localhost). Adding multi-tenant support would require auth middleware, per-user storage paths, and a real threat model — all of which is product-tier work and out of scope for personal tooling.

## Cross-device session sync
Sessions persist locally to the host. There is no server-side session store, no sync API, no "pick up on my phone where I left off on my laptop" feature. localStorage on the browser plus filesystem on the host is the whole storage model.

## Multi-agent group chat / agent-to-agent messaging
The plugin chats with one agent at a time (the active profile). Multiple-profile-panes (talking to several agents in split panes) and agent-to-agent messaging are explicitly out of scope. If you want agents to talk to each other, use Hermes Kanban or the Redis agent-messaging system — both are designed for that.

## Team features
No shared sessions, no comments on others' chats, no permissions, no audit log, no admin panel. Single-user.

## Hosted SaaS
This plugin is shipped as source. There is no hosted version, no api.example.com, no plans for one.

## Replacing the built-in Chat tab
Hermes ships a built-in Chat tab gated by `--tui`. This plugin coexists; it does not aim to replace or supersede the built-in. If the upstream Hermes Chat tab adds features this plugin has, that's fine — fork either, or use both.

## Unbounded scope creep
Issues and PRs proposing "while you're at it, can we also add X" will be closed if X isn't on the roadmap. Roadmap is intentionally tight: bug fixes, mobile polish, file-type previews. Substantial new directions will fork the project, not bloat this one.
