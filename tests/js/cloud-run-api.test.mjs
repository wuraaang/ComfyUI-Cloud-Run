import assert from "node:assert/strict";
import test from "node:test";

import { createCloudRunApi } from "../../web/js/cloud-run-api.js";


test("Verify Vast access posts no body or provider data", async () => {
  const calls = [];
  const api = createCloudRunApi(async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    return {
      ok: true,
      async json() {
        return { verified: true, instance_count: 0 };
      },
    };
  });

  assert.equal(typeof api.verifyVastAccess, "function");
  assert.deepEqual(await api.verifyVastAccess(), {
    verified: true,
    instance_count: 0,
  });
  assert.deepEqual(calls, [[
    "/cloud-run/api/settings/verify-vast-access",
    { method: "POST" },
  ]]);
  assert.equal("body" in calls[0][1], false);
  assert.equal("headers" in calls[0][1], false);
});


test("Desktop APIs stay same-origin and separate setup from Vast bootstrap", async () => {
  const calls = [];
  const api = createCloudRunApi(async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    return {
      ok: true,
      async json() {
        return { ready: true };
      },
    };
  });

  await api.getDesktopContext();
  await api.getDesktopSetup();
  await api.activateDesktopRelay("session-1");
  await api.deactivateDesktopRelay("session-1");
  await api.getDesktopBootstrap();
  await api.acknowledgeDesktopBootstrap(3);

  assert.deepEqual(calls, [
    ["/cloud-run/api/desktop-context", {}],
    ["/cloud-run/api/desktop-setup", {}],
    [
      "/cloud-run/api/sessions/session-1/desktop-relay",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      },
    ],
    [
      "/cloud-run/api/sessions/session-1/desktop-relay",
      { method: "DELETE" },
    ],
    ["/cloud-run/api/desktop-bootstrap", {}],
    [
      "/cloud-run/api/desktop-bootstrap",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bootstrap_revision: 3 }),
      },
    ],
  ]);
});
