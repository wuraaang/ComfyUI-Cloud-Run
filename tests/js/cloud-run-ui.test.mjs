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
    ...overrides,
  };
}


function quoteSession(status = "offer_selected", overrides = {}) {
  return {
    session_id: "session-1",
    status,
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
    billing_may_continue: false,
    emergency_action: null,
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
  assert.equal(launcher.textContent, "☁ Cloud Run");
  assert.equal(
    launcher.getAttribute("title"),
    "Launch on Vast.ai — paid GPU rental",
  );

  await launcher.click();

  assert.equal(document.getElementById("cloud-run-modal").open, true);
  assert.equal(requests[0][0], "/cloud-run/api/settings");
  assert.equal(document.getElementById("cloud-run-api-key").type, "password");
  assert.equal(
    document.getElementById("cloud-run-review-session").textContent,
    "Review paid rental",
  );
  assert.equal(
    document.getElementById("cloud-run-next-job").textContent,
    "Run current canvas on this GPU",
  );
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
    { id: "vast-cloud-run.open", label: "Cloud Run" },
  ]);
  assert.deepEqual(extension.menuCommands, [{
    path: ["Extensions", "Vast Cloud Run"],
    commands: ["vast-cloud-run.open"],
  }]);

  extension.setup();
  await extension.commands[0].function();

  assert.deepEqual(
    requests.map(([endpoint]) => endpoint),
    ["/cloud-run/api/settings", "/cloud-run/api/captures"],
  );
  assert.equal(localSubmissions.length, 0);
  assert.equal(api.fetchApi, originalFetch);
  assert.equal(api.queuePrompt, originalQueue);
});


test("capture preflight offer and paid review enforce instance creates", async () => {
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
          offer_id: "42",
          gpu_name: "<RTX 4090>",
          gpu_ram_gb: 24,
          dph_total: 0.5,
          reliability: 0.99,
          inet_down_cost: 0.01,
          inet_up_cost: 0.02,
        }],
      });
    }
    if (endpoint === "/cloud-run/api/sessions") {
      return jsonResponse(quoteSession());
    }
    if (endpoint === "/cloud-run/api/sessions/session-1/confirm") {
      return jsonResponse(quoteSession("creating"));
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
  const createLimit = document.getElementById(
    "cloud-run-max-instance-creates",
  );
  assert.deepEqual(
    createLimit.children.map((option) => option.value),
    ["1", "2"],
  );
  assert.equal(createLimit.value, "1");
  await document.getElementById("cloud-run-review-session").click();

  const sessionRequest = calls.find(
    ([endpoint]) => endpoint === "/cloud-run/api/sessions",
  );
  assert.deepEqual(JSON.parse(sessionRequest[1].body), {
    preflight_id: "preflight-1",
    offer_id: "42",
    idempotency_key: "session-key",
    deadline: { mode: "finite", duration_seconds: 7_200 },
    max_instance_creates: 1,
  });
  assert.match(
    document.getElementById("cloud-run-dependency-console").textContent,
    /approximately \$1\.00 active\/storage/,
  );
  assert.match(
    document.getElementById("cloud-run-dependency-console").textContent,
    /Maximum total instance creates: 1/,
  );
  assert.match(
    document.getElementById("cloud-run-offers").textContent,
    /<RTX 4090>/,
  );
  assert.equal(document.querySelector("script"), null);

  createLimit.value = "2";
  await document.getElementById("cloud-run-review-session").click();
  const selectedLimitRequest = calls.filter(
    ([endpoint]) => endpoint === "/cloud-run/api/sessions",
  )[1];
  assert.equal(
    JSON.parse(selectedLimitRequest[1].body).max_instance_creates,
    2,
  );

  await document.getElementById("cloud-run-confirm-session").click();

  assert.equal(localSubmissions.length, 0);
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
    fetchApi: async () => jsonResponse(settingsPayload()),
    async queuePrompt() {},
  };
  const browserWindow = {
    comfyAPI: { app: { app }, api: { api } },
    setTimeout() {},
  };

  registerCloudRunWhenReady(browserWindow, document);
  extension.setup();
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
  }], (offer) => {
    selected = offer;
  });

  assert.match(container.textContent, /<script>unsafe\(\)<\/script>/);
  assert.equal(container.querySelector("script"), null);
  await container.querySelector("input").click();
  assert.equal(selected.offer_id, "42");
});


test("frontend source has no legacy, browser-secret, or provider URL surface", async () => {
  const sources = await Promise.all([
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
  assert.ok(source.includes("Run current canvas on this GPU"));
  assert.ok(source.includes("Destroy GPU — stop all Vast billing"));
  assert.ok(source.includes("MutationObserver"));
  assert.ok(source.includes("#cloud-run-button:focus-visible"));
  assert.ok(source.includes("@media (max-width: 480px)"));
  assert.equal(source.includes("position: fixed"), false);
});
