const SETTINGS_ENDPOINT = "/cloud-run/api/settings";
const OFFERS_ENDPOINT = "/cloud-run/api/offers";
const OFFICIAL_TEMPLATE_ID = "57808457573e32120301649763d8e019";
const OFFICIAL_TEMPLATE_NAME = "Official ComfyUI";


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
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 9998;
  border: 1px solid var(--border-color, #4b5563);
  border-radius: 8px;
  padding: 9px 14px;
  background: var(--comfy-menu-bg, #20242b);
  color: var(--input-text, #f3f4f6);
  box-shadow: 0 5px 18px rgba(0, 0, 0, .35);
  cursor: pointer;
  font: 600 13px system-ui, sans-serif;
}
#cloud-run-modal {
  width: min(520px, calc(100vw - 32px));
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
.cloud-run-actions button { padding: 7px 10px; cursor: pointer; }
.cloud-run-status { min-height: 1.4em; color: #d1d5db; }
.cloud-run-offers { display: grid; gap: 7px; margin: 10px 0; }
.cloud-run-preview { margin-top: 10px; padding: 10px; border-radius: 7px; background: rgba(255,255,255,.06); }
`;
  document.head.appendChild(style);
}


function decimalText(value) {
  return Number(value).toFixed(1).replace(/\.0$/, "");
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
    details.textContent =
      `${String(offer.gpu_name)} — ` +
      `${decimalText(offer.gpu_ram_gb)} GB — ` +
      `$${Number(offer.dph_total).toFixed(2)}/h — ` +
      `${(Number(offer.reliability) * 100).toFixed(1)}% reliability`;
    selector.addEventListener("click", () => onSelect(offer));
    row.append(selector, details);
    container.appendChild(row);
  });
}


export function renderSelectionPreview(document, container, offer) {
  container.replaceChildren();
  const heading = createElement(document, "strong", {
    text: "Selection preview",
  });
  const gpu = createElement(document, "div");
  gpu.textContent = `GPU: ${String(offer.gpu_name)}`;
  const price = createElement(document, "div");
  price.textContent = `Price: $${Number(offer.dph_total).toFixed(2)}/h`;
  const template = createElement(document, "div");
  template.textContent =
    `Template: ${OFFICIAL_TEMPLATE_NAME} (${OFFICIAL_TEMPLATE_ID})`;
  const confirmation = createElement(document, "div", {
    text: "Preview only — No instance was created.",
  });
  container.append(heading, gpu, price, template, confirmation);
}


export function mountCloudRun(document, fetchImpl) {
  const existing = document.getElementById("cloud-run-button");
  if (existing) return existing;

  ensureStyles(document);

  const launcher = createElement(document, "button", {
    id: "cloud-run-button",
    testId: "cloud-run-button",
    text: "Cloud Run",
    type: "button",
  });
  launcher.setAttribute("aria-haspopup", "dialog");
  launcher.setAttribute("aria-controls", "cloud-run-modal");

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
    text: "Preview only — no instance will be rented.",
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
    text: "Preview selection",
    type: "button",
  });
  previewButton.disabled = true;
  actions.append(saveButton, searchButton, previewButton);

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

  async function loadSettings() {
    status.textContent = "Loading settings…";
    try {
      const response = await fetchImpl(SETTINGS_ENDPOINT);
      const payload = await response.json();
      if (!response.ok) throw new Error("settings unavailable");
      configured.textContent = payload.configured
        ? "Vast API key is configured."
        : "Vast API key is not configured.";
      priceInput.value = String(payload.max_price_per_hour);
      vramInput.value = String(payload.min_vram_gb);
      status.textContent = "";
    } catch {
      status.textContent = "Settings could not be loaded.";
    }
  }

  launcher.addEventListener("click", async () => {
    dialog.showModal();
    await loadSettings();
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

    saveButton.disabled = true;
    status.textContent = "Saving settings…";
    try {
      const response = await fetchImpl(SETTINGS_ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error("save failed");
      apiKeyInput.value = "";
      configured.textContent = result.configured
        ? "Vast API key is configured."
        : "Vast API key is not configured.";
      status.textContent = "Settings saved.";
    } catch {
      status.textContent = "Settings could not be saved.";
    } finally {
      saveButton.disabled = false;
    }
  });
  searchButton.addEventListener("click", async () => {
    selectedOffer = null;
    previewButton.disabled = true;
    selectionPreview.textContent = "";
    offers.replaceChildren();
    searchButton.disabled = true;
    status.textContent = "Searching Vast GPUs…";
    try {
      const response = await fetchImpl(OFFERS_ENDPOINT, { method: "POST" });
      const result = await response.json();
      if (!response.ok || !Array.isArray(result.offers)) {
        throw new Error("search failed");
      }
      renderOffers(document, offers, result.offers, (offer) => {
        selectedOffer = offer;
        previewButton.disabled = false;
        selectionPreview.textContent = "";
      });
      const count = result.offers.length;
      status.textContent = `${count} matching offer${count === 1 ? "" : "s"} found.`;
    } catch {
      offers.replaceChildren();
      status.textContent = "Vast offer search is unavailable.";
    } finally {
      searchButton.disabled = false;
    }
  });
  previewButton.addEventListener("click", () => {
    if (!selectedOffer) return;
    renderSelectionPreview(document, selectionPreview, selectedOffer);
  });
  closeButton.addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => {
    apiKeyInput.value = "";
  });

  document.body.append(dialog, launcher);
  return launcher;
}


export function registerCloudRunWhenReady(browserWindow, document, tries = 0) {
  if (browserWindow.__cloudRunPreviewRegistered) return true;

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

  app.registerExtension({
    name: "comfyui-cloud-run.preview",
    setup() {
      mountCloudRun(document, api.fetchApi.bind(api));
    },
  });
  browserWindow.__cloudRunPreviewRegistered = true;
  return true;
}


export {
  OFFERS_ENDPOINT,
  OFFICIAL_TEMPLATE_ID,
  OFFICIAL_TEMPLATE_NAME,
  SETTINGS_ENDPOINT,
};


if (typeof window !== "undefined" && typeof document !== "undefined") {
  registerCloudRunWhenReady(window, document);
}
