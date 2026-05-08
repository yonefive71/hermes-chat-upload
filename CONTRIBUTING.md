# Contributing

This is personal tooling shared as-is under MIT. PRs and issues welcome — but please read the expectations below before opening one.

## Response times
- Bug fixes (small, surgical): ~1-2 weeks
- Feature PRs (anything new): ~2-6 weeks, may not be merged at all
- Bug reports without PRs: triaged when I have time, no SLA

If you need something fixed faster, fork the repo and ship from your fork. MIT lets you do that and ship modified versions in your own products.

## Before opening a PR
- For substantial features (>50 lines, new UI surfaces, new dependencies): **open an issue first** to discuss scope. PRs that arrive without a prior conversation may be closed without merge if they don't fit the project's philosophy.
- For small fixes (typos, single-bug patches, dependency bumps): just open the PR.
- Run any existing tests; add new ones for behavior changes.

## Project philosophy
- **Personal tooling, not a product.** Designed for one user on their own dashboard, not a multi-tenant SaaS.
- **Tailscale-localhost threat model.** Not security-hardened for open-internet exposure. PRs that add features assuming a hostile network won't be merged unless they keep the simple-deployment path intact.
- **Boring tech.** Vanilla IIFE React, FastAPI, local FS storage. PRs adding new build tooling, frameworks, or services will be evaluated harshly.
- **Hermes-native.** Uses the dashboard plugin SDK and the in-process AIAgent. Don't try to make this work outside Hermes.

## What I'm unlikely to merge
See NON_GOALS.md.

## What I might merge
- Bug fixes for behavior the README claims works
- Mobile/responsive tweaks (iPad and phone layouts)
- New file-type previews (more of the existing pattern)
- Better keyboard navigation
- Performance improvements that don't add deps
- Accessibility fixes
