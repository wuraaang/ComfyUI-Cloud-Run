import assert from "node:assert/strict";
import test from "node:test";

import {
  bootstrapVastCanvas,
  installNativePromptIdentity,
  isVastRole,
  readDesktopContext,
  startExtension,
} from "../../web/js/comfyui-vast.js";


function jsonResponse(payload, { ok = true } = {}) {
  return {
    ok,
    async json() {
      return payload;
    },
  };
}


test("desktop context is same-origin, strict, and role-aware", async () => {
  const calls = [];
  const local = await readDesktopContext(async (endpoint, options) => {
    calls.push([endpoint, options]);
    return jsonResponse({ role: "local" });
  });
  const vast = await readDesktopContext(async () => jsonResponse({
    role: "vast",
    session_id: "session-1",
    profile_revision: 3,
    agent_bridge_url: "ws://127.0.0.1:32145/cloud-run/api/agent/ws",
  }));

  assert.deepEqual(calls, [["/cloud-run/api/desktop-context", undefined]]);
  assert.equal(isVastRole(local), false);
  assert.equal(isVastRole(vast), true);
  await assert.rejects(
    readDesktopContext(async () => jsonResponse({
      role: "vast",
      session_id: "../other",
      profile_revision: 3,
      agent_bridge_url: "wss://example.com/agent",
    })),
    /context/i,
  );
});


test("local role mounts Cloud Vast and leaves native APIs untouched", async () => {
  const calls = [];
  const api = {
    fetchApi(endpoint, options) {
      calls.push([endpoint, options]);
    },
  };
  const app = { queuePrompt() {} };
  const originalFetch = api.fetchApi;
  const originalQueue = app.queuePrompt;
  let mounts = 0;

  const result = await startExtension({
    context: { role: "local" },
    api,
    app,
    mountLifecycle() {
      mounts += 1;
      return { textContent: "Cloud Vast" };
    },
  });

  assert.equal(result.launcher.textContent, "Cloud Vast");
  assert.equal(mounts, 1);
  assert.equal(api.fetchApi, originalFetch);
  assert.equal(app.queuePrompt, originalQueue);
  assert.deepEqual(calls, []);
});


test("vast role adds one server identity only to native prompt", async () => {
  const calls = [];
  const api = {
    async fetchApi(endpoint, options = {}) {
      calls.push([endpoint, options]);
      return jsonResponse({});
    },
  };
  const app = { queuePrompt() {} };
  const originalQueue = app.queuePrompt;
  let generated = 0;
  const cryptography = {
    randomUUID() {
      generated += 1;
      return "11111111-1111-4111-8111-111111111111";
    },
  };

  const result = await startExtension({
    context: {
      role: "vast",
      session_id: "session-1",
      profile_revision: 3,
      agent_bridge_url: "ws://127.0.0.1:32145/cloud-run/api/agent/ws",
    },
    api,
    app,
    cryptoImpl: cryptography,
  });
  assert.equal(result.launcher, null);

  const body = "{\"prompt\":{}}";
  await api.fetchApi("/prompt", {
    method: "POST",
    body,
    headers: { "Content-Type": "application/json" },
  });
  await api.fetchApi("/object_info");

  assert.equal(generated, 1);
  assert.equal(calls[0][1].body, body);
  assert.equal(
    calls[0][1].headers.get("X-Cloud-Vast-Request-Id"),
    "11111111-1111-4111-8111-111111111111",
  );
  assert.equal(calls[1][1].headers, undefined);
  assert.equal(app.queuePrompt, originalQueue);
});


test("native prompt identity rejects caller identity and restores exactly", async () => {
  const original = async () => jsonResponse({});
  const api = { fetchApi: original };
  const restore = installNativePromptIdentity(api, {
    randomUUID: () => "11111111-1111-4111-8111-111111111111",
  });

  await assert.rejects(
    api.fetchApi("/prompt", {
      method: "POST",
      headers: { "x-cloud-vast-request-id": "forged" },
    }),
    /already set/i,
  );
  restore();
  assert.equal(api.fetchApi, original);
});


test("bootstrap loads once and a delayed response cannot overwrite an edit", async () => {
  let graphRevision = 1;
  const loaded = [];
  const acknowledged = [];
  const app = {
    graph: {
      serialize() {
        return { graph_revision: graphRevision };
      },
    },
    async loadGraphData(workflow) {
      loaded.push(workflow);
    },
  };
  const record = {
    bootstrap_revision: 3,
    active_revision: 2,
    remote_edit_revision: 2,
    workflow: { version: 1, nodes: [{ id: 7 }] },
  };

  const first = await bootstrapVastCanvas(app, record, {
    acknowledge: async (revision) => acknowledged.push(revision),
  });
  const second = await bootstrapVastCanvas(app, record, {
    acknowledge: async (revision) => acknowledged.push(revision),
  });
  assert.equal(first.loaded, true);
  assert.equal(second.loaded, false);
  assert.deepEqual(loaded, [record.workflow]);
  assert.deepEqual(acknowledged, [3]);

  let release;
  const delayed = bootstrapVastCanvas({
    graph: app.graph,
    async loadGraphData(workflow) {
      loaded.push(workflow);
    },
  }, { role: "vast" }, {
    loadBootstrap: () => new Promise((resolve) => {
      release = resolve;
    }),
    acknowledge: async (revision) => acknowledged.push(revision),
  });
  graphRevision += 1;
  release({
    bootstrap_revision: 4,
    active_revision: 3,
    remote_edit_revision: 3,
    workflow: { version: 1, nodes: [{ id: 8 }] },
  });
  const skipped = await delayed;

  assert.equal(skipped.loaded, false);
  assert.equal(skipped.reason, "canvas_changed");
  assert.equal(loaded.length, 1);
  assert.deepEqual(acknowledged, [3]);
});
