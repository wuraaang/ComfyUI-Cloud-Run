import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
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
        official_template_id: "57808457573e32120301649763d8e019",
        official_template_name: "Official ComfyUI",
        preview_only: true,
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


test("mounts an immediately visible Cloud Run button and required preview modal", async () => {
  const document = new FakeDocument();
  const requests = [];
  const fetchImpl = async (...args) => {
    requests.push(args);
    return settingsResponse();
  };

  mountCloudRun(document, fetchImpl);

  const launcher = document.getElementById("cloud-run-button");
  assert.ok(launcher);
  assert.equal(launcher.textContent, "Cloud Run");
  assert.equal(launcher.getAttribute("data-testid"), "cloud-run-button");
  assert.equal(launcher.hidden, false);
  assert.notEqual(launcher.style.display, "none");
  assert.equal(launcher.isConnected, true);

  const dialog = document.getElementById("cloud-run-modal");
  assert.ok(dialog);
  assert.equal(dialog.open, false);

  await launcher.click();

  assert.equal(dialog.open, true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], "/cloud-run/api/settings");
  assert.equal(
    document.getElementById("cloud-run-preview-banner").textContent,
    "Preview only — no instance will be rented.",
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
    "Preview selection",
  );
});


test("registers through the pinned ComfyUI extension API and mounts during setup", async () => {
  const document = new FakeDocument();
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
  assert.equal(extension.name, "comfyui-cloud-run.preview");
  assert.equal(typeof extension.setup, "function");

  await extension.setup();
  const launcher = document.getElementById("cloud-run-button");
  assert.ok(launcher);
  assert.equal(launcher.textContent, "Cloud Run");

  await launcher.click();
  assert.equal(apiRequests.length, 1);
  assert.equal(apiRequests[0][0], "/cloud-run/api/settings");
});


test("saves settings with a write-only optional key and clears the password", async () => {
  const document = new FakeDocument();
  const requests = [];
  const responses = [
    settingsResponse(),
    jsonResponse({
      configured: true,
      max_price_per_hour: 0.55,
      min_vram_gb: 32,
      official_template_id: "57808457573e32120301649763d8e019",
      official_template_name: "Official ComfyUI",
      preview_only: true,
    }),
    jsonResponse({
      configured: true,
      max_price_per_hour: 0.6,
      min_vram_gb: 48,
      official_template_id: "57808457573e32120301649763d8e019",
      official_template_name: "Official ComfyUI",
      preview_only: true,
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
      preview_only: true,
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


test("previews the selected offer and official template without another request", async () => {
  const document = new FakeDocument();
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
      preview_only: true,
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
  const requestCountBeforePreview = requests.length;

  await document.getElementById("cloud-run-preview-selection").click();

  assert.equal(requests.length, requestCountBeforePreview);
  const preview = document.getElementById("cloud-run-selection-preview");
  assert.ok(preview.textContent.includes("RTX 4090"));
  assert.ok(preview.textContent.includes("$0.42/h"));
  assert.ok(preview.textContent.includes("Official ComfyUI"));
  assert.ok(
    preview.textContent.includes("57808457573e32120301649763d8e019"),
  );
  assert.ok(preview.textContent.includes("No instance was created."));
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
  assert.ok(source.includes("details.textContent"));
});
