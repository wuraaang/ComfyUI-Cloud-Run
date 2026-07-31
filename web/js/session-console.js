function element(document, tagName, { id, text, type, className } = {}) {
  const value = document.createElement(tagName);
  if (id) value.id = id;
  if (text !== undefined) value.textContent = text;
  if (type) value.type = type;
  if (className) value.className = className;
  return value;
}


function positiveInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}


export function createSessionConsole(document, api = {}, options = {}) {
  const root = element(document, "section", {
    id: options.id ?? "cloud-run-session-console",
    className: "cloud-run-session-console",
  });
  const title = element(document, "h3", { text: "Dependency preflight" });
  const summary = element(document, "div", {
    className: "cloud-run-preflight-summary",
  });
  const rows = element(document, "div", {
    className: "cloud-run-preflight-rows",
  });
  const controls = element(document, "div", {
    className: "cloud-run-actions",
  });
  const outputAllowanceInput = element(document, "input", {
    id: "cloud-run-output-allowance",
    type: "number",
  });
  outputAllowanceInput.setAttribute("min", "1");
  outputAllowanceInput.setAttribute("step", "1");
  outputAllowanceInput.setAttribute(
    "aria-label",
    "Explicit output allowance in bytes",
  );
  const preflightButton = element(document, "button", {
    id: "cloud-run-preflight",
    text: "Run free preflight",
    type: "button",
  });
  const searchButton = options.searchButton ?? element(document, "button", {
    id: "cloud-run-preflight-search",
    text: "Search Vast GPUs",
    type: "button",
  });
  const status = element(document, "div", {
    className: "cloud-run-status",
  });
  status.setAttribute("role", "status");
  preflightButton.disabled = true;
  searchButton.disabled = true;
  controls.append(outputAllowanceInput, preflightButton);
  if (!options.searchButton) controls.appendChild(searchButton);
  root.append(title, summary, rows, controls, status);

  let captureId = null;
  let currentPreflightId = null;
  let busy = false;
  let offerSearchReady = false;

  function setBusy(value) {
    busy = Boolean(value);
    preflightButton.disabled = busy || !captureId;
    searchButton.disabled = busy || !offerSearchReady;
  }

  function renderPreflight(payload) {
    const safeRows = Array.isArray(payload?.rows) ? payload.rows : [];
    rows.replaceChildren();
    for (const item of safeRows) {
      const row = element(document, "div", {
        className: "cloud-run-preflight-row",
      });
      const name = String(item?.display_name ?? "Unknown dependency");
      const state = String(item?.status ?? "unsupported");
      const size = positiveInteger(item?.size_bytes);
      const reason =
        typeof item?.reason === "string" && item.reason
          ? ` — ${item.reason}`
          : "";
      row.textContent =
        `${name}: ${state}` +
        `${size === null ? "" : ` — ${size} bytes`}` +
        reason;
      rows.appendChild(row);
    }
    const transfer = Number.isSafeInteger(payload?.transfer_bytes)
      && payload.transfer_bytes >= 0
      ? payload.transfer_bytes
      : 0;
    const output = positiveInteger(payload?.output_allowance_bytes);
    const disk = positiveInteger(payload?.disk_gb);
    summary.textContent =
      `Transfer: ${transfer} bytes. ` +
      `Output allowance: ${output === null ? "required" : `${output} bytes`}. ` +
      `Disk: ${disk === null ? "pending" : `${disk} GiB`}.`;
    currentPreflightId =
      typeof payload?.preflight_id === "string" && payload.preflight_id
        ? payload.preflight_id
        : null;
    const manifestReady =
      typeof payload?.manifest_digest === "string"
      && /^[0-9a-f]{64}$/.test(payload.manifest_digest);
    offerSearchReady =
      payload?.rentable === true
      && Boolean(currentPreflightId)
      && manifestReady
      && output !== null
      && disk !== null;
    searchButton.disabled = busy || !offerSearchReady;
    status.textContent = payload?.rentable === true
      ? "Preflight complete. Offer search is unlocked."
      : "Resolve every listed dependency before offer search.";
  }

  function setCapture(value) {
    captureId = typeof value === "string" && value ? value : null;
    currentPreflightId = null;
    offerSearchReady = false;
    rows.replaceChildren();
    summary.textContent = captureId
      ? "Captured canvas is ready for free dependency analysis."
      : "Capture the current canvas before preflight.";
    status.textContent = "";
    preflightButton.disabled = !captureId;
    searchButton.disabled = true;
  }

  preflightButton.addEventListener("click", async () => {
    if (!captureId || typeof api.preflight !== "function") return;
    const rawAllowance = String(outputAllowanceInput.value ?? "").trim();
    const allowance =
      rawAllowance === "" ? null : positiveInteger(rawAllowance);
    if (rawAllowance !== "" && allowance === null) {
      status.textContent =
        "Output allowance must be a positive whole number of bytes.";
      return;
    }
    setBusy(true);
    status.textContent = "Running free dependency preflight…";
    try {
      const result = await api.preflight(captureId, allowance);
      renderPreflight(result);
    } catch {
      currentPreflightId = null;
      offerSearchReady = false;
      searchButton.disabled = true;
      status.textContent = "Dependency preflight is unavailable.";
    } finally {
      setBusy(false);
      if (currentPreflightId === null) searchButton.disabled = true;
    }
  });

  if (typeof api.searchOffers === "function" && options.manageSearch !== false) {
    searchButton.addEventListener("click", async () => {
      if (!currentPreflightId || searchButton.disabled) return;
      await api.searchOffers(currentPreflightId);
    });
  }

  return {
    root,
    outputAllowanceInput,
    preflightButton,
    searchButton,
    status,
    renderPreflight,
    setCapture,
    get preflightId() {
      return currentPreflightId;
    },
  };
}
