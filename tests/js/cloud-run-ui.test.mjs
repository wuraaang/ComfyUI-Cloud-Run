import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  mountCloudRun,
  registerCloudRunWhenReady,
  renderOffers,
} from "../../web/js/cloud-run.js";
import { FakeDocument } from "./fake-dom.mjs";


function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    async json() {
      return payload;
    },
  };
}


function settingsPayload(overrides = {}) {
  return {
    configured: false,
    max_price_per_hour: 1,
    min_vram_gb: 16,
    lifecycle_enabled: true,
    r2_configured: false,
    huggingface_configured: false,
    civitai_configured: false,
    active_sessions: [],
    active_sessions_error: null,
    recent_sessions: [],
    ...overrides,
  };
}


function readinessPayload(overrides = {}) {
  return {
    label: "cold",
    cached_bytes: 0,
    remaining_bytes: 12 * 1024,
    assumed_mbps: 25,
    estimated_seconds: 235,
    source_ready: true,
    ten_minute_eligible: true,
    digest: "d".repeat(64),
    ...overrides,
  };
}


function quoteSession(status = "offer_selected", overrides = {}) {
  const rentalOutcome = status === "offer_selected"
    ? "not_started"
    : ["creating", "reconciling_create"].includes(status)
      ? "unknown"
      : "active";
  return {
    session_id: "session-1",
    status,
    rental_outcome: rentalOutcome,
    can_search_offers: rentalOutcome === "not_started",
    can_destroy: rentalOutcome !== "not_started",
    can_verify_vast_access: false,
    offer: {
      offer_id: "42",
      gpu_name: "RTX 4090",
      gpu_ram_gb: 24,
      dph_total: 0.5,
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
      max_instance_creates: 1,
      readiness: readinessPayload(),
      estimate_digest: "d".repeat(64),
    },
    instance_id: status === "offer_selected" ? null : "77",
    deadline_at: 8_200,
    deadline_mode: "finite",
    deadline_alerts: [],
    deadline_sync_pending: false,
    pending_deadline_at: null,
    pending_deadline_mode: null,
    disk_gb: 96,
    retry_count: 0,
    destroy_requested: false,
    residual_inventory: [],
    error: null,
    created_at: 1_000,
    updated_at: 1_020,
    billing_may_continue: rentalOutcome !== "not_started",
    emergency_action: null,
    ...overrides,
  };
}


function manualQuoteSession(status = "offer_selected", overrides = {}) {
  const session = quoteSession(status);
  return {
    ...session,
    offer: {
      ...session.offer,
      duration_seconds: null,
      deadline_mode: "none",
      approximate_max_active_charge: null,
    },
    deadline_at: null,
    deadline_mode: "none",
    ...overrides,
  };
}


function preflightPayload() {
  return {
    preflight_id: "preflight-1",
    capture_id: "capture-1",
    rentable: true,
    manifest_digest: "c".repeat(64),
    transfer_bytes: 12 * 1024,
    output_allowance_bytes: 4 * 1024,
    disk_gb: 96,
    rows: [
      {
        dependency_id: "node:KSampler",
        kind: "core_node",
        display_name: "KSampler",
        status: "resolved",
        source_kind: "core",
        immutable_revision: null,
        size_bytes: null,
        sha256: null,
        destination: null,
        reason: null,
      },
    ],
  };
}


function nativePromptBody(seed = 12) {
  return {
    workflow: {
      version: 1,
      extra: { frontendVersion: "1.47.10" },
      nodes: [{ id: 7 }],
    },
    output: {
      "7": {
        class_type: "KSampler",
        inputs: { seed },
      },
    },
    queue_options: {
      number: -1,
      batch_count: 1,
      front: false,
      partial_execution_targets: ["7"],
      preview_method: "latent2rgb",
    },
  };
}


function captureHost(seed = 12) {
  const localSubmissions = [];
  const api = {
    async queuePrompt(number, payload) {
      localSubmissions.push([number, payload]);
      return { prompt_id: "local", node_errors: {} };
    },
  };
  const app = {
    async queuePrompt(number, batchCount) {
      assert.equal(batchCount, 1);
      const payload = nativePromptBody(seed);
      await api.queuePrompt(number, {
        workflow: payload.workflow,
        output: payload.output,
      }, {
        previewMethod: payload.queue_options.preview_method,
      });
    },
  };
  return { api, app, localSubmissions };
}


function mountLocalRunButton(document) {
  const actionbar = document.createElement("div");
  const queueGroup = document.createElement("div");
  const queueButton = document.createElement("button");
  const agentPanel = document.createElement("button");
  queueButton.setAttribute("data-testid", "queue-button");
  queueButton.textContent = "Run";
  agentPanel.setAttribute("data-testid", "agent-panel");
  agentPanel.textContent = "Agent Panel";
  queueGroup.appendChild(queueButton);
  actionbar.append(queueGroup, agentPanel);
  document.body.appendChild(actionbar);
  return { actionbar, agentPanel, queueButton, queueGroup };
}


test("mounts next to local Run and exposes the reusable session console", async () => {
  const document = new FakeDocument();
  const { actionbar, agentPanel, queueButton, queueGroup } =
    mountLocalRunButton(document);
  const requests = [];
  mountCloudRun(document, async (...args) => {
    requests.push(args);
    return jsonResponse(settingsPayload());
  });

  const launcher = document.getElementById("cloud-run-button");
  assert.ok(launcher);
  assert.deepEqual(actionbar.children, [queueGroup, launcher, agentPanel]);
  assert.equal(queueButton.textContent, "Run");
  assert.equal(agentPanel.textContent, "Agent Panel");
  assert.equal(launcher.textContent, "☁ Cloud Vast");
  assert.equal(
    launcher.getAttribute("title"),
    "Cloud Vast — paid GPU rental",
  );

  await launcher.click();

  assert.equal(document.getElementById("cloud-run-modal").open, true);
  assert.equal(requests[0][0], "/cloud-run/api/settings");
  assert.equal(document.getElementById("cloud-run-api-key").type, "password");
  assert.equal(
    document.getElementById("cloud-run-review-session").textContent,
    "Review paid rental",
  );
  assert.equal(document.getElementById("cloud-run-next-job"), null);
  assert.equal(
    document.getElementById("cloud-run-destroy-gpu").textContent,
    "Destroy GPU — stop all Vast billing",
  );
});


test("keyboard opening and repeated mounting never alter local Run listeners", async () => {
  const document = new FakeDocument();
  const { queueButton } = mountLocalRunButton(document);
  const localClick = () => {};
  queueButton.addEventListener("click", localClick);
  const originalListeners = queueButton.listeners.get("click").slice();
  const fetchImpl = async () => jsonResponse(settingsPayload());
  const first = mountCloudRun(document, fetchImpl);
  const second = mountCloudRun(document, fetchImpl);
  const event = {
    type: "keydown",
    key: "Enter",
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };

  await first.dispatchEvent(event);

  assert.equal(first, second);
  assert.equal(event.defaultPrevented, true);
  assert.equal(queueButton.textContent, "Run");
  assert.deepEqual(queueButton.listeners.get("click"), originalListeners);
});


test("registers through the pinned ComfyUI extension API", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app, localSubmissions } = captureHost();
  const requests = [];
  let extension = null;
  app.registerExtension = (specification) => {
    extension = specification;
  };
  api.fetchApi = async (endpoint, options) => {
    requests.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/desktop-context") {
      return jsonResponse({ role: "local" });
    }
    if (endpoint === "/cloud-run/api/settings") {
      return jsonResponse(settingsPayload());
    }
    if (endpoint === "/cloud-run/api/captures") {
      return jsonResponse({
        capture_id: "capture-1",
        prompt_digest: "d".repeat(64),
        status: "captured",
      });
    }
    throw new Error("unexpected endpoint");
  };
  const originalFetch = api.fetchApi;
  const originalQueue = api.queuePrompt;
  const browserWindow = {
    comfyAPI: {
      app: { app },
      api: { api },
    },
    setTimeout() {
      assert.fail("ready APIs must not schedule registration retry");
    },
  };

  assert.equal(registerCloudRunWhenReady(browserWindow, document), true);
  assert.equal(extension.name, "comfyui-cloud-run.lifecycle");
  assert.deepEqual(extension.commands.map(({ id, label }) => ({ id, label })), [
    { id: "vast-cloud-run.open", label: "Cloud Vast" },
  ]);
  assert.deepEqual(extension.menuCommands, [{
    path: ["Extensions", "Cloud Vast"],
    commands: ["vast-cloud-run.open"],
  }]);

  await extension.init();
  await extension.setup();
  await extension.commands[0].function();

  assert.deepEqual(
    requests.map(([endpoint]) => endpoint),
    [
      "/cloud-run/api/desktop-context",
      "/cloud-run/api/settings",
      "/cloud-run/api/captures",
    ],
  );
  assert.equal(localSubmissions.length, 0);
  assert.equal(api.fetchApi, originalFetch);
  assert.equal(api.queuePrompt, originalQueue);
});


test("vast Desktop role keeps native Run and mounts no lifecycle launcher", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const forwarded = [];
  const api = {
    async fetchApi(endpoint, options = {}) {
      if (endpoint === "/cloud-run/api/desktop-context") {
        return jsonResponse({
          role: "vast",
          session_id: "session-1",
          profile_revision: 3,
          agent_bridge_url:
            "ws://127.0.0.1:32145/cloud-run/api/agent/ws",
        });
      }
      forwarded.push([endpoint, options]);
      return jsonResponse({});
    },
  };
  const app = {
    registerExtension(value) {
      this.extension = value;
    },
    queuePrompt() {},
  };
  const originalQueue = app.queuePrompt;
  const browserWindow = {
    comfyAPI: { app: { app }, api: { api } },
    crypto: {
      randomUUID: () => "11111111-1111-4111-8111-111111111111",
    },
    setTimeout() {
      assert.fail("ready APIs must not schedule registration retry");
    },
  };

  registerCloudRunWhenReady(browserWindow, document);
  await app.extension.init();
  await app.extension.setup();
  await app.extension.commands[0].function();
  await api.fetchApi("/prompt", { method: "POST", body: "{}" });
  await api.fetchApi("/object_info");

  assert.equal(document.getElementById("cloud-run-button"), null);
  assert.equal(document.getElementById("cloud-run-modal"), null);
  assert.equal(app.queuePrompt, originalQueue);
  assert.equal(
    forwarded[0][1].headers.get("X-Cloud-Vast-Request-Id"),
    "11111111-1111-4111-8111-111111111111",
  );
  assert.equal(forwarded[1][1].headers, undefined);
});


async function optionalOfferFilterScenario(settingsOverrides = {}) {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app } = captureHost();
  const calls = [];
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      const update = options.method === "PUT"
        ? JSON.parse(options.body)
        : {};
      return jsonResponse(settingsPayload({
        configured: true,
        ...settingsOverrides,
        ...update,
      }));
    }
    if (endpoint === "/cloud-run/api/captures") {
      return jsonResponse({
        capture_id: "capture-1",
        prompt_digest: "d".repeat(64),
        status: "captured",
      });
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      return jsonResponse({ offers: [] });
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  }, {}, { app, api });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();
  return { calls, document };
}


test("optional offer filters render and submit blank fields", async () => {
  const { calls, document } = await optionalOfferFilterScenario({
    max_price_per_hour: 100,
    min_vram_gb: 1,
  });
  const priceInput = document.getElementById("cloud-run-max-price");
  const vramInput = document.getElementById("cloud-run-min-vram");

  assert.equal(priceInput.value, "");
  assert.equal(vramInput.value, "");
  await document.getElementById("cloud-run-search").click();

  const settingsPut = calls.find(
    ([endpoint, options]) => (
      endpoint === "/cloud-run/api/settings" && options.method === "PUT"
    ),
  );
  assert.ok(settingsPut, "blank filters must be persisted before search");
  assert.deepEqual(JSON.parse(settingsPut[1].body), {
    max_price_per_hour: 100,
    min_vram_gb: 1,
  });
});


test("optional offer filters allow VRAM-only and French-price-only search", async () => {
  {
    const { calls, document } = await optionalOfferFilterScenario();
    document.getElementById("cloud-run-max-price").value = "";
    document.getElementById("cloud-run-min-vram").value = "24";
    await document.getElementById("cloud-run-search").click();

    const settingsPut = calls.find(
      ([endpoint, options]) => (
        endpoint === "/cloud-run/api/settings" && options.method === "PUT"
      ),
    );
    assert.ok(settingsPut, "VRAM-only filters must be persisted");
    assert.deepEqual(JSON.parse(settingsPut[1].body), {
      max_price_per_hour: 100,
      min_vram_gb: 24,
    });
  }

  {
    const { calls, document } = await optionalOfferFilterScenario();
    const priceInput = document.getElementById("cloud-run-max-price");
    priceInput.value = "0,46";
    document.getElementById("cloud-run-min-vram").value = "";
    assert.equal(priceInput.type, "text");
    assert.equal(priceInput.getAttribute("inputmode"), "decimal");
    await document.getElementById("cloud-run-search").click();

    const settingsPut = calls.find(
      ([endpoint, options]) => (
        endpoint === "/cloud-run/api/settings" && options.method === "PUT"
      ),
    );
    assert.ok(settingsPut, "French-price-only filters must be persisted");
    assert.deepEqual(JSON.parse(settingsPut[1].body), {
      max_price_per_hour: 0.46,
      min_vram_gb: 1,
    });
  }
});


test("filter validation identifies the invalid field", async () => {
  {
    const { calls, document } = await optionalOfferFilterScenario();
    document.getElementById("cloud-run-max-price").value = "0,4.6";
    document.getElementById("cloud-run-min-vram").value = "24";
    await document.getElementById("cloud-run-search").click();

    assert.equal(
      calls.filter(([, options]) => options.method === "PUT").length,
      0,
    );
    assert.match(
      document.getElementById("cloud-run-status").textContent,
      /^Price must be blank or a number such as 0\.46 \(0,46 also works\)\.$/,
    );
  }

  {
    const { calls, document } = await optionalOfferFilterScenario();
    document.getElementById("cloud-run-max-price").value = "0.46";
    document.getElementById("cloud-run-min-vram").value = "24.5";
    await document.getElementById("cloud-run-search").click();

    assert.equal(
      calls.filter(([, options]) => options.method === "PUT").length,
      0,
    );
    assert.match(
      document.getElementById("cloud-run-status").textContent,
      /^VRAM preference must be blank or a whole number such as 16 or 24\.$/,
    );
  }
});


test("Enter searches optional filters from price or VRAM", async () => {
  for (const field of ["vram", "price"]) {
    const { calls, document } = await optionalOfferFilterScenario();
    const priceInput = document.getElementById("cloud-run-max-price");
    const vramInput = document.getElementById("cloud-run-min-vram");
    priceInput.value = field === "price" ? "0,46" : "";
    vramInput.value = field === "vram" ? "24" : "";
    const event = {
      type: "keydown",
      key: "Enter",
      defaultPrevented: false,
      preventDefault() {
        this.defaultPrevented = true;
      },
    };

    await (field === "price" ? priceInput : vramInput).dispatchEvent(event);

    assert.equal(event.defaultPrevented, true, field);
    const settingsPuts = calls.filter(
      ([endpoint, options]) => (
        endpoint === "/cloud-run/api/settings" && options.method === "PUT"
      ),
    );
    const offerSearches = calls.filter(
      ([endpoint]) => endpoint === "/cloud-run/api/offers",
    );
    assert.equal(settingsPuts.length, 1, field);
    assert.equal(offerSearches.length, 1, field);
    assert.deepEqual(JSON.parse(settingsPuts[0][1].body), field === "price"
      ? { max_price_per_hour: 0.46, min_vram_gb: 1 }
      : { max_price_per_hour: 100, min_vram_gb: 24 });
    assert.ok(
      calls.indexOf(settingsPuts[0]) < calls.indexOf(offerSearches[0]),
      field,
    );
  }
});


test("Search persists visible settings before requesting offers", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app } = captureHost();
  const calls = [];
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      return jsonResponse(settingsPayload({ configured: true }));
    }
    if (endpoint === "/cloud-run/api/captures") {
      return jsonResponse({
        capture_id: "capture-1",
        prompt_digest: "d".repeat(64),
        status: "captured",
      });
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      return jsonResponse({ offers: [] });
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  }, {}, { app, api });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();
  document.getElementById("cloud-run-max-price").value = "1.25";
  document.getElementById("cloud-run-min-vram").value = "48";
  await document.getElementById("cloud-run-search").click();

  assert.deepEqual(
    calls.map(([endpoint, options]) => [endpoint, options.method ?? "GET"]),
    [
      ["/cloud-run/api/settings", "GET"],
      ["/cloud-run/api/captures", "POST"],
      ["/cloud-run/api/preflights", "POST"],
      ["/cloud-run/api/settings", "PUT"],
      ["/cloud-run/api/offers", "POST"],
    ],
  );
  const settingsPut = calls.find(
    ([endpoint, options]) => (
      endpoint === "/cloud-run/api/settings" && options.method === "PUT"
    ),
  );
  assert.deepEqual(JSON.parse(settingsPut[1].body), {
    max_price_per_hour: 1.25,
    min_vram_gb: 48,
  });
});


test("Search persists visible settings failure prevents offer request", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app } = captureHost();
  const calls = [];
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      if (options.method === "PUT") {
        return jsonResponse(
          { error: "settings rejected" },
          { ok: false, status: 500 },
        );
      }
      return jsonResponse(settingsPayload({ configured: true }));
    }
    if (endpoint === "/cloud-run/api/captures") {
      return jsonResponse({
        capture_id: "capture-1",
        prompt_digest: "d".repeat(64),
        status: "captured",
      });
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      assert.fail("offer search must not run after settings save failure");
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  }, {}, { app, api });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();
  await document.getElementById("cloud-run-search").click();

  assert.equal(
    calls.filter(([endpoint]) => endpoint === "/cloud-run/api/offers").length,
    0,
  );
  assert.match(
    document.getElementById("cloud-run-status").textContent,
    /settings could not be saved/i,
  );
});


test("paid review defaults to manual destruction with one provider create", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app, localSubmissions } = captureHost();
  const calls = [];
  const timers = [];
  const responses = async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      return jsonResponse(settingsPayload({ configured: true }));
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      return jsonResponse({
        offers: [{
          included: true,
          readiness: readinessPayload(),
          offer_id: "42",
          gpu_name: "<RTX 4090>",
          gpu_ram_gb: 24,
          dph_total: 0.5,
          reliability: 0.99,
          inet_down_mbps: 1000,
          disk_bw_mbps: 900,
          inet_down_cost: 0.01,
          inet_up_cost: 0.02,
          estimated_transfer_seconds: 235,
        }],
      });
    }
    if (endpoint === "/cloud-run/api/sessions") {
      return jsonResponse(manualQuoteSession());
    }
    if (endpoint === "/cloud-run/api/sessions/session-1/confirm") {
      return jsonResponse(manualQuoteSession("creating"));
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  };
  const browserWindow = {
    crypto: { randomUUID: () => "session-key" },
    setTimeout(callback) {
      timers.push(callback);
      return timers.length;
    },
    clearTimeout() {},
  };
  let captureCount = 0;
  mountCloudRun(document, responses, browserWindow, {
    app,
    api,
    async onCapture() {
      captureCount += 1;
      return { capture_id: `capture-${captureCount}` };
    },
  });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();
  assert.equal(document.getElementById("cloud-run-search").disabled, false);
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  assert.equal(
    document.getElementById("cloud-run-max-instance-creates"),
    null,
  );
  assert.equal(
    document.getElementById("cloud-run-create-limit-review").textContent,
    "Maximum provider creates for this review: 1. " +
      "Cloud Vast never rents a replacement automatically.",
  );
  const duration = document.getElementById("cloud-run-session-duration");
  assert.ok(duration);
  assert.deepEqual(
    duration.children.map((option) => option.value),
    ["none", "1800", "3600", "5400", "7200"],
  );
  assert.equal(
    duration.children[0].textContent,
    "No automatic limit — manual destruction",
  );
  assert.equal(duration.value, "none");
  await document.getElementById("cloud-run-review-session").click();

  const sessionRequest = calls.find(
    ([endpoint]) => endpoint === "/cloud-run/api/sessions",
  );
  assert.deepEqual(JSON.parse(sessionRequest[1].body), {
    preflight_id: "preflight-1",
    offer_id: "42",
    idempotency_key: "session-key",
    deadline: { mode: "none", duration_seconds: null },
    max_instance_creates: 1,
    estimate_digest: "d".repeat(64),
  });
  assert.match(
    document.getElementById("cloud-run-dependency-console").textContent,
    /no automatic limit/,
  );
  assert.match(
    document.getElementById("cloud-run-dependency-console").textContent,
    /billing continues until verified destruction/i,
  );
  assert.match(
    document.getElementById("cloud-run-dependency-console").textContent,
    /Maximum total instance creates: 1/,
  );
  assert.match(
    document.getElementById("cloud-run-offers").textContent,
    /<RTX 4090>/,
  );
  for (const expected of [
    "1000 Mbps download",
    "900 MB/s disk",
    "target download class",
    "theoretical transfer ≈ 4 minutes",
    "actual startup can be longer",
  ]) {
    assert.ok(
      document.getElementById("cloud-run-offers").textContent.includes(expected),
      expected,
    );
  }
  assert.equal(document.querySelector("script"), null);

  await document.getElementById("cloud-run-confirm-session").click();

  assert.equal(localSubmissions.length, 0);
  assert.equal(captureCount, 2);
  const confirmRequest = calls.find(
    ([endpoint]) => endpoint === "/cloud-run/api/sessions/session-1/confirm",
  );
  assert.deepEqual(JSON.parse(confirmRequest[1].body), {
    idempotency_key: "session-key",
    estimate_digest: "d".repeat(64),
    accepted_longer_estimate: false,
    preflight_id: "preflight-1",
  });
  assert.ok(
    calls.some(
      ([endpoint]) =>
        endpoint === "/cloud-run/api/sessions/session-1/confirm",
    ),
  );
  assert.ok(calls.every(([endpoint]) => !endpoint.includes("/quotes")));
  assert.ok(calls.every(([endpoint]) => !endpoint.includes("/attempts/")));
  assert.ok(timers.length >= 1);
});


test("listed finite session duration remains available", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app } = captureHost();
  const calls = [];
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      return jsonResponse(settingsPayload({ configured: true }));
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      return jsonResponse({
        offers: [{
          included: true,
          readiness: readinessPayload(),
          offer_id: "42",
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.5,
          reliability: 0.99,
        }],
      });
    }
    if (endpoint === "/cloud-run/api/sessions") {
      return jsonResponse(quoteSession());
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  }, {
    crypto: { randomUUID: () => "session-key" },
  }, {
    app,
    api,
    async onCapture() {
      return { capture_id: "capture-1" };
    },
  });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  document.getElementById("cloud-run-session-duration").value = "5400";
  await document.getElementById("cloud-run-review-session").click();

  const sessionRequest = calls.find(
    ([endpoint]) => endpoint === "/cloud-run/api/sessions",
  );
  assert.deepEqual(JSON.parse(sessionRequest[1].body).deadline, {
    mode: "finite",
    duration_seconds: 5_400,
  });
});


test("forged session duration is rejected instead of using a fallback", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app } = captureHost();
  const calls = [];
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      return jsonResponse(settingsPayload({ configured: true }));
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      return jsonResponse({
        offers: [{
          included: true,
          readiness: readinessPayload(),
          offer_id: "42",
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.5,
          reliability: 0.99,
        }],
      });
    }
    if (endpoint === "/cloud-run/api/sessions") {
      return jsonResponse(quoteSession());
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  }, {
    crypto: { randomUUID: () => "session-key" },
  }, {
    app,
    api,
    async onCapture() {
      return { capture_id: "capture-1" };
    },
  });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  const duration = document.getElementById("cloud-run-session-duration");
  assert.ok(duration);
  duration.value = "1801";
  await document.getElementById("cloud-run-review-session").click();

  assert.equal(
    calls.filter(([endpoint]) => endpoint === "/cloud-run/api/sessions").length,
    0,
  );
  assert.match(
    document.getElementById("cloud-run-status").textContent,
    /Choose manual destruction or one of the listed session durations/,
  );
});


test("ambiguous active session blocks search review and confirm after preflight", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app } = captureHost();
  const calls = [];
  const ambiguous = quoteSession("reconciling_create", {
    session_id: "session-ambiguous",
    rental_outcome: "unknown",
    instance_id: null,
    can_search_offers: false,
    can_destroy: true,
    billing_may_continue: true,
  });
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      return jsonResponse(settingsPayload({
        configured: true,
        active_sessions: [ambiguous],
      }));
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      return jsonResponse({ offers: [] });
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  }, {
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  }, {
    app,
    api,
    async onCapture() {
      return { capture_id: "capture-1" };
    },
  });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();

  assert.equal(document.getElementById("cloud-run-search").disabled, true);
  assert.equal(document.getElementById("cloud-run-review-session").disabled, true);
  assert.equal(document.getElementById("cloud-run-confirm-session").disabled, true);
  assert.equal(document.getElementById("cloud-run-confirm-session").hidden, true);
  assert.match(
    document.getElementById("cloud-run-preview-banner").className,
    /cloud-run-danger/,
  );
  await document.getElementById("cloud-run-search").click();
  assert.equal(
    calls.filter(([endpoint]) => endpoint === "/cloud-run/api/offers").length,
    0,
  );
});


test("reload chooses active or unknown state before recent absent history", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const oldActive = quoteSession("ready", {
    session_id: "session-old-active",
    rental_outcome: "active",
    can_search_offers: false,
    can_destroy: true,
    billing_may_continue: true,
    updated_at: 1_100,
  });
  const newestUnknown = quoteSession("reconciling_create", {
    session_id: "session-newest-unknown",
    rental_outcome: "unknown",
    instance_id: null,
    can_search_offers: false,
    can_destroy: true,
    billing_may_continue: true,
    updated_at: 1_200,
  });
  const recentAbsent = quoteSession("failed", {
    session_id: "session-recent-absent",
    rental_outcome: "absent",
    instance_id: null,
    can_search_offers: true,
    can_destroy: false,
    billing_may_continue: false,
    updated_at: 1_300,
  });
  mountCloudRun(document, async () => jsonResponse(settingsPayload({
    active_sessions: [oldActive, newestUnknown],
    recent_sessions: [recentAbsent],
  })), {
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  });

  await document.getElementById("cloud-run-button").click();

  const text = document.getElementById("cloud-run-dependency-console").textContent;
  assert.match(text, /session-newest-unknown/);
  assert.doesNotMatch(text, /session-recent-absent/);
});


test("reload selects a billable safety card without rental outcome", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const fallback = {
    session_id: "session-fallback",
    instance_id: "46741738",
    status: "failed",
    billing_may_continue: true,
    can_destroy: true,
    rate: 0.73,
    error: "Active session details are temporarily unavailable.",
  };
  mountCloudRun(document, async () => jsonResponse(settingsPayload({
    active_sessions: [fallback],
  })), {
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  });

  await document.getElementById("cloud-run-button").click();

  const console = document.getElementById("cloud-run-dependency-console");
  assert.match(console.textContent, /session-fallback/);
  assert.match(console.textContent, /\$0\.73\/h/);
  assert.equal(document.getElementById("cloud-run-destroy-gpu").hidden, false);
  assert.match(
    document.getElementById("cloud-run-preview-banner").className,
    /cloud-run-danger/,
  );
});


test("malformed later active entry cannot shadow a valid destroyable card", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const fallback = {
    session_id: "session-fallback",
    instance_id: "46741738",
    status: "failed",
    billing_may_continue: true,
    can_destroy: true,
    rate: 0.73,
    error: "Active session details are temporarily unavailable.",
  };
  mountCloudRun(document, async () => jsonResponse(settingsPayload({
    active_sessions: [
      fallback,
      { billing_may_continue: true },
    ],
  })), {
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  });

  await document.getElementById("cloud-run-button").click();

  const console = document.getElementById("cloud-run-dependency-console");
  assert.match(console.textContent, /session-fallback/);
  assert.equal(document.getElementById("cloud-run-destroy-gpu").hidden, false);
});


test("reload renders the newest failed absent session without active billing banner", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const oldAbsent = quoteSession("failed", {
    session_id: "session-old-absent",
    rental_outcome: "absent",
    instance_id: null,
    can_search_offers: true,
    can_destroy: false,
    billing_may_continue: false,
    updated_at: 1_100,
  });
  const newestAbsent = quoteSession("failed", {
    session_id: "session-newest-absent",
    rental_outcome: "absent",
    instance_id: null,
    can_search_offers: true,
    can_destroy: false,
    billing_may_continue: false,
    updated_at: 1_200,
  });
  mountCloudRun(document, async () => jsonResponse(settingsPayload({
    active_sessions: [],
    recent_sessions: [oldAbsent, newestAbsent],
  })));

  await document.getElementById("cloud-run-button").click();

  const text = document.getElementById("cloud-run-dependency-console").textContent;
  const banner = document.getElementById("cloud-run-preview-banner");
  assert.match(text, /session-newest-absent/);
  assert.doesNotMatch(text, /session-old-absent/);
  assert.doesNotMatch(banner.className, /cloud-run-danger/);
  assert.match(banner.textContent, /no longer bills|No Vast billing is active/i);
  assert.doesNotMatch(banner.textContent, /billing ends only after/);
});


test("verified absence requires a fresh manual offer review and idempotency key", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const { api, app } = captureHost();
  const calls = [];
  let sessionReviews = 0;
  let offerSearches = 0;
  let uuidCount = 0;
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if (endpoint === "/cloud-run/api/settings") {
      return jsonResponse(settingsPayload({ configured: true }));
    }
    if (endpoint === "/cloud-run/api/preflights") {
      return jsonResponse(preflightPayload());
    }
    if (endpoint === "/cloud-run/api/offers") {
      offerSearches += 1;
      return jsonResponse({
        offers: [{
          included: true,
          readiness: readinessPayload(),
          offer_id: String(40 + offerSearches),
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.5,
          reliability: 0.99,
        }],
      });
    }
    if (endpoint === "/cloud-run/api/sessions") {
      sessionReviews += 1;
      return jsonResponse(quoteSession("offer_selected", {
        session_id: `session-${sessionReviews}`,
        offer: {
          ...quoteSession().offer,
          offer_id: String(40 + sessionReviews),
        },
      }));
    }
    if (endpoint === "/cloud-run/api/sessions/session-1/confirm") {
      return jsonResponse(quoteSession("failed", {
        session_id: "session-1",
        rental_outcome: "absent",
        instance_id: null,
        can_search_offers: true,
        can_destroy: false,
        billing_may_continue: false,
      }));
    }
    if (endpoint === "/cloud-run/api/sessions/session-2/confirm") {
      return jsonResponse(quoteSession("creating", {
        session_id: "session-2",
      }));
    }
    throw new Error(`unexpected endpoint: ${endpoint}`);
  }, {
    crypto: {
      randomUUID() {
        uuidCount += 1;
        return `session-key-${uuidCount}`;
      },
    },
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  }, {
    app,
    api,
    async onCapture() {
      return { capture_id: "capture-1" };
    },
  });

  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-preflight").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  await document.getElementById("cloud-run-review-session").click();
  await document.getElementById("cloud-run-confirm-session").click();

  assert.equal(offerSearches, 1);
  assert.equal(sessionReviews, 1);
  assert.equal(uuidCount, 1);
  assert.equal(document.getElementById("cloud-run-offers").children.length, 0);
  assert.equal(document.getElementById("cloud-run-review-session").disabled, true);
  assert.equal(document.getElementById("cloud-run-search").disabled, false);

  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  await document.getElementById("cloud-run-review-session").click();
  await document.getElementById("cloud-run-confirm-session").click();

  const reviewBodies = calls
    .filter(([endpoint]) => endpoint === "/cloud-run/api/sessions")
    .map(([, options]) => JSON.parse(options.body));
  assert.deepEqual(
    reviewBodies.map((body) => body.idempotency_key),
    ["session-key-1", "session-key-2"],
  );
  assert.equal(offerSearches, 2);
  assert.equal(sessionReviews, 2);
  assert.equal(uuidCount, 2);
});


test("billing banner is normal for ready active and red only for unknown or residual failure", async () => {
  const cases = [
    {
      session: quoteSession("ready", {
        rental_outcome: "active",
        can_search_offers: false,
        can_destroy: true,
        billing_may_continue: true,
      }),
      red: false,
    },
    {
      session: quoteSession("reconciling_create", {
        rental_outcome: "unknown",
        instance_id: null,
        can_search_offers: false,
        can_destroy: true,
        billing_may_continue: true,
      }),
      red: true,
    },
    {
      session: quoteSession("failed", {
        rental_outcome: "active",
        can_search_offers: false,
        can_destroy: true,
        billing_may_continue: true,
        residual_inventory: ["88"],
      }),
      red: true,
    },
  ];

  for (const { session, red } of cases) {
    const document = new FakeDocument();
    mountLocalRunButton(document);
    mountCloudRun(document, async () => jsonResponse(settingsPayload({
      active_sessions: [session],
    })), {
      setTimeout() {
        return 1;
      },
      clearTimeout() {},
    });
    await document.getElementById("cloud-run-button").click();
    const banner = document.getElementById("cloud-run-preview-banner");
    assert.equal(/cloud-run-danger/.test(banner.className), red);
    if (!red) {
      assert.match(banner.textContent, /Paid Vast\.ai session/);
      assert.doesNotMatch(banner.textContent, /Warning/);
    }
  }
});


test("settings remain write-only and clear the password field", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const calls = [];
  mountCloudRun(document, async (endpoint, options = {}) => {
    calls.push([endpoint, options]);
    if ((options.method ?? "GET") === "PUT") {
      return jsonResponse(settingsPayload({ configured: true }));
    }
    return jsonResponse(settingsPayload());
  });
  await document.getElementById("cloud-run-button").click();
  document.getElementById("cloud-run-api-key").value = "write-only-key";
  document.getElementById("cloud-run-max-price").value = "0.75";
  document.getElementById("cloud-run-min-vram").value = "24";

  await document.getElementById("cloud-run-save-settings").click();

  const request = calls.find(([, options]) => options.method === "PUT");
  assert.deepEqual(JSON.parse(request[1].body), {
    max_price_per_hour: 0.75,
    min_vram_gb: 24,
    api_key: "write-only-key",
  });
  assert.equal(document.getElementById("cloud-run-api-key").value, "");
  assert.equal(
    document.getElementById("cloud-run-configured").textContent,
    "Vast API key is configured.",
  );
  assert.ok(
    !document.getElementById("cloud-run-modal").textContent.includes(
      "write-only-key",
    ),
  );
});


test("backend failure leaves the dialog and local controls usable", async () => {
  const document = new FakeDocument();
  const { queueButton } = mountLocalRunButton(document);
  mountCloudRun(document, async () => {
    throw new Error("offline");
  });

  await document.getElementById("cloud-run-button").click();

  assert.equal(document.getElementById("cloud-run-modal").open, true);
  assert.equal(queueButton.textContent, "Run");
  assert.match(
    document.getElementById("cloud-run-status").textContent,
    /capture is unavailable/,
  );
});


test("waits for a late local Run and reattaches after actionbar replacement", () => {
  const document = new FakeDocument();
  const observers = [];
  class MutationObserver {
    constructor(callback) {
      this.callback = callback;
      observers.push(this);
    }

    observe(target, options) {
      this.target = target;
      this.options = options;
    }
  }
  const browserWindow = { MutationObserver };
  const launcher = mountCloudRun(
    document,
    async () => jsonResponse(settingsPayload()),
    browserWindow,
  );
  assert.equal(launcher.isConnected, false);
  assert.equal(observers.length, 1);
  assert.deepEqual(observers[0].options, {
    childList: true,
    subtree: true,
  });

  const first = mountLocalRunButton(document);
  observers[0].callback();
  assert.deepEqual(first.actionbar.children, [
    first.queueGroup,
    launcher,
    first.agentPanel,
  ]);

  first.actionbar.remove();
  const replacement = mountLocalRunButton(document);
  observers[0].callback();
  assert.deepEqual(replacement.actionbar.children, [
    replacement.queueGroup,
    launcher,
    replacement.agentPanel,
  ]);
  assert.equal(observers.length, 1);
});


test("Extensions command remains usable when the actionbar is absent", async () => {
  const document = new FakeDocument();
  let extension = null;
  const app = {
    registerExtension(value) {
      extension = value;
    },
    async queuePrompt() {
      throw new Error("capture unavailable");
    },
  };
  const api = {
    fetchApi: async (endpoint) => jsonResponse(
      endpoint === "/cloud-run/api/desktop-context"
        ? { role: "local" }
        : settingsPayload(),
    ),
    async queuePrompt() {},
  };
  const browserWindow = {
    comfyAPI: { app: { app }, api: { api } },
    setTimeout() {},
  };

  registerCloudRunWhenReady(browserWindow, document);
  await extension.init();
  await extension.setup();
  await extension.commands[0].function();

  assert.equal(document.getElementById("cloud-run-modal").open, true);
  assert.equal(document.getElementById("cloud-run-button"), null);
});


test("offer rendering treats provider strings as inert text", async () => {
  const document = new FakeDocument();
  const container = document.createElement("div");
  document.body.appendChild(container);
  let selected = null;

  renderOffers(document, container, [{
    offer_id: "42",
    gpu_name: "<script>unsafe()</script>",
    gpu_ram_gb: 24,
    dph_total: 0.5,
    reliability: 0.99,
    dlperf: 72.5,
  }], (offer) => {
    selected = offer;
  });

  assert.match(container.textContent, /<script>unsafe\(\)<\/script>/);
  assert.match(container.textContent, /DLPerf 72\.5/);
  assert.equal(container.querySelector("script"), null);
  await container.querySelector("input").click();
  assert.equal(selected.offer_id, "42");
});


test("offer rendering marks missing connection metrics unavailable", () => {
  const document = new FakeDocument();
  const container = document.createElement("div");
  document.body.appendChild(container);

  renderOffers(document, container, [{
    offer_id: "42",
    gpu_name: "RTX 4090",
    gpu_ram_gb: 24,
    dph_total: 0.5,
    reliability: 0.99,
    inet_down_mbps: Infinity,
    disk_bw_mbps: NaN,
    inet_down_cost: Infinity,
    inet_up_cost: NaN,
    estimated_transfer_seconds: Infinity,
  }], () => {});

  assert.match(container.textContent, /download unavailable/);
  assert.match(container.textContent, /DLPerf unavailable/);
  assert.match(container.textContent, /disk speed unavailable/);
  assert.match(container.textContent, /download class unavailable/);
  assert.match(container.textContent, /theoretical transfer ≈ unavailable/);
  assert.doesNotMatch(container.textContent, /NaN|Infinity/);
});


test("offer rendering limits the shortlist and explains excluded offers", async () => {
  const document = new FakeDocument();
  const container = document.createElement("div");
  document.body.appendChild(container);
  const selected = [];
  const included = Array.from({ length: 7 }, (_, index) => ({
    included: true,
    included_reasons: ["workflow_requirements", "price_cap"],
    excluded_reasons: [],
    readiness: readinessPayload(),
    offer_id: String(index + 1),
    gpu_name: `GPU ${index + 1}`,
    gpu_ram_gb: 24,
    dph_total: 0.5,
    reliability: 0.99,
    inet_down_mbps: 1000,
    disk_bw_mbps: 900,
    dlperf: 80,
  }));
  renderOffers(document, container, [
    ...included,
    {
      ...included[0],
      included: false,
      included_reasons: [],
      excluded_reasons: ["vram", "price"],
      offer_id: "99",
      gpu_name: "Excluded GPU",
    },
  ], (offer) => selected.push(offer.offer_id));

  assert.equal(container.querySelectorAll("input").length, 5);
  assert.match(container.textContent, /meets workflow VRAM and disk/);
  assert.match(container.textContent, /1 excluded offer/);
  assert.match(container.textContent, /VRAM below the workflow minimum/);
  assert.match(container.textContent, /price above the hard cap/);
  await document.getElementById("cloud-run-offer-0").click();
  assert.deepEqual(selected, ["1"]);
});


test("frontend source has no legacy, browser-secret, or provider URL surface", async () => {
  const sources = await Promise.all([
    "../../web/js/canvas-adapter.js",
    "../../web/js/comfyui-vast.js",
    "../../web/js/cloud-run.js",
    "../../web/js/cloud-run-api.js",
    "../../web/js/session-console.js",
  ].map((relative) => readFile(
    new URL(relative, import.meta.url),
    "utf8",
  )));
  const source = sources.join("\n");
  for (const snippet of [
    ".innerHTML",
    ".outerHTML",
    "insertAdjacentHTML",
    "localStorage",
    "sessionStorage",
    "window.location",
    "console.vast.ai",
    "http://",
    "https://",
    "/cloud-run/api/quotes",
    "/cloud-run/api/attempts/",
  ]) {
    assert.equal(source.includes(snippet), false, snippet);
  }
  assert.doesNotMatch(
    source,
    /\bconsole\.(?:debug|error|info|log|warn)\s*\(/,
  );
  assert.ok(source.includes("createCloudRunApi"));
  assert.ok(source.includes("createSession"));
  assert.ok(!source.includes("Run current canvas on this GPU"));
  assert.ok(!source.includes("Run Vast"));
  assert.ok(!source.includes("Cloud Run"));
  assert.ok(source.includes("Destroy GPU — stop all Vast billing"));
  assert.ok(source.includes("MutationObserver"));
  assert.ok(source.includes("#cloud-run-button:focus-visible"));
  assert.ok(source.includes("@media (max-width: 480px)"));
  assert.equal(source.includes("position: fixed"), false);
});
