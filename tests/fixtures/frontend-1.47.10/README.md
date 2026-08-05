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
module tree. It uses only repository fixtures: an isolated `HOME` with every
`COMFYUI_*` source variable absent exercises the same gate.

Agent Panel's exact MIT-licensed curated archive is stored beside this README
under its content digest. The harness verifies the archive size and SHA-256
from the baseline lock before parsing it, then verifies every extracted web
file again.

The minimal Efficiency Nodes fixture is the content-addressed archive
`efficiency-frontend-27862272e5ba1bc7b7066dd8475cb3234ba496ba57967358f5a636c16bad1a21.tar`.
It contains only the exact upstream `LICENSE` and `js/previewfix.js` needed by
this compatibility gate. Provenance is Registry version 1.0.9 from
`https://github.com/jags111/efficiency-nodes-comfyui` at immutable revision
`835bbe14627cccc871822e804c65c734960d3c6e`; the included source is licensed
under GPL-3.0. The harness verifies the mini-archive's size and SHA-256 before
parsing it, then verifies both extracted files against their individual
size/SHA-256 records in the certified baseline lock. This fixture is not a
substitute for the complete curated worker package.

No request in this fixture reaches a network. Unknown routes, absolute URLs,
and credential-bearing headers raise before a transport could be selected.
