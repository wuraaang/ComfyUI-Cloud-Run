import { captureOfficialQueuePayload } from "./canvas-adapter.js";
import {
  createCloudRunApi,
  OFFERS_ENDPOINT,
  PREFLIGHTS_ENDPOINT,
  SETTINGS_ENDPOINT,
  SESSIONS_ENDPOINT,
} from "./cloud-run-api.js";
import { createSessionConsole } from "./session-console.js";
import {
  installNativePromptIdentity,
  isVastRole,
  readDesktopContext,
  startExtension,
} from "./comfyui-vast.js";


const OPEN_COMMAND_ID = "vast-cloud-run.open";
const POLL_INTERVAL_MS = 1000;
const DEFAULT_SESSION_SECONDS = 2 * 60 * 60;
const UNFILTERED_MAX_PRICE_PER_HOUR = 100;
const UNFILTERED_MIN_VRAM_GB = 1;
const INVALID_PRICE_MESSAGE =
  "Price must be blank or a number such as 0.46 (0,46 also works).";
const INVALID_VRAM_MESSAGE =
  "VRAM preference must be blank or a whole number such as 16 or 24.";
const SESSION_DURATION_SECONDS = new Map([
  ["none", null],
  ["1800", 30 * 60],
  ["3600", 60 * 60],
  ["5400", 90 * 60],
  ["7200", DEFAULT_SESSION_SECONDS],
]);
const mountedCloudRuns = new WeakMap();
const OFFER_REASON_TEXT = new Map([
  ["workflow_requirements", "meets workflow VRAM and disk requirements"],
  ["price_cap", "within the hard price cap"],
  ["source_ready", "all required artifacts have a verified source"],
  ["quality_metrics", "required reliability and transfer metrics are present"],
  ["vram", "VRAM below the workflow minimum"],
  ["disk", "disk below the workflow requirement"],
  ["price", "price above the hard cap"],
  ["reliability", "reliability below the safety floor"],
  ["source_readiness", "required artifact source is not ready"],
  ["missing_metrics", "required offer metrics are unavailable"],
  ["blacklisted", "host is temporarily excluded after a failure"],
  ["suspicious_price", "price is inconsistent with equivalent offers"],
]);


function createElement(document, tagName, options = {}) {
  const element = document.createElement(tagName);
  if (options.id) element.id = options.id;
  if (options.testId) element.setAttribute("data-testid", options.testId);
  if (options.text !== undefined) element.textContent = options.text;
  if (options.type) element.type = options.type;
  if (options.className) element.className = options.className;
  return element;
}


function appendField(document, parent, labelText, input) {
  const field = createElement(document, "div", {
    className: "cloud-run-field",
  });
  const label = createElement(document, "label", { text: labelText });
  label.setAttribute("for", input.id);
  field.append(label, input);
  parent.appendChild(field);
}


function ensureStyles(document) {
  if (document.getElementById("cloud-run-styles")) return;
  const style = createElement(document, "style", { id: "cloud-run-styles" });
  style.textContent = `
#cloud-run-button {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  border: 1px solid var(--border-color, #4b5563);
  border-radius: 8px;
  padding: 7px 12px;
  background: var(--comfy-menu-bg, #20242b);
  color: var(--input-text, #f3f4f6);
  cursor: pointer;
  font: 600 13px system-ui, sans-serif;
}
#cloud-run-button:focus-visible,
.cloud-run-actions button:focus-visible,
.cloud-run-actions a:focus-visible {
  outline: 2px solid #60a5fa;
  outline-offset: 2px;
}
@media (max-width: 480px) {
  #cloud-run-button {
    box-sizing: border-box;
    justify-content: center;
    width: 32px;
    padding: 6px;
    overflow: hidden;
    font-size: 0;
  }
  #cloud-run-button::before {
    content: "☁";
    font-size: 16px;
  }
}
#cloud-run-modal {
  width: min(680px, calc(100vw - 32px));
  max-height: calc(100vh - 48px);
  overflow: auto;
  border: 1px solid var(--border-color, #4b5563);
  border-radius: 12px;
  padding: 0;
  background: var(--comfy-menu-bg, #20242b);
  color: var(--input-text, #f3f4f6);
  box-shadow: 0 18px 60px rgba(0, 0, 0, .55);
}
#cloud-run-modal::backdrop { background: rgba(0, 0, 0, .62); }
.cloud-run-card { padding: 18px; font: 13px/1.4 system-ui, sans-serif; }
.cloud-run-header { display: flex; align-items: center; gap: 12px; }
.cloud-run-header h2 { flex: 1; margin: 0; font-size: 18px; }
.cloud-run-banner {
  margin: 12px 0;
  padding: 9px 10px;
  border-radius: 7px;
  background: #4a3211;
  color: #ffe3a3;
  font-weight: 700;
}
.cloud-run-field { display: grid; gap: 5px; margin: 10px 0; }
.cloud-run-field input,
.cloud-run-field select {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid var(--border-color, #4b5563);
  border-radius: 6px;
  padding: 8px;
  background: var(--comfy-input-bg, #171a1f);
  color: inherit;
}
.cloud-run-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 12px 0;
}
.cloud-run-actions button,
.cloud-run-actions a {
  box-sizing: border-box;
  border: 1px solid var(--border-color, #4b5563);
  border-radius: 6px;
  padding: 7px 10px;
  background: var(--comfy-input-bg, #171a1f);
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-decoration: none;
}
.cloud-run-actions button:disabled { cursor: wait; opacity: .55; }
.cloud-run-danger { border-color: #dc2626 !important; color: #fecaca !important; }
.cloud-run-status { min-height: 1.4em; color: var(--input-text, #d1d5db); }
.cloud-run-offers { display: grid; gap: 7px; margin: 10px 0; }
.cloud-run-session-console {
  margin: 12px 0;
  padding: 10px;
  border: 1px solid var(--border-color, #4b5563);
  border-radius: 7px;
}
.cloud-run-session-console h3,
.cloud-run-session-console h4 { margin: 8px 0; }
.cloud-run-preflight-rows,
.cloud-run-history,
.cloud-run-outputs { display: grid; gap: 4px; margin: 8px 0; }
.cloud-run-paid-review,
.cloud-run-live-session,
.cloud-run-job,
.cloud-run-destroy-review {
  margin: 10px 0;
  padding: 10px;
  border-radius: 7px;
  background: rgba(127,127,127,.12);
}
.cloud-run-previews {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 6px;
}
.cloud-run-previews img { width: 100%; height: auto; }
`;
  document.head.appendChild(style);
}


function decimalText(value) {
  const number = Number(value);
  return Number.isFinite(number)
    ? number.toFixed(1).replace(/\.0$/, "")
    : "unknown";
}


function hourlyText(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `$${number.toFixed(2)}/h` : "unknown rate";
}


function finiteNonnegativeNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}


function metricNumberText(value) {
  return value.toFixed(1).replace(/\.0$/, "");
}


function downloadClassText(downloadMbps) {
  if (downloadMbps === null || downloadMbps < 500) {
    return "download class unavailable";
  }
  return downloadMbps >= 1000
    ? "target download class"
    : "fallback download class";
}


function theoreticalTransferText(value) {
  if (
    typeof value !== "number"
    || !Number.isSafeInteger(value)
    || value < 0
  ) {
    return "unavailable";
  }
  if (value < 60) return `${value} second${value === 1 ? "" : "s"}`;
  const minutes = Math.ceil(value / 60);
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}


function readinessBytes(value) {
  if (!Number.isSafeInteger(value) || value < 0) return "unavailable";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KiB`;
  return `${value} B`;
}


function safeReadiness(value) {
  if (
    !value
    || typeof value !== "object"
    || !["cold", "prepositioned", "warm"].includes(value.label)
    || !Number.isSafeInteger(value.cached_bytes)
    || value.cached_bytes < 0
    || !Number.isSafeInteger(value.remaining_bytes)
    || value.remaining_bytes < 0
    || !Number.isFinite(value.assumed_mbps)
    || value.assumed_mbps <= 0
    || !Number.isSafeInteger(value.estimated_seconds)
    || value.estimated_seconds < 0
    || typeof value.source_ready !== "boolean"
    || typeof value.ten_minute_eligible !== "boolean"
    || typeof value.digest !== "string"
    || !/^[0-9a-f]{64}$/.test(value.digest)
  ) {
    return null;
  }
  return value;
}


function createIdempotencyKey(browserWindow) {
  const cryptography = browserWindow?.crypto ?? globalThis.crypto;
  if (typeof cryptography?.randomUUID === "function") {
    return cryptography.randomUUID();
  }
  if (typeof cryptography?.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    cryptography.getRandomValues(bytes);
    return Array.from(
      bytes,
      (value) => value.toString(16).padStart(2, "0"),
    ).join("");
  }
  throw new Error("secure random source unavailable");
}


export function renderOffers(document, container, offers, onSelect) {
  container.replaceChildren();
  if (!Array.isArray(offers) || offers.length === 0) {
    container.textContent = "No matching offers found.";
    return;
  }
  const included = offers.filter((offer) => offer?.included !== false).slice(0, 5);
  const excluded = offers.filter((offer) => offer?.included === false);
  if (included.length === 0) {
    container.appendChild(createElement(document, "div", {
      text: "No eligible offer matches the workflow and safety limits.",
    }));
  }
  included.forEach((offer, index) => {
    const row = createElement(document, "label", {
      className: "cloud-run-offer",
    });
    const selector = createElement(document, "input", {
      id: `cloud-run-offer-${index}`,
      testId: `cloud-run-offer-select-${index}`,
      type: "radio",
    });
    selector.setAttribute("name", "cloud-run-offer");
    const details = createElement(document, "span");
    const reliability = Number.isFinite(Number(offer?.reliability))
      ? `${(Number(offer.reliability) * 100).toFixed(1)}% reliability`
      : "reliability unavailable";
    const downloadMbps = finiteNonnegativeNumber(offer?.inet_down_mbps);
    const diskSpeed = finiteNonnegativeNumber(offer?.disk_bw_mbps);
    const dlperf = finiteNonnegativeNumber(offer?.dlperf);
    const down = finiteNonnegativeNumber(offer?.inet_down_cost);
    const up = finiteNonnegativeNumber(offer?.inet_up_cost);
    const downloadPrice = down !== null
      ? `$${down.toFixed(3)}/GB down`
      : "download price unavailable";
    const uploadPrice = up !== null
      ? `$${up.toFixed(3)}/GB up`
      : "upload price unavailable";
    const download = downloadMbps !== null
      ? `${metricNumberText(downloadMbps)} Mbps download`
      : "download unavailable";
    const disk = diskSpeed !== null
      ? `${metricNumberText(diskSpeed)} MB/s disk`
      : "disk speed unavailable";
    const performance = dlperf !== null
      ? `DLPerf ${metricNumberText(dlperf)}`
      : "DLPerf unavailable";
    const readiness = safeReadiness(offer?.readiness);
    const estimate = theoreticalTransferText(
      readiness?.estimated_seconds ?? offer?.estimated_transfer_seconds,
    );
    const readinessText = readiness
      ? `${readiness.label}; cached ${readinessBytes(readiness.cached_bytes)}; ` +
        `remaining ${readinessBytes(readiness.remaining_bytes)}; ` +
        `assumption ${metricNumberText(readiness.assumed_mbps)} MB/s`
      : "readiness unavailable";
    const includedReasons = Array.isArray(offer?.included_reasons)
      ? offer.included_reasons
        .map((reason) => OFFER_REASON_TEXT.get(reason))
        .filter(Boolean)
      : [];
    details.textContent =
      `${String(offer?.gpu_name ?? "Unknown GPU")} — ` +
      `${decimalText(offer?.gpu_ram_gb)} GB — ` +
      `${hourlyText(offer?.dph_total)} — ${reliability} — ` +
      `${performance} — ${download} — ` +
      `${disk} — ${downloadClassText(downloadMbps)} — ` +
      `${downloadPrice}, ${uploadPrice} — ${readinessText} — ` +
      `theoretical transfer ≈ ${estimate}; actual startup can be longer` +
      `${includedReasons.length ? ` — ${includedReasons.join("; ")}` : ""}`;
    selector.addEventListener("click", () => onSelect(offer));
    row.append(selector, details);
    container.appendChild(row);
  });
  if (excluded.length > 0) {
    const details = createElement(document, "details", {
      className: "cloud-run-excluded-offers",
    });
    const summary = createElement(document, "summary", {
      text: `${excluded.length} excluded offer${excluded.length === 1 ? "" : "s"}`,
    });
    details.appendChild(summary);
    for (const offer of excluded.slice(0, 20)) {
      const reasons = Array.isArray(offer?.excluded_reasons)
        ? offer.excluded_reasons
          .map((reason) => OFFER_REASON_TEXT.get(reason))
          .filter(Boolean)
        : [];
      details.appendChild(createElement(document, "div", {
        text:
          `${String(offer?.gpu_name ?? "Unknown GPU")} — ` +
          `${reasons.length ? reasons.join(", ") : "not eligible"}`,
      }));
    }
    container.appendChild(details);
  }
}


function placeLauncherBesideLocalRun(document, launcher) {
  const queueButton = document.querySelector?.('[data-testid="queue-button"]');
  const queueGroup = queueButton?.parentElement;
  const actionbar = queueGroup?.parentElement;
  if (!actionbar) return false;
  if (
    launcher.parentElement === actionbar
    && queueGroup.nextSibling === launcher
  ) {
    return true;
  }
  actionbar.insertBefore(launcher, queueGroup.nextSibling);
  return true;
}


function ensureLauncherPlacement(browserWindow, document, mounted) {
  const placed = placeLauncherBesideLocalRun(document, mounted.launcher);
  if (
    !mounted.observer
    && typeof browserWindow?.MutationObserver === "function"
  ) {
    mounted.observer = new browserWindow.MutationObserver(() => {
      placeLauncherBesideLocalRun(document, mounted.launcher);
    });
    mounted.observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }
  return placed;
}


export function mountCloudRun(
  document,
  fetchImpl,
  browserWindow = globalThis,
  captureContext = {},
) {
  const mounted = mountedCloudRuns.get(document);
  if (mounted) {
    if (captureContext.app || captureContext.api || captureContext.onCapture) {
      mounted.captureContext = {
        ...mounted.captureContext,
        ...captureContext,
      };
    }
    ensureLauncherPlacement(browserWindow, document, mounted);
    return mounted.launcher;
  }

  ensureStyles(document);
  const cloudApi = createCloudRunApi(fetchImpl);
  const launcher = createElement(document, "button", {
    id: "cloud-run-button",
    testId: "cloud-run-button",
    text: "☁ Cloud Vast",
    type: "button",
  });
  launcher.setAttribute("aria-haspopup", "dialog");
  launcher.setAttribute("aria-controls", "cloud-run-modal");
  launcher.setAttribute("aria-label", "Cloud Vast on Vast.ai");
  launcher.setAttribute("title", "Cloud Vast — paid GPU rental");

  const dialog = createElement(document, "dialog", {
    id: "cloud-run-modal",
    testId: "cloud-run-modal",
  });
  dialog.setAttribute("aria-labelledby", "cloud-run-title");
  const card = createElement(document, "div", {
    className: "cloud-run-card",
  });
  const header = createElement(document, "div", {
    className: "cloud-run-header",
  });
  const title = createElement(document, "h2", {
    id: "cloud-run-title",
    text: "Cloud Vast",
  });
  const closeButton = createElement(document, "button", {
    id: "cloud-run-close",
    testId: "cloud-run-close",
    text: "Close",
    type: "button",
  });
  header.append(title, closeButton);

  const banner = createElement(document, "div", {
    id: "cloud-run-preview-banner",
    testId: "cloud-run-preview-banner",
    className: "cloud-run-banner",
    text:
      "Paid Vast.ai rental — nothing is created until explicit confirmation.",
  });
  const configured = createElement(document, "div", {
    id: "cloud-run-configured",
    testId: "cloud-run-configured",
  });
  const apiKeyInput = createElement(document, "input", {
    id: "cloud-run-api-key",
    testId: "cloud-run-api-key",
    type: "password",
  });
  apiKeyInput.setAttribute("autocomplete", "new-password");
  const priceInput = createElement(document, "input", {
    id: "cloud-run-max-price",
    testId: "cloud-run-max-price",
    type: "text",
  });
  priceInput.setAttribute("inputmode", "decimal");
  priceInput.setAttribute("placeholder", "No price filter");
  const vramInput = createElement(document, "input", {
    id: "cloud-run-min-vram",
    testId: "cloud-run-min-vram",
    type: "number",
  });
  vramInput.setAttribute("min", "1");
  vramInput.setAttribute("max", "1024");
  vramInput.setAttribute("step", "1");
  vramInput.setAttribute("placeholder", "No VRAM preference");
  const durationSelect = createElement(document, "select", {
    id: "cloud-run-session-duration",
    testId: "cloud-run-session-duration",
  });
  for (const [value, label] of [
    ["none", "No automatic limit — manual destruction"],
    ["1800", "30 minutes"],
    ["3600", "60 minutes"],
    ["5400", "90 minutes"],
    ["7200", "120 minutes"],
  ]) {
    const option = createElement(document, "option", {
      text: label,
    });
    option.value = value;
    durationSelect.appendChild(option);
  }
  durationSelect.value = "none";
  const createLimitReview = createElement(document, "div", {
    id: "cloud-run-create-limit-review",
    testId: "cloud-run-create-limit-review",
    text:
      "Maximum provider creates for this review: 1. " +
      "Cloud Vast never rents a replacement automatically.",
  });

  const primaryActions = createElement(document, "div", {
    className: "cloud-run-actions",
  });
  const saveButton = createElement(document, "button", {
    id: "cloud-run-save-settings",
    testId: "cloud-run-save-settings",
    text: "Save settings",
    type: "button",
  });
  const searchButton = createElement(document, "button", {
    id: "cloud-run-search",
    testId: "cloud-run-search",
    text: "Search Vast GPUs",
    type: "button",
  });
  const reviewButton = createElement(document, "button", {
    id: "cloud-run-review-session",
    testId: "cloud-run-review-session",
    text: "Review paid rental",
    type: "button",
  });
  reviewButton.disabled = true;
  primaryActions.append(saveButton, searchButton, reviewButton);

  const status = createElement(document, "div", {
    id: "cloud-run-status",
    testId: "cloud-run-status",
    className: "cloud-run-status",
  });
  status.setAttribute("role", "status");
  const offers = createElement(document, "div", {
    id: "cloud-run-offers",
    testId: "cloud-run-offers",
    className: "cloud-run-offers",
  });

  let record;
  let selectedOffer = null;
  let sessionIdempotencyKey = null;
  let busy = false;

  function clearBrowserPaidReview() {
    selectedOffer = null;
    sessionIdempotencyKey = null;
    offers.replaceChildren();
    reviewButton.disabled = true;
  }

  async function captureCurrentCanvas() {
    const context = record?.captureContext ?? captureContext;
    if (!context.app || !context.api) {
      throw new Error("Pinned ComfyUI capture API is unavailable.");
    }
    const capture = await captureOfficialQueuePayload({
      app: context.app,
      api: context.api,
    });
    const persisted = typeof context.onCapture === "function"
      ? await context.onCapture(capture)
      : await cloudApi.capture(capture);
    if (
      !persisted
      || typeof persisted.capture_id !== "string"
      || !persisted.capture_id
    ) {
      throw new Error("Cloud Vast capture persistence failed.");
    }
    record.currentCapture = capture;
    record.sessionConsole.setCapture(persisted.capture_id);
    clearBrowserPaidReview();
    return persisted;
  }

  const sessionConsole = createSessionConsole(
    document,
    cloudApi,
    {
      id: "cloud-run-dependency-console",
      searchButton,
      manageSearch: false,
      async revalidateCurrentCanvas() {
        const context = record?.captureContext ?? captureContext;
        if (!context.app || !context.api) {
          throw new Error("Pinned ComfyUI capture API is unavailable.");
        }
        const capture = await captureOfficialQueuePayload({
          app: context.app,
          api: context.api,
        });
        const persisted = typeof context.onCapture === "function"
          ? await context.onCapture(capture)
          : await cloudApi.capture(capture);
        if (
          !persisted
          || typeof persisted.capture_id !== "string"
          || !persisted.capture_id
        ) {
          throw new Error("Cloud Vast capture persistence failed.");
        }
        return cloudApi.preflight(
          persisted.capture_id,
          sessionConsole.outputAllowance,
        );
      },
      setTimeout: typeof browserWindow?.setTimeout === "function"
        ? browserWindow.setTimeout.bind(browserWindow)
        : undefined,
      clearTimeout: typeof browserWindow?.clearTimeout === "function"
        ? browserWindow.clearTimeout.bind(browserWindow)
        : undefined,
      pollIntervalMs: POLL_INTERVAL_MS,
      now: () => Date.now() / 1000,
      onSession(session) {
        if (session?.rental_outcome === "absent") {
          clearBrowserPaidReview();
          banner.className = "cloud-run-banner";
          banner.textContent =
            "Vast inventory confirms that this session no longer bills.";
        } else if (
          session?.rental_outcome === "unknown"
          || (
            session?.billing_may_continue === true
            && session?.rental_outcome !== "active"
          )
          || (
            session?.rental_outcome === "active"
            && (
              session?.status === "failed"
              || (
                Array.isArray(session?.residual_inventory)
                && session.residual_inventory.length > 0
              )
            )
          )
        ) {
          banner.className = "cloud-run-banner cloud-run-danger";
          banner.textContent =
            "Warning — Vast billing may still be active. Use Destroy GPU.";
        } else if (session?.rental_outcome === "active") {
          banner.className = "cloud-run-banner";
          banner.textContent =
            "Paid Vast.ai session — billing ends only after verified destruction.";
        }
      },
    },
  );

  card.replaceChildren(header, banner, configured);
  appendField(document, card, "Vast API key", apiKeyInput);
  appendField(
    document,
    card,
    "Maximum hourly price ($/h, optional)",
    priceInput,
  );
  appendField(document, card, "Preferred VRAM (GB, optional)", vramInput);
  appendField(document, card, "Session duration", durationSelect);
  card.appendChild(createLimitReview);
  card.append(
    primaryActions,
    status,
    offers,
    sessionConsole.root,
  );
  dialog.appendChild(card);

  function setBusy(value) {
    busy = Boolean(value);
    saveButton.disabled = busy;
    searchButton.disabled = busy || !sessionConsole.canSearchOffers;
    reviewButton.disabled = busy || !selectedOffer;
  }

  function visibleSettingsPayload() {
    const rawPrice = String(priceInput.value ?? "").trim();
    let maxPrice = UNFILTERED_MAX_PRICE_PER_HOUR;
    if (rawPrice !== "") {
      if (!/^(?:\d+(?:[.,]\d+)?|[.,]\d+)$/.test(rawPrice)) {
        return { payload: null, error: INVALID_PRICE_MESSAGE };
      }
      maxPrice = Number(rawPrice.replace(",", "."));
      if (!Number.isFinite(maxPrice) || maxPrice < 0.01 || maxPrice > 100) {
        return { payload: null, error: INVALID_PRICE_MESSAGE };
      }
    }

    const rawVram = String(vramInput.value ?? "").trim();
    let minVram = UNFILTERED_MIN_VRAM_GB;
    if (rawVram !== "") {
      minVram = Number(rawVram);
      if (!Number.isInteger(minVram) || minVram < 1 || minVram > 1024) {
        return { payload: null, error: INVALID_VRAM_MESSAGE };
      }
    }
    const payload = {
      max_price_per_hour: maxPrice,
      min_vram_gb: minVram,
    };
    const key = apiKeyInput.value.trim();
    if (key) payload.api_key = key;
    return { payload, error: null };
  }

  function renderSavedSettings(result) {
    apiKeyInput.value = "";
    configured.textContent = result.configured
      ? "Vast API key is configured."
      : "Vast API key is not configured.";
    sessionConsole.renderSettings(result);
  }

  async function loadSettings() {
    status.textContent = "Loading settings…";
    try {
      const payload = await cloudApi.getSettings();
      configured.textContent = payload.configured
        ? "Vast API key is configured."
        : "Vast API key is not configured.";
      priceInput.value =
        Number(payload.max_price_per_hour) === UNFILTERED_MAX_PRICE_PER_HOUR
          ? ""
          : String(payload.max_price_per_hour);
      vramInput.value =
        Number(payload.min_vram_gb) === UNFILTERED_MIN_VRAM_GB
          ? ""
          : String(payload.min_vram_gb);
      sessionConsole.renderSettings(payload);
      const active = Array.isArray(payload.active_sessions)
        ? [...payload.active_sessions].reverse().find(
          (session) => (
            session?.billing_may_continue === true
            || ["unknown", "active"].includes(session?.rental_outcome)
          ),
        )
        : null;
      const recentAbsent = !active && Array.isArray(payload.recent_sessions)
        ? [...payload.recent_sessions].reverse().find(
          (session) => (
            session?.status === "failed"
            && session?.rental_outcome === "absent"
          ),
        )
        : null;
      if (active || recentAbsent) {
        sessionConsole.renderSession(active ?? recentAbsent);
      }
      status.textContent = "";
    } catch {
      status.textContent = "Settings could not be loaded.";
    }
  }

  const openDialog = async () => {
    dialog.showModal();
    await loadSettings();
    const context = record.captureContext;
    if (!context.app || !context.api) {
      status.textContent =
        "Pinned ComfyUI canvas capture is unavailable in this host.";
      return;
    }
    status.textContent = "Compiling the current canvas with ComfyUI…";
    try {
      await captureCurrentCanvas();
      status.textContent =
        "Current canvas captured without local execution. Run free preflight.";
    } catch (error) {
      const allowed = new Set([
        "Pinned ComfyUI queue API is unavailable.",
        "Pinned ComfyUI prompt API is unavailable.",
        "A Cloud Vast canvas capture is already in progress.",
        "ComfyUI was busy; no Cloud Vast payload was captured.",
      ]);
      status.textContent =
        error instanceof Error && allowed.has(error.message)
          ? error.message
          : "Canvas capture failed before any Cloud Vast mutation.";
    }
  };

  launcher.addEventListener("click", openDialog);
  launcher.addEventListener("keydown", async (event) => {
    if (!["Enter", " ", "Spacebar"].includes(event.key)) return;
    event.preventDefault();
    await openDialog();
  });

  saveButton.addEventListener("click", async () => {
    const { payload, error } = visibleSettingsPayload();
    if (error) {
      status.textContent = error;
      return;
    }
    setBusy(true);
    status.textContent = "Saving settings…";
    try {
      const result = await cloudApi.updateSettings(payload);
      renderSavedSettings(result);
      status.textContent = "Settings saved.";
    } catch {
      status.textContent = "Settings could not be saved.";
    } finally {
      setBusy(false);
    }
  });

  searchButton.addEventListener("click", async () => {
    if (!sessionConsole.canSearchOffers) {
      status.textContent =
        "Run and resolve the free dependency preflight first.";
      return;
    }
    const {
      payload: settingsPayload,
      error: settingsError,
    } = visibleSettingsPayload();
    if (settingsError) {
      status.textContent = settingsError;
      return;
    }
    clearBrowserPaidReview();
    sessionConsole.clearPaidReview();
    setBusy(true);
    try {
      status.textContent = "Saving settings for this search…";
      try {
        const settings = await cloudApi.updateSettings(settingsPayload);
        renderSavedSettings(settings);
      } catch {
        status.textContent =
          "Search settings could not be saved; no offer search was started.";
        return;
      }
      status.textContent = "Searching eligible Vast GPUs…";
      const result = await cloudApi.searchOffers(
        sessionConsole.preflightId,
      );
      if (!Array.isArray(result.offers)) throw new Error("invalid offers");
      renderOffers(document, offers, result.offers, (offer) => {
        const changed =
          String(selectedOffer?.offer_id ?? "")
          !== String(offer?.offer_id ?? "");
        selectedOffer = offer;
        if (changed) sessionIdempotencyKey = null;
        setBusy(false);
      });
      const includedCount = result.offers.filter(
        (offer) => offer?.included !== false,
      ).length;
      status.textContent =
        `${includedCount} matching offer` +
        `${includedCount === 1 ? "" : "s"} found.`;
    } catch {
      offers.replaceChildren();
      status.textContent = "Vast offer search is unavailable.";
    } finally {
      setBusy(false);
    }
  });

  async function searchOnEnter(event) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    await searchButton.click();
  }

  priceInput.addEventListener("keydown", searchOnEnter);
  vramInput.addEventListener("keydown", searchOnEnter);

  reviewButton.addEventListener("click", async () => {
    if (!selectedOffer || !sessionConsole.preflightId) return;
    const readiness = safeReadiness(selectedOffer.readiness);
    if (readiness === null || readiness.source_ready !== true) {
      status.textContent =
        "This offer has no verified source-ready estimate.";
      return;
    }
    const durationSeconds = SESSION_DURATION_SECONDS.get(
      String(durationSelect.value),
    );
    if (durationSeconds === undefined) {
      status.textContent =
        "Choose manual destruction or one of the listed session durations.";
      return;
    }
    try {
      if (!sessionIdempotencyKey) {
        sessionIdempotencyKey = createIdempotencyKey(browserWindow);
      }
    } catch {
      status.textContent =
        "A secure paid-session key could not be generated.";
      return;
    }
    setBusy(true);
    status.textContent = durationSeconds === null
      ? "Creating a paid review with manual destruction…"
      : "Creating a paid review with an automatic limit…";
    try {
      const session = await cloudApi.createSession({
        preflight_id: sessionConsole.preflightId,
        offer_id: selectedOffer.offer_id,
        idempotency_key: sessionIdempotencyKey,
        deadline: {
          mode: durationSeconds === null ? "none" : "finite",
          duration_seconds: durationSeconds,
        },
        max_instance_creates: 1,
        estimate_digest: readiness.digest,
      });
      sessionConsole.renderQuote(session, {
        idempotencyKey: sessionIdempotencyKey,
      });
      status.textContent =
        durationSeconds === null
          ? "Review the price: billing continues until you Destroy the GPU."
          : "Review the price and automatic limit before paid confirmation.";
    } catch (error) {
      const message =
        error instanceof Error
        && error.message
        && error.message !== "request failed"
          ? error.message
          : "The selected offer could not be reviewed.";
      status.textContent = message;
    } finally {
      setBusy(false);
    }
  });

  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    apiKeyInput.value = "";
  });

  document.body.appendChild(dialog);
  record = {
    captureContext: { ...captureContext },
    currentCapture: null,
    dialog,
    launcher,
    observer: null,
    openDialog,
    sessionConsole,
  };
  mountedCloudRuns.set(document, record);
  setBusy(false);
  ensureLauncherPlacement(browserWindow, document, record);
  return launcher;
}


export async function openCloudRun(
  document,
  fetchImpl,
  browserWindow = globalThis,
  captureContext = {},
) {
  mountCloudRun(document, fetchImpl, browserWindow, captureContext);
  return mountedCloudRuns.get(document).openDialog();
}


export function registerCloudRunWhenReady(browserWindow, document, tries = 0) {
  if (browserWindow.__cloudRunLifecycleRegistered) return true;
  const app = browserWindow.comfyAPI?.app?.app ?? browserWindow.app;
  const api = browserWindow.comfyAPI?.api?.api ?? browserWindow.api;
  if (
    !app
    || typeof app.registerExtension !== "function"
    || !api
    || typeof api.fetchApi !== "function"
  ) {
    if (tries < 500 && typeof browserWindow.setTimeout === "function") {
      browserWindow.setTimeout(
        () => registerCloudRunWhenReady(
          browserWindow,
          document,
          tries + 1,
        ),
        10,
      );
    }
    return false;
  }

  const fetchImpl = api.fetchApi.bind(api);
  const cloudApi = createCloudRunApi(fetchImpl);
  let contextPromise = null;
  let desktopContext = null;
  let vastIdentityInstalled = false;
  const resolveContext = async () => {
    if (desktopContext !== null) return desktopContext;
    if (contextPromise === null) {
      contextPromise = readDesktopContext(fetchImpl)
        .then((context) => {
          desktopContext = context;
          return context;
        })
        .catch(() => null);
    }
    return contextPromise;
  };
  const captureContext = {
    app,
    api,
    onCapture: (capture) => cloudApi.capture(capture),
  };
  app.registerExtension({
    name: "comfyui-cloud-run.lifecycle",
    commands: [
      {
        id: OPEN_COMMAND_ID,
        label: "Cloud Vast",
        async function() {
          const context = await resolveContext();
          if (context?.role !== "local") return null;
          return openCloudRun(
            document,
            fetchImpl,
            browserWindow,
            captureContext,
          );
        },
      },
    ],
    menuCommands: [
      {
        path: ["Extensions", "Cloud Vast"],
        commands: [OPEN_COMMAND_ID],
      },
    ],
    async init() {
      const context = await resolveContext();
      if (context !== null && isVastRole(context)) {
        installNativePromptIdentity(api, browserWindow.crypto);
        vastIdentityInstalled = true;
      }
    },
    async setup() {
      const context = await resolveContext();
      if (context === null) return null;
      if (isVastRole(context) && !vastIdentityInstalled) {
        installNativePromptIdentity(api, browserWindow.crypto);
        vastIdentityInstalled = true;
      }
      return startExtension({
        context,
        api,
        app,
        cryptoImpl: browserWindow.crypto,
        mountLifecycle: () => mountCloudRun(
          document,
          fetchImpl,
          browserWindow,
          captureContext,
        ),
        loadBootstrap: (
          isVastRole(context)
          && Number.isSafeInteger(context.bootstrap_revision)
          && context.bootstrap_revision > 0
        )
          ? () => cloudApi.getDesktopBootstrap()
          : undefined,
        acknowledgeBootstrap: (
          isVastRole(context)
          && Number.isSafeInteger(context.bootstrap_revision)
          && context.bootstrap_revision > 0
        )
          ? (revision) => cloudApi.acknowledgeDesktopBootstrap(revision)
          : undefined,
      });
    },
  });
  browserWindow.__cloudRunLifecycleRegistered = true;
  return true;
}


export {
  DEFAULT_SESSION_SECONDS,
  OFFERS_ENDPOINT,
  OPEN_COMMAND_ID,
  POLL_INTERVAL_MS,
  PREFLIGHTS_ENDPOINT,
  SESSIONS_ENDPOINT,
  SETTINGS_ENDPOINT,
};


if (typeof window !== "undefined" && typeof document !== "undefined") {
  registerCloudRunWhenReady(window, document);
}
