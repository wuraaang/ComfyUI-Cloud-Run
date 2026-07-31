import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  attemptPresentation,
  mountCloudRun,
  registerCloudRunWhenReady,
} from "../../web/js/cloud-run.js";
import { FakeDocument } from "./fake-dom.mjs";


function settingsResponse() {
  return {
    ok: true,
    status: 200,
    async json() {
      return {
        configured: false,
        max_price_per_hour: 1,
        min_vram_gb: 16,
        official_template_id: "027fba7753c024be019030fb42aed900",
        official_template_name: "Official ComfyUI",
        lifecycle_enabled: true,
      };
    },
  };
}


function jsonResponse(payload, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    async json() {
      return payload;
    },
  };
}

function attemptPayload(status, overrides = {}) {
  return {
    attempt_id: "attempt-1",
    status,
    offer: {
      offer_id: "42",
      gpu_name: "RTX 4090",
      gpu_ram_gb: 24,
      dph_total: 0.42,
      reliability: 0.99,
      max_price_per_hour: 0.55,
      expires_at: 160,
    },
    instance_id: null,
    ready_url: null,
    retry_count: 0,
    cancel_requested: false,
    error: null,
    billing_may_continue: false,
    emergency_action: null,
    official_template_id: "027fba7753c024be019030fb42aed900",
    official_template_name: "Official ComfyUI",
    ...overrides,
  };
}

function mountLocalRunButton(document) {
  const actionbar = document.createElement("div");
  const queueGroup = document.createElement("div");
  const queueButton = document.createElement("button");
  queueButton.setAttribute("data-testid", "queue-button");
  queueButton.textContent = "Run";
  queueGroup.appendChild(queueButton);
  actionbar.appendChild(queueGroup);
  document.body.appendChild(actionbar);
  return { actionbar, queueButton, queueGroup };
}


test("mounts Cloud Run immediately after the local Run group", async () => {
  const document = new FakeDocument();
  const { actionbar, queueGroup } = mountLocalRunButton(document);
  const requests = [];
  const fetchImpl = async (...args) => {
    requests.push(args);
    return settingsResponse();
  };

  mountCloudRun(document, fetchImpl);

  const launcher = document.getElementById("cloud-run-button");
  assert.ok(launcher);
  assert.equal(launcher.textContent, "☁ Cloud Run");
  assert.equal(launcher.getAttribute("data-testid"), "cloud-run-button");
  assert.equal(launcher.hidden, false);
  assert.notEqual(launcher.style.display, "none");
  assert.equal(launcher.isConnected, true);
  assert.deepEqual(actionbar.children, [queueGroup, launcher]);
  assert.equal(
    launcher.getAttribute("title"),
    "Launch on Vast.ai — paid GPU rental",
  );
  assert.equal(launcher.getAttribute("aria-label"), "Cloud Run on Vast.ai");

  const dialog = document.getElementById("cloud-run-modal");
  assert.ok(dialog);
  assert.equal(dialog.open, false);

  await launcher.click();

  assert.equal(dialog.open, true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], "/cloud-run/api/settings");
  assert.equal(
    document.getElementById("cloud-run-preview-banner").textContent,
    "Paid Vast.ai rental — nothing is created until explicit confirmation.",
  );
  assert.equal(document.getElementById("cloud-run-api-key").type, "password");
  assert.ok(document.getElementById("cloud-run-max-price"));
  assert.ok(document.getElementById("cloud-run-min-vram"));
  assert.equal(
    document.getElementById("cloud-run-save-settings").textContent,
    "Save settings",
  );
  assert.equal(
    document.getElementById("cloud-run-search").textContent,
    "Search Vast GPUs",
  );
  assert.equal(
    document.getElementById("cloud-run-preview-selection").textContent,
    "Review paid rental",
  );
});

test("opens from the keyboard without changing the local Run button", async () => {
  const document = new FakeDocument();
  const { queueButton } = mountLocalRunButton(document);
  const requests = [];
  mountCloudRun(document, async (...args) => {
    requests.push(args);
    return settingsResponse();
  });
  const launcher = document.getElementById("cloud-run-button");
  const event = {
    type: "keydown",
    key: "Enter",
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };

  await launcher.dispatchEvent(event);

  assert.equal(event.defaultPrevented, true);
  assert.equal(document.getElementById("cloud-run-modal").open, true);
  assert.equal(requests.length, 1);
  assert.equal(queueButton.textContent, "Run");
});


test("registers through the pinned ComfyUI extension API and mounts during setup", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const apiRequests = [];
  let extension = null;
  const app = {
    registerExtension(specification) {
      extension = specification;
    },
  };
  const api = {
    async fetchApi(...args) {
      apiRequests.push(args);
      return settingsResponse();
    },
  };
  const browserWindow = {
    comfyAPI: {
      app: { app },
      api: { api },
    },
    setTimeout() {
      assert.fail("ready ComfyUI APIs must not schedule a retry");
    },
  };

  const registered = registerCloudRunWhenReady(browserWindow, document);

  assert.equal(registered, true);
  assert.ok(extension);
  assert.equal(extension.name, "comfyui-cloud-run.lifecycle");
  assert.equal(typeof extension.setup, "function");
  assert.deepEqual(
    extension.commands.map(({ id, label }) => ({ id, label })),
    [{ id: "vast-cloud-run.open", label: "Cloud Run" }],
  );
  assert.deepEqual(extension.menuCommands, [
    {
      path: ["Extensions", "Vast Cloud Run"],
      commands: ["vast-cloud-run.open"],
    },
  ]);

  await extension.setup();
  const launcher = document.getElementById("cloud-run-button");
  assert.ok(launcher);
  assert.equal(launcher.textContent, "☁ Cloud Run");

  await extension.commands[0].function();
  assert.equal(apiRequests.length, 1);
  assert.equal(apiRequests[0][0], "/cloud-run/api/settings");
  assert.equal(document.getElementById("cloud-run-modal").open, true);
});


test("Cloud Run captures the official queue payload before the console callback", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const captures = [];
  const localSubmissions = [];
  const api = {
    async queuePrompt(number, data) {
      localSubmissions.push([number, data]);
      return { prompt_id: "local", node_errors: {} };
    },
  };
  const originalQueuePrompt = api.queuePrompt;
  const app = {
    async queuePrompt(number, batchCount) {
      assert.equal(batchCount, 1);
      await api.queuePrompt(
        number,
        {
          workflow: {
            version: 1,
            extra: { frontendVersion: "1.47.10" },
            nodes: [{ id: 7 }],
          },
          output: {
            "7": {
              class_type: "KSampler",
              inputs: { seed: 12 },
            },
          },
        },
        { previewMethod: "latent2rgb" },
      );
    },
  };

  mountCloudRun(
    document,
    async () => settingsResponse(),
    {},
    {
      app,
      api,
      onCapture(capture) {
        captures.push(capture);
      },
    },
  );
  await document.getElementById("cloud-run-button").click();

  assert.equal(captures.length, 1);
  assert.equal(captures[0].output["7"].inputs.seed, 12);
  assert.equal(captures[0].queue_options.preview_method, "latent2rgb");
  assert.deepEqual(localSubmissions, []);
  assert.strictEqual(api.queuePrompt, originalQueuePrompt);
  assert.equal(
    document.getElementById("cloud-run-status").textContent,
    "Current canvas captured without local execution.",
  );
});

test("waits for a late local Run button and injects the launcher only once", async () => {
  const document = new FakeDocument();
  let extension = null;
  let mutationCallback = null;
  let observedRoot = null;
  let disconnectCount = 0;
  const app = {
    registerExtension(specification) {
      extension = specification;
    },
  };
  const browserWindow = {
    comfyAPI: {
      app: { app },
      api: { api: { fetchApi: async () => settingsResponse() } },
    },
    MutationObserver: class {
      constructor(callback) {
        mutationCallback = callback;
      }

      observe(root) {
        observedRoot = root;
      }

      disconnect() {
        disconnectCount += 1;
      }
    },
    setTimeout() {
      assert.fail("ready ComfyUI APIs must not schedule a retry");
    },
  };

  registerCloudRunWhenReady(browserWindow, document);
  await extension.setup();

  assert.equal(document.getElementById("cloud-run-button"), null);
  assert.equal(observedRoot, document.body);
  assert.equal(typeof mutationCallback, "function");

  const { actionbar, queueGroup } = mountLocalRunButton(document);
  mutationCallback();

  const launcher = document.getElementById("cloud-run-button");
  assert.ok(launcher);
  assert.deepEqual(actionbar.children, [queueGroup, launcher]);
  assert.equal(disconnectCount, 0);

  await extension.setup();
  assert.deepEqual(actionbar.children, [queueGroup, launcher]);
});

test("reattaches the same launcher after the action bar is replaced", () => {
  const document = new FakeDocument();
  const first = mountLocalRunButton(document);
  let mutationCallback = null;
  let disconnectCount = 0;
  const browserWindow = {
    MutationObserver: class {
      constructor(callback) {
        mutationCallback = callback;
      }

      observe(root, options) {
        assert.equal(root, document.body);
        assert.deepEqual(options, { childList: true, subtree: true });
      }

      disconnect() {
        disconnectCount += 1;
      }
    },
  };

  const launcher = mountCloudRun(
    document,
    async () => settingsResponse(),
    browserWindow,
  );
  assert.equal(typeof mutationCallback, "function");
  assert.deepEqual(first.actionbar.children, [first.queueGroup, launcher]);

  first.actionbar.remove();
  const second = mountLocalRunButton(document);
  mutationCallback();

  assert.equal(launcher.isConnected, true);
  assert.deepEqual(second.actionbar.children, [second.queueGroup, launcher]);
  assert.equal(disconnectCount, 0);

  mutationCallback();
  assert.deepEqual(second.actionbar.children, [second.queueGroup, launcher]);
});

test("keeps the Extensions command usable when the action bar is unavailable", async () => {
  const document = new FakeDocument();
  let extension = null;
  const app = {
    registerExtension(specification) {
      extension = specification;
    },
  };
  const browserWindow = {
    comfyAPI: {
      app: { app },
      api: { api: { fetchApi: async () => settingsResponse() } },
    },
    setTimeout() {
      assert.fail("ready ComfyUI APIs must not schedule a retry");
    },
  };

  registerCloudRunWhenReady(browserWindow, document);
  await extension.setup();

  assert.equal(document.getElementById("cloud-run-button"), null);
  await extension.commands[0].function();
  assert.equal(document.getElementById("cloud-run-modal").open, true);
});

test("keeps the dialog usable when the Cloud Run backend is unavailable", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  mountCloudRun(document, async () => {
    throw new Error("synthetic backend outage");
  });

  await document.getElementById("cloud-run-button").click();

  assert.equal(document.getElementById("cloud-run-modal").open, true);
  assert.equal(
    document.getElementById("cloud-run-status").textContent,
    "Settings could not be loaded.",
  );
  assert.equal(document.getElementById("cloud-run-close").disabled, false);
});


test("saves settings with a write-only optional key and clears the password", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const requests = [];
  const responses = [
    settingsResponse(),
    jsonResponse({
      configured: true,
      max_price_per_hour: 0.55,
      min_vram_gb: 32,
      official_template_id: "027fba7753c024be019030fb42aed900",
      official_template_name: "Official ComfyUI",
      lifecycle_enabled: true,
    }),
    jsonResponse({
      configured: true,
      max_price_per_hour: 0.6,
      min_vram_gb: 48,
      official_template_id: "027fba7753c024be019030fb42aed900",
      official_template_name: "Official ComfyUI",
      lifecycle_enabled: true,
    }),
  ];
  const fetchImpl = async (...args) => {
    requests.push(args);
    return responses.shift();
  };
  mountCloudRun(document, fetchImpl);
  await document.getElementById("cloud-run-button").click();

  const keyInput = document.getElementById("cloud-run-api-key");
  const priceInput = document.getElementById("cloud-run-max-price");
  const vramInput = document.getElementById("cloud-run-min-vram");
  keyInput.value = "synthetic-value";
  priceInput.value = "0.55";
  vramInput.value = "32";

  await document.getElementById("cloud-run-save-settings").click();

  assert.equal(requests[1][0], "/cloud-run/api/settings");
  assert.equal(requests[1][1].method, "PUT");
  assert.deepEqual(JSON.parse(requests[1][1].body), {
    api_key: "synthetic-value",
    max_price_per_hour: 0.55,
    min_vram_gb: 32,
  });
  assert.equal(keyInput.value, "");
  assert.equal(
    document.getElementById("cloud-run-status").textContent,
    "Settings saved.",
  );

  priceInput.value = "0.6";
  vramInput.value = "48";
  await document.getElementById("cloud-run-save-settings").click();

  assert.deepEqual(JSON.parse(requests[2][1].body), {
    max_price_per_hour: 0.6,
    min_vram_gb: 48,
  });
});


test("searches and renders selectable remote offers as inert text", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const requests = [];
  const maliciousName = "<img src=x onerror=steal()>";
  const responses = [
    settingsResponse(),
    jsonResponse({
      offers: [
        {
          offer_id: 42,
          gpu_name: maliciousName,
          gpu_ram_gb: 24,
          dph_total: 0.42,
          reliability: 0.99,
        },
      ],
    }),
  ];
  const fetchImpl = async (...args) => {
    requests.push(args);
    return responses.shift();
  };
  mountCloudRun(document, fetchImpl);
  await document.getElementById("cloud-run-button").click();

  await document.getElementById("cloud-run-search").click();

  assert.equal(requests[1][0], "/cloud-run/api/offers");
  assert.equal(requests[1][1].method, "POST");
  const offers = document.getElementById("cloud-run-offers");
  assert.equal(offers.children.length, 1);
  assert.ok(offers.textContent.includes(maliciousName));
  assert.ok(offers.textContent.includes("24 GB"));
  assert.ok(offers.textContent.includes("$0.42/h"));
  assert.ok(offers.textContent.includes("99.0% reliability"));

  const selector = document.getElementById("cloud-run-offer-0");
  assert.ok(selector);
  assert.equal(selector.type, "radio");
  assert.equal(
    selector.getAttribute("data-testid"),
    "cloud-run-offer-select-0",
  );
  await selector.click();
  assert.equal(
    document.getElementById("cloud-run-preview-selection").disabled,
    false,
  );
});


test("server quote shows the exact paid confirmation before create", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const requests = [];
  const responses = [
    settingsResponse(),
    jsonResponse({
      offers: [
        {
          offer_id: 42,
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.42,
          reliability: 0.99,
        },
      ],
    }),
    jsonResponse({
      attempt_id: "attempt-1",
      status: "offer_selected",
      offer: {
        offer_id: "42",
        gpu_name: "RTX 4090",
        gpu_ram_gb: 24,
        dph_total: 0.42,
        reliability: 0.99,
        max_price_per_hour: 0.55,
        expires_at: 160,
      },
      instance_id: null,
      ready_url: null,
      retry_count: 0,
      cancel_requested: false,
      error: null,
      billing_may_continue: false,
      emergency_action: null,
      official_template_id: "027fba7753c024be019030fb42aed900",
      official_template_name: "Official ComfyUI",
    }),
  ];
  const fetchImpl = async (...args) => {
    requests.push(args);
    return responses.shift();
  };
  mountCloudRun(document, fetchImpl);
  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();

  await document.getElementById("cloud-run-preview-selection").click();

  assert.equal(requests.length, 3);
  assert.equal(requests[2][0], "/cloud-run/api/quotes");
  assert.equal(requests[2][1].method, "POST");
  const body = JSON.parse(requests[2][1].body);
  assert.equal(body.offer_id, 42);
  assert.equal(typeof body.idempotency_key, "string");
  assert.ok(body.idempotency_key.length > 0);
  const preview = document.getElementById("cloud-run-selection-preview");
  assert.ok(preview.textContent.includes("RTX 4090"));
  assert.ok(preview.textContent.includes("24 GB"));
  assert.ok(preview.textContent.includes("$0.42/h"));
  assert.ok(preview.textContent.includes("$0.55/h"));
  assert.ok(preview.textContent.includes("Offer: 42"));
  assert.ok(preview.textContent.includes("Official ComfyUI"));
  assert.ok(
    preview.textContent.includes("027fba7753c024be019030fb42aed900"),
  );
  assert.ok(preview.textContent.includes("No rental exists until you confirm"));
  assert.equal(
    document.getElementById("cloud-run-confirm").hidden,
    false,
  );
});

test("quote review displays the sanitized backend error", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const responses = [
    settingsResponse(),
    jsonResponse({
      offers: [
        {
          offer_id: 42,
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.42,
          reliability: 0.99,
        },
      ],
    }),
    jsonResponse(
      { error: "The selected Vast offer is no longer available." },
      { ok: false, status: 409 },
    ),
  ];

  mountCloudRun(document, async () => responses.shift());
  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  await document.getElementById("cloud-run-preview-selection").click();

  assert.equal(
    document.getElementById("cloud-run-status").textContent,
    "The selected Vast offer is no longer available.",
  );
});

test("quote review keeps the generic fallback for unsafe backend errors", async () => {
  const unsafeErrors = [
    {},
    { error: "   " },
    { error: 409 },
  ];

  for (const unsafeError of unsafeErrors) {
    const document = new FakeDocument();
    mountLocalRunButton(document);
    const responses = [
      settingsResponse(),
      jsonResponse({
        offers: [
          {
            offer_id: 42,
            gpu_name: "RTX 4090",
            gpu_ram_gb: 24,
            dph_total: 0.42,
            reliability: 0.99,
          },
        ],
      }),
      jsonResponse(unsafeError, { ok: false, status: 409 }),
    ];

    mountCloudRun(document, async () => responses.shift());
    await document.getElementById("cloud-run-button").click();
    await document.getElementById("cloud-run-search").click();
    await document.getElementById("cloud-run-offer-0").click();
    await document.getElementById("cloud-run-preview-selection").click();

    assert.equal(
      document.getElementById("cloud-run-status").textContent,
      "The selected offer could not be quoted.",
    );
  }
});

test("every server lifecycle state has an explicit safe presentation", () => {
  const expected = {
    idle: { poll: false },
    searching: { poll: true },
    offer_selected: { confirm: true, poll: false },
    confirming: { poll: true },
    creating: { cancel: true, poll: true },
    starting: { cancel: true, poll: true },
    cancel_requested: { poll: true },
    destroying: { poll: true },
    retrying: { cancel: true, poll: true },
    ready: { open: true, destroy: true, poll: false },
    cancelled: { poll: false },
    failed: { destroy: true, poll: false },
  };

  for (const [status, flags] of Object.entries(expected)) {
    const payload = attemptPayload(status, {
      instance_id: ["ready", "failed"].includes(status) ? "instance-9" : null,
      ready_url: status === "ready" ? "http://8.8.8.8:32100" : null,
      billing_may_continue: status === "failed",
      emergency_action:
        status === "failed"
          ? "Destroy Vast instance instance-9 immediately."
          : null,
    });
    const presentation = attemptPresentation(payload);
    assert.ok(presentation.message.length > 0, status);
    for (const key of ["confirm", "cancel", "open", "destroy", "poll"]) {
      assert.equal(Boolean(presentation[key]), Boolean(flags[key]), `${status}:${key}`);
    }
  }
});

test("confirmation reuses one idempotency key, polls to ready, opens, then destroys", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const requests = [];
  const timers = new Map();
  let nextTimer = 1;
  const browserWindow = {
    crypto: { randomUUID: () => "browser-idem-1" },
    setTimeout(callback) {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  const responses = [
    settingsResponse(),
    jsonResponse({
      offers: [
        {
          offer_id: 42,
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.42,
          reliability: 0.99,
        },
      ],
    }),
    jsonResponse(attemptPayload("offer_selected")),
    jsonResponse(
      attemptPayload("starting", { instance_id: "instance-9" }),
    ),
    jsonResponse(
      attemptPayload("ready", {
        instance_id: "instance-9",
        ready_url: "http://8.8.8.8:32100",
      }),
    ),
    jsonResponse(attemptPayload("cancelled")),
  ];
  const fetchImpl = async (...args) => {
    requests.push(args);
    return responses.shift();
  };

  mountCloudRun(document, fetchImpl, browserWindow);
  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  await document.getElementById("cloud-run-preview-selection").click();
  await document.getElementById("cloud-run-confirm").click();

  assert.equal(requests[2][0], "/cloud-run/api/quotes");
  assert.equal(
    JSON.parse(requests[2][1].body).idempotency_key,
    "browser-idem-1",
  );
  assert.equal(
    requests[3][0],
    "/cloud-run/api/attempts/attempt-1/confirm",
  );
  assert.equal(
    JSON.parse(requests[3][1].body).idempotency_key,
    "browser-idem-1",
  );
  assert.equal(timers.size, 1);
  const poll = [...timers.values()][0];
  timers.clear();
  await poll();

  assert.equal(
    requests[4][0],
    "/cloud-run/api/attempts/attempt-1",
  );
  const open = document.getElementById("cloud-run-open");
  assert.equal(open.hidden, false);
  assert.equal(open.getAttribute("href"), "http://8.8.8.8:32100");
  assert.equal(open.getAttribute("target"), "_blank");
  assert.equal(open.getAttribute("rel"), "noopener noreferrer");
  assert.equal(
    document.getElementById("cloud-run-destroy").hidden,
    false,
  );

  await document.getElementById("cloud-run-destroy").click();

  assert.equal(
    requests[5][0],
    "/cloud-run/api/attempts/attempt-1",
  );
  assert.equal(requests[5][1].method, "DELETE");
  assert.equal(
    document.getElementById("cloud-run-status").textContent,
    "Cancelled. Vast inventory is confirmed empty.",
  );
});

test("Cancel remains available during creation and polls until confirmed empty", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  const requests = [];
  const timers = new Map();
  let nextTimer = 1;
  const browserWindow = {
    crypto: { randomUUID: () => "browser-idem-cancel" },
    setTimeout(callback) {
      const id = nextTimer++;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  const responses = [
    settingsResponse(),
    jsonResponse({
      offers: [
        {
          offer_id: 42,
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.42,
          reliability: 0.99,
        },
      ],
    }),
    jsonResponse(attemptPayload("offer_selected")),
    jsonResponse(attemptPayload("creating")),
    jsonResponse(
      attemptPayload("cancel_requested", { cancel_requested: true }),
    ),
    jsonResponse(attemptPayload("cancelled")),
  ];
  const fetchImpl = async (...args) => {
    requests.push(args);
    return responses.shift();
  };

  mountCloudRun(document, fetchImpl, browserWindow);
  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  await document.getElementById("cloud-run-preview-selection").click();
  await document.getElementById("cloud-run-confirm").click();

  const cancel = document.getElementById("cloud-run-cancel");
  assert.equal(cancel.hidden, false);
  assert.equal(cancel.disabled, false);
  await cancel.click();
  assert.equal(
    requests[4][0],
    "/cloud-run/api/attempts/attempt-1/cancel",
  );
  assert.equal(requests[4][1].method, "POST");
  assert.equal(timers.size, 1);
  const poll = [...timers.values()][0];
  timers.clear();
  await poll();
  assert.equal(
    document.getElementById("cloud-run-status").textContent,
    "Cancelled. Vast inventory is confirmed empty.",
  );
});

test("mutable controls lock while a provider-changing request is in flight", async () => {
  const document = new FakeDocument();
  mountLocalRunButton(document);
  let releaseConfirmation;
  const confirmationResponse = new Promise((resolve) => {
    releaseConfirmation = resolve;
  });
  const responses = [
    settingsResponse(),
    jsonResponse({
      offers: [
        {
          offer_id: 42,
          gpu_name: "RTX 4090",
          gpu_ram_gb: 24,
          dph_total: 0.42,
          reliability: 0.99,
        },
      ],
    }),
    jsonResponse(attemptPayload("offer_selected")),
    confirmationResponse,
  ];
  const browserWindow = {
    crypto: { randomUUID: () => "browser-idem-lock" },
    setTimeout() {
      return 1;
    },
    clearTimeout() {},
  };
  const fetchImpl = async () => responses.shift();

  mountCloudRun(document, fetchImpl, browserWindow);
  await document.getElementById("cloud-run-button").click();
  await document.getElementById("cloud-run-search").click();
  await document.getElementById("cloud-run-offer-0").click();
  await document.getElementById("cloud-run-preview-selection").click();

  const confirming = document.getElementById("cloud-run-confirm").click();
  await Promise.resolve();
  assert.equal(document.getElementById("cloud-run-confirm").disabled, true);
  assert.equal(document.getElementById("cloud-run-search").disabled, true);
  assert.equal(document.getElementById("cloud-run-save-settings").disabled, true);
  releaseConfirmation(
    jsonResponse(
      attemptPayload("starting", { instance_id: "instance-9" }),
    ),
  );
  await confirming;
  assert.equal(document.getElementById("cloud-run-cancel").disabled, false);
});

test("residual failure keeps the instance ID and emergency destruction visible", async () => {
  const presentation = attemptPresentation(
    attemptPayload("failed", {
      instance_id: "instance-99",
      error: "Automatic cleanup could not be verified.",
      billing_may_continue: true,
      emergency_action:
        "Destroy Vast instance instance-99 in the Vast.ai console immediately.",
    }),
  );

  assert.equal(presentation.destroy, true);
  assert.ok(presentation.message.includes("instance-99"));
  assert.ok(presentation.message.includes("billing may continue"));
  assert.ok(presentation.message.includes("Vast.ai console"));
});


test("frontend source has no HTML, browser-storage, URL, console, or mutation credential path", async () => {
  const source = await readFile(
    new URL("../../web/js/cloud-run.js", import.meta.url),
    "utf8",
  );
  const forbiddenSnippets = [
    ".innerHTML",
    ".outerHTML",
    "insertAdjacentHTML",
    "localStorage",
    "sessionStorage",
    "URLSearchParams",
    "window.location",
    "console.",
    "console.vast.ai",
    "/" + "asks",
    "/" + "instances",
  ];
  for (const snippet of forbiddenSnippets) {
    assert.equal(source.includes(snippet), false, `forbidden snippet: ${snippet}`);
  }
  assert.ok(source.includes('const SETTINGS_ENDPOINT = "/cloud-run/api/settings"'));
  assert.ok(source.includes('const OFFERS_ENDPOINT = "/cloud-run/api/offers"'));
  assert.ok(source.includes('const QUOTES_ENDPOINT = "/cloud-run/api/quotes"'));
  assert.ok(source.includes("details.textContent"));
  assert.equal(source.includes("position: fixed"), false);
  assert.ok(source.includes("#cloud-run-button:focus-visible"));
  assert.ok(source.includes("@media (max-width: 480px)"));
  assert.ok(source.includes("width: 32px"));
});
