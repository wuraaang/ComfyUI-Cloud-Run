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
