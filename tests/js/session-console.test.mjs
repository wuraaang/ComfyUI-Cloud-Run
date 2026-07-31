import assert from "node:assert/strict";
import test from "node:test";

import { createSessionConsole } from "../../web/js/session-console.js";
import { FakeDocument } from "./fake-dom.mjs";


test("renders dependency states as inert text and gates offer search", () => {
  const document = new FakeDocument();
  const consoleView = createSessionConsole(document, {});
  document.body.appendChild(consoleView.root);

  consoleView.renderPreflight({
    preflight_id: "preflight-1",
    capture_id: "capture-1",
    rentable: false,
    manifest_digest: null,
    transfer_bytes: 12,
    output_allowance_bytes: null,
    disk_gb: null,
    rows: [
      {
        dependency_id: "node:KSampler",
        kind: "core_node",
        display_name: "<KSampler>",
        status: "resolved",
        size_bytes: 1,
      },
      {
        dependency_id: "node:Fancy",
        kind: "custom_node",
        display_name: "Fancy",
        status: "mapping_required",
        reason: "Approve source",
      },
      {
        dependency_id: "node:Unsafe",
        kind: "custom_node",
        display_name: "Unsafe",
        status: "unsupported",
        reason: "Mutable revision",
      },
    ],
  });

  assert.match(consoleView.root.textContent, /<KSampler>/);
  assert.match(consoleView.root.textContent, /mapping_required/);
  assert.match(consoleView.root.textContent, /12 bytes/);
  assert.equal(consoleView.searchButton.disabled, true);
  assert.equal(consoleView.root.querySelector("script"), null);

  consoleView.renderPreflight({
    preflight_id: "preflight-2",
    capture_id: "capture-1",
    rentable: true,
    manifest_digest: "a".repeat(64),
    transfer_bytes: 12,
    output_allowance_bytes: 8,
    disk_gb: 80,
    rows: [],
  });
  assert.equal(consoleView.searchButton.disabled, false);
  assert.match(consoleView.root.textContent, /80 GiB/);
});


test("preflight button uses only the persisted capture identity", async () => {
  const document = new FakeDocument();
  const calls = [];
  const api = {
    async preflight(captureId, explicitOutputAllowanceBytes) {
      calls.push([captureId, explicitOutputAllowanceBytes]);
      return {
        preflight_id: "preflight-1",
        capture_id: captureId,
        rentable: true,
        manifest_digest: "a".repeat(64),
        transfer_bytes: 0,
        output_allowance_bytes: 1024,
        disk_gb: 80,
        rows: [],
      };
    },
  };
  const consoleView = createSessionConsole(document, api);
  document.body.appendChild(consoleView.root);

  assert.equal(consoleView.preflightButton.disabled, true);
  consoleView.setCapture("capture-1");
  consoleView.outputAllowanceInput.value = "1024";
  await consoleView.preflightButton.click();

  assert.deepEqual(calls, [["capture-1", 1024]]);
  assert.equal(consoleView.preflightId, "preflight-1");
  assert.equal(consoleView.searchButton.disabled, false);
});
