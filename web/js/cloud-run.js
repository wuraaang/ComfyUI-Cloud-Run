const SETTINGS_ENDPOINT = "/cloud-run/api/settings";
const OFFERS_ENDPOINT = "/cloud-run/api/offers";
const QUOTES_ENDPOINT = "/cloud-run/api/quotes";
const OFFICIAL_TEMPLATE_ID = "57808457573e32120301649763d8e019";
const OFFICIAL_TEMPLATE_NAME = "Official ComfyUI";
const OPEN_COMMAND_ID = "vast-cloud-run.open";
const POLL_INTERVAL_MS = 1000;
const mountedCloudRuns = new WeakMap();


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
  const field = createElement(document, "div", { className: "cloud-run-field" });
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
  width: min(560px, calc(100vw - 32px));
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
.cloud-run-field input {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid var(--border-color, #4b5563);
  border-radius: 6px;
  padding: 8px;
  background: var(--comfy-input-bg, #171a1f);
  color: inherit;
}
.cloud-run-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
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
.cloud-run-preview {
  display: grid;
  gap: 5px;
  margin-top: 10px;
  padding: 10px;
  border-radius: 7px;
  background: rgba(127,127,127,.12);
}
`;
  document.head.appendChild(style);
}


function decimalText(value) {
  return Number(value).toFixed(1).replace(/\.0$/, "");
}


function hourlyText(value) {
  return `$${Number(value).toFixed(2)}/h`;
}


function attemptEndpoint(attemptId) {
  return `/cloud-run/api/attempts/${encodeURIComponent(String(attemptId))}`;
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


async function fetchJson(fetchImpl, endpoint, options) {
  let response;
  try {
    response = await fetchImpl(endpoint, options);
  } catch {
    throw new Error("request failed");
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error("request failed");
  }

  if (!payload || typeof payload !== "object") {
    throw new Error("request failed");
  }
  if (!response.ok) {
    const message =
      typeof payload.error === "string" ? payload.error.trim() : "";
    throw new Error(message || "request failed");
  }
  return payload;
}


export function renderOffers(document, container, offers, onSelect) {
  container.replaceChildren();
  if (!Array.isArray(offers) || offers.length === 0) {
    container.textContent = "No matching offers found.";
    return;
  }

  offers.forEach((offer, index) => {
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
    const reliability = Number.isFinite(Number(offer.reliability))
      ? `${(Number(offer.reliability) * 100).toFixed(1)}% reliability`
      : "reliability unavailable";
    details.textContent =
      `${String(offer.gpu_name)} — ` +
      `${decimalText(offer.gpu_ram_gb)} GB — ` +
      `${hourlyText(offer.dph_total)} — ${reliability}`;
    selector.addEventListener("click", () => onSelect(offer));
    row.append(selector, details);
    container.appendChild(row);
  });
}


export function attemptPresentation(attempt) {
  const status = String(attempt?.status ?? "idle");
  const presentations = {
    idle: { message: "Idle." },
    searching: { message: "Searching eligible Vast GPUs…", poll: true },
    offer_selected: {
      message: "Quote ready. Review the paid rate before confirmation.",
      confirm: true,
    },
    confirming: {
      message: "Revalidating the exact offer before rental…",
      poll: true,
    },
    creating: {
      message: "Vast is creating the instance; billing may have started.",
      cancel: true,
      poll: true,
    },
    starting: {
      message: "Instance created. Waiting for ComfyUI; billing is active.",
      cancel: true,
      poll: true,
    },
    cancel_requested: {
      message: "Cancellation requested. Verifying destruction and inventory…",
      poll: true,
    },
    destroying: {
      message: "Destroying the Vast instance and verifying it is absent…",
      poll: true,
    },
    retrying: {
      message: "Preparing the single safe replacement after verified cleanup…",
      cancel: true,
      poll: true,
    },
    ready: {
      message: "ComfyUI is ready. Billing continues until you choose Destroy.",
      open: Boolean(attempt?.ready_url),
      destroy: Boolean(attempt?.instance_id),
    },
    cancelled: {
      message: "Cancelled. Vast inventory is confirmed empty.",
    },
    failed: {
      message: "Cloud Run failed.",
      destroy: Boolean(attempt?.instance_id),
    },
  };
  const presentation = {
    confirm: false,
    cancel: false,
    open: false,
    destroy: false,
    poll: false,
    ...(presentations[status] ?? {
      message: "Cloud Run returned an unknown state.",
    }),
  };
  if (attempt?.error) {
    presentation.message += ` ${String(attempt.error)}`;
  }
  if (attempt?.billing_may_continue && attempt?.instance_id) {
    presentation.message +=
      ` Residual Vast instance: ${String(attempt.instance_id)}; ` +
      "billing may continue.";
  }
  if (attempt?.emergency_action) {
    presentation.message += ` ${String(attempt.emergency_action)}`;
  }
  return presentation;
}


export function renderSelectionPreview(document, container, attempt) {
  container.replaceChildren();
  const quote = attempt?.offer;
  if (!quote) return;
  const rows = [
    ["Paid rental confirmation", "Review every value before confirming."],
    ["Offer", String(quote.offer_id)],
    ["GPU", String(quote.gpu_name)],
    ["VRAM", `${decimalText(quote.gpu_ram_gb)} GB`],
    ["Rate", hourlyText(quote.dph_total)],
    ["Configured cap", hourlyText(quote.max_price_per_hour)],
    [
      "Template",
      `${OFFICIAL_TEMPLATE_NAME} (${OFFICIAL_TEMPLATE_ID})`,
    ],
  ];
  for (const [label, value] of rows) {
    const row = createElement(document, "div");
    row.textContent = `${label}: ${value}`;
    container.appendChild(row);
  }
  const cost = createElement(document, "strong", {
    text:
      attempt.status === "offer_selected"
        ? "No rental exists until you confirm the paid rate above."
        : "Billing can continue until Vast inventory confirms destruction.",
  });
  container.appendChild(cost);
  if (attempt.instance_id) {
    const instance = createElement(document, "div", {
      text: `Vast instance: ${String(attempt.instance_id)}`,
    });
    container.appendChild(instance);
  }
  if (attempt.emergency_action) {
    const emergency = createElement(document, "strong", {
      text: String(attempt.emergency_action),
    });
    container.appendChild(emergency);
  }
}


export function renderAttemptState(document, elements, attempt, busy = false) {
  const presentation = attemptPresentation(attempt);
  elements.status.textContent = presentation.message;
  elements.banner.textContent = attempt?.billing_may_continue
    ? "Warning — Vast billing may still be active."
    : "Paid Vast.ai rental — Destroy is the billing-safe terminal action.";
  renderSelectionPreview(document, elements.selectionPreview, attempt);

  elements.confirmButton.hidden = !presentation.confirm;
  elements.cancelButton.hidden = !presentation.cancel;
  elements.openLink.hidden = !presentation.open;
  elements.destroyButton.hidden = !presentation.destroy;

  const lifecycleLocked = new Set([
    "confirming",
    "creating",
    "cancel_requested",
    "destroying",
    "retrying",
  ]).has(String(attempt?.status));
  elements.saveButton.disabled = busy || lifecycleLocked;
  elements.searchButton.disabled = busy || lifecycleLocked;
  elements.previewButton.disabled =
    busy || lifecycleLocked || !elements.selectedOffer();
  elements.confirmButton.disabled = busy || !presentation.confirm;
  elements.cancelButton.disabled =
    busy ||
    ["cancel_requested", "destroying"].includes(String(attempt?.status));
  elements.destroyButton.disabled = busy || !presentation.destroy;

  if (presentation.open) {
    elements.openLink.setAttribute("href", String(attempt.ready_url));
    elements.openLink.setAttribute("target", "_blank");
    elements.openLink.setAttribute("rel", "noopener noreferrer");
  } else {
    elements.openLink.setAttribute("href", "");
  }
}


function placeLauncherBesideLocalRun(document, launcher) {
  const queueButton = document.querySelector?.('[data-testid="queue-button"]');
  const queueGroup = queueButton?.parentElement;
  const actionbar = queueGroup?.parentElement;
  if (!actionbar) return false;
  if (
    launcher.parentElement === actionbar &&
    queueGroup.nextSibling === launcher
  ) {
    return true;
  }
  actionbar.insertBefore(launcher, queueGroup.nextSibling);
  return true;
}


function ensureLauncherPlacement(browserWindow, document, mounted) {
  const placed = placeLauncherBesideLocalRun(document, mounted.launcher);
  if (
    !mounted.observer &&
    typeof browserWindow?.MutationObserver === "function"
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


export function mountCloudRun(document, fetchImpl, browserWindow = globalThis) {
  const mounted = mountedCloudRuns.get(document);
  if (mounted) {
    ensureLauncherPlacement(browserWindow, document, mounted);
    return mounted.launcher;
  }

  ensureStyles(document);
  const launcher = createElement(document, "button", {
    id: "cloud-run-button",
    testId: "cloud-run-button",
    text: "☁ Cloud Run",
    type: "button",
  });
  launcher.setAttribute("aria-haspopup", "dialog");
  launcher.setAttribute("aria-controls", "cloud-run-modal");
  launcher.setAttribute("aria-label", "Cloud Run on Vast.ai");
  launcher.setAttribute("title", "Launch on Vast.ai — paid GPU rental");

  const dialog = createElement(document, "dialog", {
    id: "cloud-run-modal",
    testId: "cloud-run-modal",
  });
  dialog.setAttribute("aria-labelledby", "cloud-run-title");
  const card = createElement(document, "div", { className: "cloud-run-card" });
  const header = createElement(document, "div", { className: "cloud-run-header" });
  const title = createElement(document, "h2", {
    id: "cloud-run-title",
    text: "Cloud Run",
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
    text: "Paid Vast.ai rental — nothing is created until explicit confirmation.",
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
    type: "number",
  });
  priceInput.setAttribute("min", "0.01");
  priceInput.setAttribute("max", "100");
  priceInput.setAttribute("step", "0.01");
  const vramInput = createElement(document, "input", {
    id: "cloud-run-min-vram",
    testId: "cloud-run-min-vram",
    type: "number",
  });
  vramInput.setAttribute("min", "1");
  vramInput.setAttribute("max", "1024");
  vramInput.setAttribute("step", "1");

  const actions = createElement(document, "div", {
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
  const previewButton = createElement(document, "button", {
    id: "cloud-run-preview-selection",
    testId: "cloud-run-preview-selection",
    text: "Review paid rental",
    type: "button",
  });
  previewButton.disabled = true;
  const confirmButton = createElement(document, "button", {
    id: "cloud-run-confirm",
    testId: "cloud-run-confirm",
    text: "Confirm & rent GPU",
    type: "button",
  });
  confirmButton.hidden = true;
  const cancelButton = createElement(document, "button", {
    id: "cloud-run-cancel",
    testId: "cloud-run-cancel",
    text: "Cancel — verify billing stopped",
    type: "button",
  });
  cancelButton.hidden = true;
  const openLink = createElement(document, "a", {
    id: "cloud-run-open",
    testId: "cloud-run-open",
    text: "Open ComfyUI",
  });
  openLink.hidden = true;
  const destroyButton = createElement(document, "button", {
    id: "cloud-run-destroy",
    testId: "cloud-run-destroy",
    text: "Destroy — end Vast billing",
    type: "button",
    className: "cloud-run-danger",
  });
  destroyButton.hidden = true;
  actions.append(
    saveButton,
    searchButton,
    previewButton,
    confirmButton,
    cancelButton,
    openLink,
    destroyButton,
  );

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
  const selectionPreview = createElement(document, "div", {
    id: "cloud-run-selection-preview",
    testId: "cloud-run-selection-preview",
    className: "cloud-run-preview",
  });

  card.replaceChildren(header, banner, configured);
  appendField(document, card, "Vast API key", apiKeyInput);
  appendField(document, card, "Maximum hourly price ($/h)", priceInput);
  appendField(document, card, "Minimum VRAM (GB)", vramInput);
  card.append(actions, status, offers, selectionPreview);
  dialog.appendChild(card);

  let selectedOffer = null;
  let currentAttempt = null;
  let idempotencyKey = null;
  let pollTimer = null;
  let busy = false;
  const elements = {
    banner,
    cancelButton,
    confirmButton,
    destroyButton,
    openLink,
    previewButton,
    saveButton,
    searchButton,
    selectionPreview,
    selectedOffer: () => selectedOffer,
    status,
  };

  function clearPoll() {
    if (
      pollTimer !== null &&
      typeof browserWindow?.clearTimeout === "function"
    ) {
      browserWindow.clearTimeout(pollTimer);
    }
    pollTimer = null;
  }

  function renderCurrent() {
    if (currentAttempt) {
      renderAttemptState(document, elements, currentAttempt, busy);
      return;
    }
    saveButton.disabled = busy;
    searchButton.disabled = busy;
    previewButton.disabled = busy || !selectedOffer;
    confirmButton.hidden = true;
    cancelButton.hidden = true;
    openLink.hidden = true;
    destroyButton.hidden = true;
  }

  function setBusy(value) {
    busy = Boolean(value);
    renderCurrent();
  }

  async function refreshAttempt() {
    pollTimer = null;
    if (!currentAttempt?.attempt_id) return;
    try {
      currentAttempt = await fetchJson(
        fetchImpl,
        attemptEndpoint(currentAttempt.attempt_id),
      );
      renderCurrent();
    } catch {
      status.textContent = "Attempt status is temporarily unavailable; retrying.";
    }
    schedulePoll();
  }

  function schedulePoll() {
    clearPoll();
    if (!currentAttempt || !attemptPresentation(currentAttempt).poll) return;
    if (typeof browserWindow?.setTimeout !== "function") return;
    pollTimer = browserWindow.setTimeout(
      () => refreshAttempt(),
      POLL_INTERVAL_MS,
    );
  }

  async function loadSettings() {
    status.textContent = "Loading settings…";
    try {
      const payload = await fetchJson(fetchImpl, SETTINGS_ENDPOINT);
      configured.textContent = payload.configured
        ? "Vast API key is configured."
        : "Vast API key is not configured.";
      priceInput.value = String(payload.max_price_per_hour);
      vramInput.value = String(payload.min_vram_gb);
      if (currentAttempt) renderCurrent();
      else status.textContent = "";
    } catch {
      status.textContent = "Settings could not be loaded.";
    }
  }

  const openDialog = async () => {
    dialog.showModal();
    await loadSettings();
  };
  launcher.addEventListener("click", openDialog);
  launcher.addEventListener("keydown", async (event) => {
    if (!["Enter", " ", "Spacebar"].includes(event.key)) return;
    event.preventDefault();
    await openDialog();
  });

  saveButton.addEventListener("click", async () => {
    const maxPrice = Number(priceInput.value);
    const minVram = Number(vramInput.value);
    if (
      !Number.isFinite(maxPrice) ||
      maxPrice < 0.01 ||
      maxPrice > 100 ||
      !Number.isInteger(minVram) ||
      minVram < 1 ||
      minVram > 1024
    ) {
      status.textContent = "Enter a valid price and whole-number VRAM value.";
      return;
    }
    const payload = {
      max_price_per_hour: maxPrice,
      min_vram_gb: minVram,
    };
    const key = apiKeyInput.value.trim();
    if (key) payload.api_key = key;

    setBusy(true);
    status.textContent = "Saving settings…";
    let failed = false;
    try {
      const result = await fetchJson(fetchImpl, SETTINGS_ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      apiKeyInput.value = "";
      configured.textContent = result.configured
        ? "Vast API key is configured."
        : "Vast API key is not configured.";
    } catch {
      failed = true;
    } finally {
      setBusy(false);
      status.textContent = failed
        ? "Settings could not be saved."
        : "Settings saved.";
    }
  });

  searchButton.addEventListener("click", async () => {
    clearPoll();
    selectedOffer = null;
    currentAttempt = null;
    idempotencyKey = null;
    selectionPreview.textContent = "";
    offers.replaceChildren();
    setBusy(true);
    status.textContent = "Searching Vast GPUs…";
    try {
      const result = await fetchJson(fetchImpl, OFFERS_ENDPOINT, {
        method: "POST",
      });
      if (!Array.isArray(result.offers)) throw new Error("search failed");
      renderOffers(document, offers, result.offers, (offer) => {
        const changed =
          String(selectedOffer?.offer_id ?? "") !== String(offer.offer_id);
        selectedOffer = offer;
        if (changed) {
          currentAttempt = null;
          idempotencyKey = null;
          selectionPreview.textContent = "";
        }
        renderCurrent();
      });
      const count = result.offers.length;
      status.textContent =
        `${count} matching offer${count === 1 ? "" : "s"} found.`;
    } catch {
      offers.replaceChildren();
      status.textContent = "Vast offer search is unavailable.";
    } finally {
      setBusy(false);
    }
  });

  previewButton.addEventListener("click", async () => {
    if (!selectedOffer) return;
    try {
      if (!idempotencyKey) {
        idempotencyKey = createIdempotencyKey(browserWindow);
      }
    } catch {
      status.textContent = "A secure confirmation key could not be generated.";
      return;
    }
    setBusy(true);
    status.textContent = "Creating a short-lived server quote…";
    let failureMessage = null;
    try {
      currentAttempt = await fetchJson(fetchImpl, QUOTES_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          offer_id: selectedOffer.offer_id,
          idempotency_key: idempotencyKey,
        }),
      });
    } catch (error) {
      const message =
        error instanceof Error ? String(error.message).trim() : "";
      failureMessage =
        message && message !== "request failed"
          ? message
          : "The selected offer could not be quoted.";
    } finally {
      setBusy(false);
      if (failureMessage) {
        status.textContent = failureMessage;
      }
    }
  });

  confirmButton.addEventListener("click", async () => {
    if (!currentAttempt?.attempt_id || !idempotencyKey) return;
    clearPoll();
    setBusy(true);
    status.textContent = "Submitting explicit paid confirmation…";
    let failed = false;
    try {
      currentAttempt = await fetchJson(
        fetchImpl,
        `${attemptEndpoint(currentAttempt.attempt_id)}/confirm`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ idempotency_key: idempotencyKey }),
        },
      );
    } catch {
      failed = true;
    } finally {
      setBusy(false);
      if (failed) {
        status.textContent =
          "The paid confirmation was not accepted. No retry was issued.";
      } else {
        schedulePoll();
      }
    }
  });

  cancelButton.addEventListener("click", async () => {
    if (!currentAttempt?.attempt_id) return;
    clearPoll();
    setBusy(true);
    status.textContent = "Requesting cancellation and verified cleanup…";
    let failed = false;
    try {
      currentAttempt = await fetchJson(
        fetchImpl,
        `${attemptEndpoint(currentAttempt.attempt_id)}/cancel`,
        { method: "POST" },
      );
    } catch {
      failed = true;
    } finally {
      setBusy(false);
      if (failed) {
        status.textContent =
          "Cancellation could not be confirmed. Check Vast inventory now.";
      } else {
        schedulePoll();
      }
    }
  });

  destroyButton.addEventListener("click", async () => {
    if (!currentAttempt?.attempt_id) return;
    clearPoll();
    setBusy(true);
    status.textContent = "Destroying the Vast instance and checking inventory…";
    let failed = false;
    try {
      currentAttempt = await fetchJson(
        fetchImpl,
        attemptEndpoint(currentAttempt.attempt_id),
        { method: "DELETE" },
      );
    } catch {
      failed = true;
    } finally {
      setBusy(false);
      if (failed) {
        status.textContent =
          "Destroy could not be verified. Use the Vast console immediately.";
      } else {
        schedulePoll();
      }
    }
  });

  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    apiKeyInput.value = "";
  });

  document.body.appendChild(dialog);
  const record = { dialog, launcher, observer: null, openDialog };
  mountedCloudRuns.set(document, record);
  ensureLauncherPlacement(browserWindow, document, record);
  return launcher;
}


export async function openCloudRun(document, fetchImpl, browserWindow = globalThis) {
  mountCloudRun(document, fetchImpl, browserWindow);
  return mountedCloudRuns.get(document).openDialog();
}


export function registerCloudRunWhenReady(browserWindow, document, tries = 0) {
  if (browserWindow.__cloudRunLifecycleRegistered) return true;
  const app = browserWindow.comfyAPI?.app?.app ?? browserWindow.app;
  const api = browserWindow.comfyAPI?.api?.api ?? browserWindow.api;
  if (
    !app ||
    typeof app.registerExtension !== "function" ||
    !api ||
    typeof api.fetchApi !== "function"
  ) {
    if (tries < 500 && typeof browserWindow.setTimeout === "function") {
      browserWindow.setTimeout(
        () => registerCloudRunWhenReady(browserWindow, document, tries + 1),
        10,
      );
    }
    return false;
  }

  const fetchImpl = api.fetchApi.bind(api);
  app.registerExtension({
    name: "comfyui-cloud-run.lifecycle",
    commands: [
      {
        id: OPEN_COMMAND_ID,
        label: "Cloud Run",
        function: () => openCloudRun(document, fetchImpl, browserWindow),
      },
    ],
    menuCommands: [
      {
        path: ["Extensions", "Vast Cloud Run"],
        commands: [OPEN_COMMAND_ID],
      },
    ],
    setup() {
      mountCloudRun(document, fetchImpl, browserWindow);
    },
  });
  browserWindow.__cloudRunLifecycleRegistered = true;
  return true;
}


export {
  OFFERS_ENDPOINT,
  OFFICIAL_TEMPLATE_ID,
  OFFICIAL_TEMPLATE_NAME,
  OPEN_COMMAND_ID,
  QUOTES_ENDPOINT,
  SETTINGS_ENDPOINT,
};


if (typeof window !== "undefined" && typeof document !== "undefined") {
  registerCloudRunWhenReady(window, document);
}
