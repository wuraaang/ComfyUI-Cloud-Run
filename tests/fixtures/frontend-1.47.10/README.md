# ComfyUI frontend 1.47.10 extension API fixture

This directory is an API-contract fixture for the pinned ComfyUI frontend
version. It is not a copy, bundle, or reconstruction of
`comfyui-frontend-package`.

`extension-api.mjs` exposes only the public host surfaces exercised by the
locked Agent Panel, Hermes Nous, and Efficiency entrypoints: extension and
sidebar registration, native prompt queuing, frontend events, settings,
graph/canvas access, and a fail-closed same-origin backend adapter. Tests use
the DOM primitives from `tests/js/fake-dom.mjs`.

The compatibility harness reads the real audited entrypoints, verifies every
copied locked byte against `cloud_run/certified_baseline.lock.json`, and maps
their browser `/scripts` imports to this fixture inside a temporary offline
module tree. Set `COMFYUI_CERTIFIED_CUSTOM_NODES` when the audited
`custom_nodes` directory is not under the repository owner's standard
`ComfyUI-Installs` path.

Agent Panel's exact MIT-licensed curated archive is stored beside this README
under its content digest. The harness verifies the archive size and SHA-256
from the baseline lock before parsing it, then verifies every extracted web
file again. A matching Desktop tree or `COMFYUI_CERTIFIED_AGENT_PANEL_ROOT`
may be used first, but the test never depends on an ephemeral system cache.

No request in this fixture reaches a network. Unknown routes, absolute URLs,
and credential-bearing headers raise before a transport could be selected.
