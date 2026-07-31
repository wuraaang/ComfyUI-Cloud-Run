import assert from "node:assert/strict";
import test from "node:test";

import { createCloudRunApi } from "../../web/js/cloud-run-api.js";
import { createSessionConsole } from "../../web/js/session-console.js";
import { FakeDocument } from "./fake-dom.mjs";


function fullQuote(overrides = {}) {
  return {
    session_id: "session-1",
    status: "offer_selected",
    deadline_at: 8_200,
    deadline_mode: "finite",
    disk_gb: 96,
    offer: {
      offer_id: "42",
      gpu_name: "RTX 4090",
      gpu_ram_gb: 24,
      dph_total: 0.50,
      reliability: 0.99,
      max_price_per_hour: 0.55,
      expires_at: 1_060,
      disk_gb: 96,
      transfer_bytes: 12 * 1024,
      output_allowance_bytes: 4 * 1024,
      inet_down_cost: 0.01,
      inet_up_cost: 0.02,
      duration_seconds: 7_200,
      deadline_mode: "finite",
      approximate_max_active_charge: 1,
      template_hash_id: "1".repeat(32),
      worker_commit: "a".repeat(40),
      worker_archive_sha256: "b".repeat(64),
      protocol_version: "1",
      manifest_digest: "c".repeat(64),
    },
    ...overrides,
  };
}


function sessionPayload(overrides = {}) {
  return {
    ...fullQuote(),
    status: "ready",
    instance_id: "77",
    created_at: 1_000,
    updated_at: 1_020,
    deadline_at: 8_200,
    deadline_mode: "finite",
    deadline_alerts: [],
    billing_may_continue: false,
    emergency_action: null,
    ...overrides,
  };
}


function fakeApi() {
  const api = {
    deadlineCalls: [],
    destroyCalls: [],
    jobCalls: [],
    async reviewDestroy() {
      return {
        session_id: "session-1",
        instance_id: "77",
        status: "ready",
        unverified_artifact_ids: ["output-2"],
        warning:
          "Unverified or incomplete results will be irreversibly lost.",
        review_token: "review-token".repeat(4),
        expires_at: 1_300,
      };
    },
    async destroySession(sessionId, confirmation) {
      api.destroyCalls.push([sessionId, confirmation]);
      return sessionPayload({
        status: "destroyed",
        instance_id: null,
      });
    },
    async createJob(sessionId, captureId, idempotencyKey) {
      api.jobCalls.push({
        session_id: sessionId,
        capture_id: captureId,
        idempotency_key: idempotencyKey,
      });
      return {
        job_id: "job-2",
        session_id: sessionId,
        status: "running",
        error: null,
      };
    },
    async updateDeadline(sessionId, payload) {
      api.deadlineCalls.push([sessionId, payload]);
      return sessionPayload({
        status: "running",
        deadline_at: 10_000,
      });
    },
    previewUrl(sessionId, jobId, previewId) {
      return `/cloud-run/api/sessions/${sessionId}/jobs/${jobId}/previews/${previewId}`;
    },
    artifactUrl(sessionId, jobId, artifactId) {
      return `/cloud-run/api/sessions/${sessionId}/jobs/${jobId}/artifacts/${artifactId}`;
    },
  };
  return api;
}


test("session API uses only the exact same-origin lifecycle routes", async () => {
  const calls = [];
  const fetchImpl = async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    return {
      ok: true,
      async json() {
        return { ok: true };
      },
    };
  };
  const api = createCloudRunApi(fetchImpl);

  await api.createSession({
    preflight_id: "preflight-1",
    offer_id: "42",
    idempotency_key: "session-key",
    deadline: { mode: "finite", duration_seconds: 7_200 },
  });
  await api.approveMapping("FancyNode", "d".repeat(64));
  await api.confirmSession("session-1", "session-key");
  await api.getSession("session-1");
  await api.createJob("session-1", "capture-2", "job-key-2");
  await api.getJob("session-1", "job-2");
  await api.getEvents("session-1", "job-2", 7);
  await api.updateDeadline("session-1", { action: "add_30_minutes" });
  await api.reviewDestroy("session-1");
  await api.destroySession("session-1", {
    review_token: "review-token",
    acknowledge_data_loss: true,
  });

  assert.deepEqual(
    calls.map(([endpoint, options]) => [endpoint, options.method ?? "GET"]),
    [
      ["/cloud-run/api/sessions", "POST"],
      ["/cloud-run/api/mappings/FancyNode", "PUT"],
      ["/cloud-run/api/sessions/session-1/confirm", "POST"],
      ["/cloud-run/api/sessions/session-1", "GET"],
      ["/cloud-run/api/sessions/session-1/jobs", "POST"],
      ["/cloud-run/api/sessions/session-1/jobs/job-2", "GET"],
      [
        "/cloud-run/api/sessions/session-1/jobs/job-2/events?after_sequence=7",
        "GET",
      ],
      ["/cloud-run/api/sessions/session-1/deadline", "PUT"],
      ["/cloud-run/api/sessions/session-1/destroy-review", "POST"],
      ["/cloud-run/api/sessions/session-1", "DELETE"],
    ],
  );
  assert.equal(
    api.previewUrl("session-1", "job-2", "preview-1"),
    "/cloud-run/api/sessions/session-1/jobs/job-2/previews/preview-1",
  );
  assert.equal(
    api.artifactUrl("session-1", "job-2", "output-1"),
    "/cloud-run/api/sessions/session-1/jobs/job-2/artifacts/output-1",
  );
});


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


test("renders only validated immutable Hugging Face provenance as a safe link", () => {
  const document = new FakeDocument();
  const view = createSessionConsole(document, {});
  document.body.appendChild(view.root);
  const revision = "a".repeat(40);
  const digest = "b".repeat(64);
  const repository = "black-forest-labs/FLUX.1-Fill-dev";
  const filename = "flux1-fill-dev.safetensors";
  const destination = `models/diffusion_models/${filename}`;

  view.renderPreflight({
    preflight_id: "preflight-provenance",
    capture_id: "capture-1",
    rentable: true,
    manifest_digest: "c".repeat(64),
    transfer_bytes: 4_096,
    output_allowance_bytes: 4_096,
    disk_gb: 80,
    rows: [{
      dependency_id: "artifact:model-flux-fill",
      kind: "model",
      display_name: filename,
      status: "resolved",
      source_kind: "huggingface",
      source_locator:
        `https://huggingface.co/${repository}/resolve/${revision}/${filename}`,
      immutable_revision: revision,
      size_bytes: 4_096,
      sha256: digest,
      destination,
      reason: null,
    }],
  });

  const text = view.root.textContent;
  assert.ok(text.includes(destination));
  assert.ok(text.includes(`${repository}/${filename}`));
  assert.ok(text.includes(digest.slice(0, 12)));
  assert.ok(text.includes(revision));
  assert.ok(text.includes("4 KB"));
  const links = view.root.querySelectorAll("a");
  assert.equal(links.length, 1);
  assert.equal(
    links[0].getAttribute("href"),
    `https://huggingface.co/${repository}`,
  );
  assert.equal(links[0].getAttribute("target"), "_blank");
  assert.equal(links[0].getAttribute("rel"), "noopener noreferrer");
});


test("keeps hostile model provenance inert and creates no link", () => {
  const document = new FakeDocument();
  const view = createSessionConsole(document, {});
  document.body.appendChild(view.root);
  const revision = "a".repeat(40);
  const pinned =
    `https://huggingface.co/owner/repository/resolve/${revision}/model.safetensors`;
  const hostileLocators = [
    pinned.replace("huggingface.co", "example.com"),
    pinned.replace("huggingface.co", "user@huggingface.co"),
    pinned.replace("huggingface.co", "huggingface.co:443"),
    `${pinned}#fragment`,
    pinned.replace("/resolve/", "/blob/"),
    pinned.replace("model.safetensors", "%6dodel.safetensors"),
    "<a href=\"https://huggingface.co/owner/repository\">model</a>",
    "javascript:alert(1)",
  ];

  view.renderPreflight({
    preflight_id: "preflight-hostile",
    capture_id: "capture-1",
    rentable: false,
    manifest_digest: null,
    transfer_bytes: 0,
    output_allowance_bytes: null,
    disk_gb: null,
    rows: hostileLocators.map((locator, index) => ({
      dependency_id: `artifact:hostile-${index}`,
      kind: "model",
      display_name: locator,
      status: "resolved",
      source_kind: "huggingface",
      source_locator: locator,
      immutable_revision: revision,
      size_bytes: 4_096,
      sha256: "b".repeat(64),
      destination: "models/diffusion_models/model.safetensors",
      reason: null,
    })),
  });

  for (const locator of hostileLocators) {
    assert.ok(view.root.textContent.includes(locator));
  }
  assert.equal(view.root.querySelectorAll("a").length, 0);
  assert.equal(view.root.querySelector("script"), null);
});


test("mapping candidates require an explicit pinned approval then rerun preflight", async () => {
  const document = new FakeDocument();
  const calls = [];
  const api = {
    async approveMapping(mappingId, candidateDigest) {
      calls.push(["approve", mappingId, candidateDigest]);
      return { approved: true };
    },
    async preflight(captureId, allowance) {
      calls.push(["preflight", captureId, allowance]);
      return {
        preflight_id: "preflight-2",
        capture_id: captureId,
        rentable: false,
        manifest_digest: null,
        transfer_bytes: 0,
        output_allowance_bytes: null,
        disk_gb: null,
        rows: [],
      };
    },
  };
  const view = createSessionConsole(document, api);
  document.body.appendChild(view.root);
  view.setCapture("capture-1");
  view.renderPreflight({
    preflight_id: "preflight-1",
    capture_id: "capture-1",
    rentable: false,
    manifest_digest: null,
    transfer_bytes: 12,
    output_allowance_bytes: null,
    disk_gb: null,
    rows: [{
      dependency_id: "node:FancyNode",
      kind: "custom_node",
      display_name: "FancyNode",
      status: "mapping_required",
      source_kind: "registry",
      reason: "Explicit approval required",
      mapping_candidate: {
        class_type: "FancyNode",
        source_kind: "registry",
        candidate_digest: "d".repeat(64),
        repository_url: "https://github.com/example/fancy",
        revision: "e".repeat(40),
        package_id: "example.fancy",
        archive_complete: true,
        wheels_complete: true,
        approved: false,
        origin_source_kind: "registry",
      },
    }],
  });

  const button = document.getElementById("cloud-run-approve-mapping-0");
  assert.ok(button);
  assert.match(view.root.textContent, /github\.com\/example\/fancy/);
  assert.match(view.root.textContent, new RegExp("e".repeat(40)));
  await button.click();

  assert.deepEqual(calls, [
    ["approve", "FancyNode", "d".repeat(64)],
    ["preflight", "capture-1", null],
  ]);
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


test("paid review shows every bounded cost and immutable identity", () => {
  const document = new FakeDocument();
  const view = createSessionConsole(document, fakeApi());
  document.body.appendChild(view.root);

  view.renderQuote(fullQuote());

  const text = view.root.textContent;
  for (const expected of [
    "Offer 42",
    "RTX 4090",
    "24 GB",
    "$0.50/h",
    "Reliability: 99.0%",
    "Bandwidth: $0.010/GB down; $0.020/GB up",
    "96 GB ephemeral disk",
    "12 KB dependencies and inputs",
    "2 hour automatic limit",
    "approximately $1.00 active/storage",
    `template ${"1".repeat(32)}`,
    `worker ${"a".repeat(40)}`,
    "bandwidth pricing can change the final provider charge",
  ]) {
    assert.ok(text.includes(expected), expected);
  }
});


test("console renders remote node progress previews errors and verified outputs", () => {
  const document = new FakeDocument();
  const api = fakeApi();
  const view = createSessionConsole(document, api);
  document.body.appendChild(view.root);

  view.renderSession(sessionPayload({
    status: "running",
    current_job: {
      job_id: "job-1",
      session_id: "session-1",
      status: "running",
      current_node: { id: "7", title: "<Upscale>" },
      progress: { value: 3, max: 10 },
      previews: [{ id: "preview-1", state: "verified" }],
      outputs: [{
        id: "output-1",
        state: "local_verified",
        filename: "wallpaper.png",
      }],
      error: {
        code: "execution_error",
        message: "<sanitized remote error>",
      },
    },
  }));

  assert.match(view.root.textContent, /<Upscale>/);
  assert.match(view.root.textContent, /3 of 10/);
  assert.match(view.root.textContent, /wallpaper\.png/);
  assert.match(view.root.textContent, /<sanitized remote error>/);
  assert.equal(view.root.querySelectorAll("script").length, 0);
  assert.equal(
    view.root.querySelector("img").getAttribute("src"),
    "/cloud-run/api/sessions/session-1/jobs/job-1/previews/preview-1",
  );
  assert.equal(
    view.root.querySelector("a").getAttribute("href"),
    "/cloud-run/api/sessions/session-1/jobs/job-1/artifacts/output-1",
  );
});


test("provisioning shows the meaningful-progress clock and ten-minute stall", () => {
  const document = new FakeDocument();
  const view = createSessionConsole(document, fakeApi());
  document.body.appendChild(view.root);

  view.renderSession(sessionPayload({
    status: "provisioning",
    provisioning: {
      transferred_bytes: 4_096,
      total_bytes: 8_192,
      installed_units: 2,
      validated_units: 1,
      seconds_without_progress: 599,
      stall_budget_seconds: 600,
    },
  }));

  assert.match(view.root.textContent, /599 seconds since meaningful progress/);
  assert.doesNotMatch(view.provisioningStatus.className, /cloud-run-danger/);

  view.renderSession(sessionPayload({
    status: "provisioning",
    provisioning: {
      transferred_bytes: 4_096,
      total_bytes: 8_192,
      installed_units: 2,
      validated_units: 1,
      seconds_without_progress: 600,
      stall_budget_seconds: 600,
    },
  }));

  assert.match(view.root.textContent, /STALL DETECTED/);
  assert.match(view.provisioningStatus.className, /cloud-run-danger/);
});


test("provisioning renders only bounded worker phase and inert model text", () => {
  const document = new FakeDocument();
  const view = createSessionConsole(document, fakeApi());
  document.body.appendChild(view.root);
  const hostileModel = "<script>Gold model</script>.safetensors";

  view.renderSession(sessionPayload({
    status: "provisioning",
    provisioning: {
      phase: "model_transfer",
      current_model: hostileModel,
      transferred_bytes: 4_096,
      total_bytes: 8_192,
      installed_units: 0,
      validated_units: 0,
      seconds_without_progress: 5,
      stall_budget_seconds: 600,
      source_url: "https://huggingface.co/private?token=secret",
    },
  }));

  assert.match(view.root.textContent, /Model transfer/);
  assert.ok(view.root.textContent.includes(hostileModel));
  assert.match(view.root.textContent, /4 KB of 8 KB/);
  assert.doesNotMatch(view.root.textContent, /huggingface\.co/);
  assert.doesNotMatch(view.root.textContent, /token=secret/);
  assert.equal(view.root.querySelector("script"), null);
  assert.equal(view.root.querySelector("a"), null);

  view.renderSession(sessionPayload({
    status: "provisioning",
    provisioning: {
      phase: "unknown_phase",
      current_model: "https://example.com/private-model",
      transferred_bytes: 9_999,
      total_bytes: 10,
      installed_units: 0,
      validated_units: 0,
      seconds_without_progress: 5,
      stall_budget_seconds: 600,
    },
  }));

  assert.match(view.root.textContent, /Verified transfer\/install/);
  assert.doesNotMatch(view.root.textContent, /9\.8 KB of 10 bytes/);
  assert.doesNotMatch(view.root.textContent, /example\.com/);
});


test("no-limit stays red and destroy requires review then final acknowledgement", async () => {
  const document = new FakeDocument();
  const api = fakeApi();
  const view = createSessionConsole(document, api);
  document.body.appendChild(view.root);
  view.renderSession(sessionPayload({
    deadline_mode: "none",
    deadline_at: null,
    status: "ready",
  }));

  assert.match(view.deadlineWarning.className, /cloud-run-danger/);
  assert.equal(
    view.destroyButton.textContent,
    "Destroy GPU — stop all Vast billing",
  );

  await view.destroyButton.click();
  assert.match(view.destroyReview.textContent, /irreversibly lost/);
  assert.equal(api.destroyCalls.length, 0);
  view.dataLossCheckbox.checked = true;
  await view.destroyNowButton.click();
  assert.equal(api.destroyCalls.length, 1);
  assert.equal(api.destroyCalls[0][1].acknowledge_data_loss, true);
});


test("ready session captures a fresh canvas and submits a second job", async () => {
  const document = new FakeDocument();
  const api = fakeApi();
  const capture = {
    calls: 0,
    async capture() {
      capture.calls += 1;
      return { capture_id: "capture-2" };
    },
  };
  const view = createSessionConsole(document, api, {
    capture: () => capture.capture(),
    newIdempotencyKey: () => "job-key-2",
  });
  document.body.appendChild(view.root);
  view.renderSession(sessionPayload({ status: "ready" }));

  await view.runNextJobButton.click();

  assert.equal(capture.calls, 1);
  assert.equal(api.jobCalls[0].capture_id, "capture-2");
  assert.equal(api.jobCalls[0].idempotency_key, "job-key-2");
});


test("a running job can extend its finite billing deadline", async () => {
  const document = new FakeDocument();
  const api = fakeApi();
  const view = createSessionConsole(document, api);
  document.body.appendChild(view.root);
  view.renderSession(sessionPayload({ status: "running" }));

  assert.equal(view.extendThirtyButton.disabled, false);
  await view.extendThirtyButton.click();

  assert.deepEqual(api.deadlineCalls, [[
    "session-1",
    { action: "add_30_minutes" },
  ]]);
});
