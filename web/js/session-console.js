function element(document, tagName, { id, text, type, className } = {}) {
  const value = document.createElement(tagName);
  if (id) value.id = id;
  if (text !== undefined) value.textContent = text;
  if (type) value.type = type;
  if (className) value.className = className;
  return value;
}


function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}


function positiveInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}


function safeText(value, fallback = "") {
  return typeof value === "string" && value ? value : fallback;
}


function safeId(value) {
  return (
    typeof value === "string"
    && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value)
  )
    ? value
    : null;
}


function safeMappingCandidate(value) {
  if (
    !value
    || typeof value !== "object"
    || !safeId(value.class_type)
    || typeof value.candidate_digest !== "string"
    || !/^[0-9a-f]{64}$/.test(value.candidate_digest)
    || typeof value.revision !== "string"
    || !/^[0-9a-f]{40}$/.test(value.revision)
    || typeof value.repository_url !== "string"
    || !/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(
      value.repository_url.replace(/\.git$/, ""),
    )
    || value.approved !== false
  ) {
    return null;
  }
  return value;
}


function safeHuggingFaceProvenance(item) {
  const locator = item?.source_locator;
  const revision = item?.immutable_revision;
  if (
    item?.status !== "resolved"
    || item?.source_kind !== "huggingface"
    || typeof locator !== "string"
    || !locator
    || locator.length > 8192
    || locator.includes("%")
    || locator.includes("\\")
    || typeof revision !== "string"
    || !/^[0-9a-f]{40}$/.test(revision)
  ) {
    return null;
  }
  for (let index = 0; index < locator.length; index += 1) {
    const code = locator.charCodeAt(index);
    if (code < 33 || code > 126) return null;
  }

  let parsed;
  try {
    parsed = new URL(locator);
  } catch {
    return null;
  }
  if (
    parsed.protocol !== "https:"
    || parsed.hostname !== "huggingface.co"
    || parsed.username
    || parsed.password
    || parsed.port
    || parsed.hash
    || parsed.search
    || parsed.href !== locator
  ) {
    return null;
  }

  const parts = parsed.pathname.split("/");
  const repositoryPart = /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/;
  const pathPart = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$/;
  if (
    parts.length < 6
    || parts[0] !== ""
    || !repositoryPart.test(parts[1])
    || !repositoryPart.test(parts[2])
    || parts[3] !== "resolve"
    || parts[4] !== revision
    || !parts.slice(5).every((part) => pathPart.test(part))
  ) {
    return null;
  }

  const destination = item?.destination;
  const destinationParts = typeof destination === "string"
    ? destination.split("/")
    : [];
  if (
    !destination
    || destination.startsWith("/")
    || destination.includes("\\")
    || destinationParts.some((part) => !part || part === "." || part === "..")
  ) {
    return null;
  }
  const digest = typeof item?.sha256 === "string"
    && /^[0-9a-f]{64}$/.test(item.sha256)
    ? item.sha256.slice(0, 12)
    : null;
  if (digest === null) return null;

  const repository = `${parts[1]}/${parts[2]}`;
  return {
    repository,
    filePath: parts.slice(5).join("/"),
    repositoryUrl: `${parsed.origin}/${repository}`,
    destination,
    digest,
  };
}


function money(value) {
  const number = finiteNumber(value);
  return number !== null && number >= 0 ? `$${number.toFixed(2)}` : "unknown";
}


function bandwidthPrice(value) {
  const number = finiteNumber(value);
  return number !== null && number >= 0
    ? `$${number.toFixed(3)}/GB`
    : "unavailable";
}


function bytesText(value) {
  const bytes = Number(value);
  if (!Number.isSafeInteger(bytes) || bytes < 0) return "unknown size";
  if (bytes < 1024) return `${bytes} bytes`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && amount >= 1024; index += 1) {
    amount /= 1024;
    unit = units[index];
  }
  const rendered = Number.isInteger(amount)
    ? String(amount)
    : amount.toFixed(1);
  return `${rendered} ${unit}`;
}


function durationText(seconds) {
  const value = positiveInteger(seconds);
  if (value === null) return "no automatic limit";
  if (value % 3600 === 0) {
    return `${value / 3600} hour automatic limit`;
  }
  return `${Math.ceil(value / 60)} minute automatic limit`;
}


function epochText(value) {
  const epoch = finiteNumber(value);
  if (epoch === null || epoch < 0 || epoch > 253_402_300_799) {
    return "unavailable";
  }
  return new Date(epoch * 1000).toISOString();
}


function sessionMessage(session) {
  const messages = {
    preflight: "Resolve dependencies before selecting a paid session.",
    offer_selected: "Paid quote ready for explicit confirmation.",
    confirming: "Revalidating the exact Vast offer.",
    creating: "Creating the Vast instance; billing may have started.",
    bootstrapping: "Authenticating the project Remote Worker.",
    provisioning: "Transferring and installing verified dependencies.",
    validating: "Validating remote ComfyUI nodes and files.",
    repairing: "Applying the single approved repair budget.",
    ready: "GPU session ready for a remote job.",
    running: "Remote ComfyUI job is running.",
    harvesting: "Retrieving and verifying remote results locally.",
    destroy_requested: "GPU destruction requested and durably recorded.",
    destroying: "Destroying the GPU and verifying Vast inventory.",
    destroyed: "Vast inventory confirms that billing has stopped.",
    failed: "Cloud Run session needs attention.",
  };
  return messages[String(session?.status)] ?? "Unknown Cloud Run session state.";
}


function providerMutationMayHaveOccurred(session) {
  return new Set([
    "confirming",
    "creating",
    "bootstrapping",
    "provisioning",
    "validating",
    "repairing",
    "ready",
    "running",
    "harvesting",
    "destroy_requested",
    "destroying",
    "failed",
  ]).has(String(session?.status));
}


function pollableSession(session) {
  return new Set([
    "confirming",
    "creating",
    "bootstrapping",
    "provisioning",
    "validating",
    "repairing",
    "running",
    "harvesting",
    "destroy_requested",
    "destroying",
  ]).has(String(session?.status)) || session?.billing_may_continue === true;
}


function pollableJob(job) {
  return new Set([
    "captured",
    "resolving",
    "queued",
    "running",
    "harvesting",
  ]).has(String(job?.status));
}


function deadlineCanSynchronize(session) {
  return new Set([
    "bootstrapping",
    "provisioning",
    "validating",
    "repairing",
    "ready",
    "running",
    "harvesting",
  ]).has(String(session?.status));
}


export function createSessionConsole(document, api = {}, options = {}) {
  const root = element(document, "section", {
    id: options.id ?? "cloud-run-session-console",
    className: "cloud-run-session-console",
  });
  const title = element(document, "h3", { text: "Cloud Run session" });
  const settingsSummary = element(document, "div", {
    className: "cloud-run-configured-summary",
  });

  const preflightTitle = element(document, "h4", {
    text: "Dependency preflight",
  });
  const summary = element(document, "div", {
    className: "cloud-run-preflight-summary",
  });
  const rows = element(document, "div", {
    className: "cloud-run-preflight-rows",
  });
  const preflightControls = element(document, "div", {
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
  preflightButton.disabled = true;
  searchButton.disabled = true;
  preflightControls.append(outputAllowanceInput, preflightButton);
  if (!options.searchButton) preflightControls.appendChild(searchButton);

  const quotePanel = element(document, "section", {
    className: "cloud-run-paid-review",
  });
  quotePanel.hidden = true;
  const quoteTitle = element(document, "h4", {
    text: "Paid rental review",
  });
  const quoteDetails = element(document, "div");
  const confirmButton = element(document, "button", {
    id: "cloud-run-confirm-session",
    text: "Confirm & rent this GPU",
    type: "button",
    className: "cloud-run-danger",
  });
  confirmButton.disabled = true;
  quotePanel.append(quoteTitle, quoteDetails, confirmButton);

  const sessionPanel = element(document, "section", {
    className: "cloud-run-live-session",
  });
  sessionPanel.hidden = true;
  const sessionStatus = element(document, "div", {
    className: "cloud-run-session-status",
  });
  const costSummary = element(document, "div", {
    className: "cloud-run-cost-summary",
  });
  const provisioningStatus = element(document, "div", {
    className: "cloud-run-provisioning-status",
  });
  const deadlineWarning = element(document, "div", {
    className: "cloud-run-deadline-warning",
  });
  const alertSummary = element(document, "div", {
    className: "cloud-run-deadline-alerts",
  });
  const sessionError = element(document, "div", {
    className: "cloud-run-error",
  });
  sessionPanel.append(
    sessionStatus,
    costSummary,
    provisioningStatus,
    deadlineWarning,
    alertSummary,
    sessionError,
  );

  const deadlineControls = element(document, "div", {
    className: "cloud-run-actions",
  });
  const extendThirtyButton = element(document, "button", {
    text: "+30 minutes",
    type: "button",
  });
  const extendHourButton = element(document, "button", {
    text: "+1 hour",
    type: "button",
  });
  const noLimitCheckbox = element(document, "input", {
    id: "cloud-run-no-limit-ack",
    type: "checkbox",
  });
  const noLimitLabel = element(document, "label", {
    text: "I understand that disabling the limit can continue billing",
  });
  noLimitLabel.setAttribute("for", noLimitCheckbox.id);
  const disableDeadlineButton = element(document, "button", {
    text: "Disable automatic limit",
    type: "button",
    className: "cloud-run-danger",
  });
  deadlineControls.append(
    extendThirtyButton,
    extendHourButton,
    noLimitCheckbox,
    noLimitLabel,
    disableDeadlineButton,
  );
  sessionPanel.appendChild(deadlineControls);

  const jobPanel = element(document, "section", {
    className: "cloud-run-job",
  });
  jobPanel.hidden = true;
  const jobStatus = element(document, "div");
  const nodeProgress = element(document, "div");
  const jobError = element(document, "div", {
    className: "cloud-run-error",
  });
  const previews = element(document, "div", {
    className: "cloud-run-previews",
  });
  const outputs = element(document, "div", {
    className: "cloud-run-outputs",
  });
  jobPanel.append(jobStatus, nodeProgress, jobError, previews, outputs);

  const historyPanel = element(document, "section", {
    className: "cloud-run-history",
  });
  historyPanel.hidden = true;
  const historyTitle = element(document, "h4", {
    text: "Local job history",
  });
  const historyRows = element(document, "div");
  historyPanel.append(historyTitle, historyRows);

  const sessionControls = element(document, "div", {
    className: "cloud-run-actions",
  });
  const runNextJobButton = element(document, "button", {
    id: "cloud-run-next-job",
    text: "Run current canvas on this GPU",
    type: "button",
  });
  const destroyButton = element(document, "button", {
    id: "cloud-run-destroy-gpu",
    text: "Destroy GPU — stop all Vast billing",
    type: "button",
    className: "cloud-run-danger",
  });
  runNextJobButton.hidden = true;
  destroyButton.hidden = true;
  sessionControls.append(runNextJobButton, destroyButton);

  const destroyReview = element(document, "section", {
    className: "cloud-run-destroy-review cloud-run-danger",
  });
  destroyReview.hidden = true;
  const destroyReviewText = element(document, "div");
  const dataLossCheckbox = element(document, "input", {
    id: "cloud-run-data-loss-ack",
    type: "checkbox",
  });
  const dataLossLabel = element(document, "label", {
    text: "I accept permanent loss of every incomplete result",
  });
  dataLossLabel.setAttribute("for", dataLossCheckbox.id);
  const destroyNowButton = element(document, "button", {
    text: "Destroy now",
    type: "button",
    className: "cloud-run-danger",
  });
  destroyReview.append(
    destroyReviewText,
    dataLossCheckbox,
    dataLossLabel,
    destroyNowButton,
  );

  const status = element(document, "div", {
    className: "cloud-run-status",
  });
  status.setAttribute("role", "status");
  root.append(
    title,
    settingsSummary,
    preflightTitle,
    summary,
    rows,
    preflightControls,
    quotePanel,
    sessionPanel,
    jobPanel,
    historyPanel,
    sessionControls,
    destroyReview,
    status,
  );

  let captureId = null;
  let currentPreflightId = null;
  let offerSearchReady = false;
  let currentSession = null;
  let currentJob = null;
  let sessionIdempotencyKey = null;
  let destroyReviewToken = null;
  let explicitOutputAllowance = null;
  let eventCursor = 0;
  let eventState = {};
  let busy = false;
  let pollTimer = null;
  const history = new Map();
  let mappingButtons = [];

  function notifySession(session) {
    if (typeof options.onSession === "function") {
      options.onSession(session);
    }
  }

  function clearPoll() {
    if (
      pollTimer !== null
      && typeof options.clearTimeout === "function"
    ) {
      options.clearTimeout(pollTimer);
    }
    pollTimer = null;
  }

  function shouldPoll() {
    return pollableSession(currentSession) || pollableJob(currentJob);
  }

  function schedulePoll() {
    clearPoll();
    if (
      !shouldPoll()
      || typeof options.setTimeout !== "function"
      || typeof api.getSession !== "function"
    ) {
      return;
    }
    pollTimer = options.setTimeout(() => {
      void refresh();
    }, positiveInteger(options.pollIntervalMs) ?? 1000);
  }

  function setBusy(value) {
    busy = Boolean(value);
    preflightButton.disabled = busy || !captureId;
    searchButton.disabled = busy || !offerSearchReady;
    confirmButton.disabled =
      busy
      || currentSession?.status !== "offer_selected"
      || !sessionIdempotencyKey;
    const ready = currentSession?.status === "ready";
    const deadlineMutable = deadlineCanSynchronize(currentSession);
    runNextJobButton.disabled = busy || !ready;
    extendThirtyButton.disabled = busy || !deadlineMutable;
    extendHourButton.disabled = busy || !deadlineMutable;
    disableDeadlineButton.disabled = busy || !deadlineMutable;
    destroyButton.disabled =
      busy
      || !providerMutationMayHaveOccurred(currentSession)
      || ["destroy_requested", "destroying", "destroyed"].includes(
        String(currentSession?.status),
      );
    destroyNowButton.disabled = busy || !destroyReviewToken;
    for (const button of mappingButtons) button.disabled = busy;
  }

  function renderSettings(payload) {
    const configured = payload?.configured === true
      ? "Vast key configured"
      : "Vast key not configured";
    const cache = payload?.r2_configured === true
      ? "private cache configured"
      : "private cache optional";
    const sources = [
      (
        payload?.hf_configured === true
        || payload?.huggingface_configured === true
      ) ? "HF configured" : null,
      payload?.civitai_configured === true ? "Civitai configured" : null,
    ].filter(Boolean);
    settingsSummary.textContent =
      `${configured}; ${cache}` +
      `${sources.length ? `; ${sources.join(", ")}` : ""}. ` +
      "Account keys remain write-only on this Mac.";
  }

  function renderPreflight(payload) {
    const safeRows = Array.isArray(payload?.rows) ? payload.rows : [];
    rows.replaceChildren();
    mappingButtons = [];
    for (const [index, item] of safeRows.entries()) {
      const row = element(document, "div", {
        className: "cloud-run-preflight-row",
      });
      const name = safeText(item?.display_name, "Unknown dependency");
      const state = safeText(item?.status, "unsupported");
      const size = positiveInteger(item?.size_bytes);
      const source = safeText(item?.source_kind);
      const revision = safeText(item?.immutable_revision);
      const reason =
        typeof item?.reason === "string" && item.reason
          ? ` — ${item.reason}`
          : "";
      row.textContent =
        `${name}: ${state}` +
        `${source ? ` — ${source}` : ""}` +
        `${revision ? ` @ ${revision}` : ""}` +
        `${size === null ? "" : ` — ${bytesText(size)}`}` +
        reason;
      const provenance = safeHuggingFaceProvenance(item);
      if (provenance) {
        const details = element(document, "div", {
          className: "cloud-run-model-provenance",
        });
        const repositoryLink = element(document, "a", {
          text: provenance.repository,
        });
        repositoryLink.setAttribute("href", provenance.repositoryUrl);
        repositoryLink.setAttribute("target", "_blank");
        repositoryLink.setAttribute("rel", "noopener noreferrer");
        details.append(
          "Verified model: ",
          repositoryLink,
          `/${provenance.filePath} — destination ${provenance.destination}`,
          ` — SHA-256 ${provenance.digest}`,
        );
        row.appendChild(details);
      }
      const candidate = safeMappingCandidate(item?.mapping_candidate);
      if (
        state === "mapping_required"
        && candidate
        && typeof api.approveMapping === "function"
      ) {
        row.appendChild(element(document, "div", {
          text:
            `Pinned candidate: ${candidate.repository_url} @ ` +
            `${candidate.revision}; source ${safeText(
              candidate.origin_source_kind,
              candidate.source_kind,
            )}.`,
        }));
        const approve = element(document, "button", {
          id: `cloud-run-approve-mapping-${index}`,
          text: "Approve this pinned mapping",
          type: "button",
        });
        approve.addEventListener("click", async () => {
          if (busy) return;
          setBusy(true);
          status.textContent = "Recording explicit mapping approval…";
          try {
            await api.approveMapping(
              candidate.class_type,
              candidate.candidate_digest,
            );
            if (captureId && typeof api.preflight === "function") {
              status.textContent =
                "Mapping approved. Re-running dependency preflight…";
              renderPreflight(await api.preflight(
                captureId,
                explicitOutputAllowance,
              ));
            } else {
              status.textContent =
                "Mapping approved. Re-run dependency preflight.";
            }
          } catch {
            status.textContent =
              "Pinned mapping approval was rejected.";
          } finally {
            setBusy(false);
          }
        });
        mappingButtons.push(approve);
        row.appendChild(approve);
      }
      rows.appendChild(row);
    }
    const transfer = Number.isSafeInteger(payload?.transfer_bytes)
      && payload.transfer_bytes >= 0
      ? payload.transfer_bytes
      : 0;
    const output = positiveInteger(payload?.output_allowance_bytes);
    const disk = positiveInteger(payload?.disk_gb);
    summary.textContent =
      `Transfer: ${bytesText(transfer)}. ` +
      `Output allowance: ${output === null ? "required" : bytesText(output)}. ` +
      `Disk: ${disk === null ? "pending" : `${disk} GiB ephemeral`}.`;
    currentPreflightId =
      safeId(payload?.preflight_id);
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
    captureId = safeId(value);
    currentPreflightId = null;
    offerSearchReady = false;
    explicitOutputAllowance = null;
    rows.replaceChildren();
    summary.textContent = captureId
      ? "Captured canvas is ready for free dependency analysis."
      : "Capture the current canvas before preflight.";
    status.textContent = "";
    preflightButton.disabled = busy || !captureId;
    searchButton.disabled = true;
  }

  function renderQuote(payload, quoteOptions = {}) {
    const quote = payload?.offer ?? payload;
    if (!quote || typeof quote !== "object") return;
    if (safeId(payload?.session_id)) currentSession = payload;
    if (typeof quoteOptions.idempotencyKey === "string") {
      sessionIdempotencyKey = quoteOptions.idempotencyKey;
    }
    quotePanel.hidden = false;
    quoteDetails.replaceChildren();
    const values = [
      `Offer ${safeText(quote.offer_id, "unknown")}`,
      `${safeText(quote.gpu_name, "Unknown GPU")} — ` +
        `${finiteNumber(quote.gpu_ram_gb) ?? "unknown"} GB`,
      `${money(quote.dph_total)}/h; configured cap ` +
        `${money(quote.max_price_per_hour)}/h`,
      `Reliability: ${
        finiteNumber(quote.reliability) === null
          ? "unavailable"
          : `${(Number(quote.reliability) * 100).toFixed(1)}%`
      }`,
      `Bandwidth: ${bandwidthPrice(quote.inet_down_cost)} down; ` +
        `${bandwidthPrice(quote.inet_up_cost)} up`,
      `${positiveInteger(quote.disk_gb) ?? "unknown"} GB ephemeral disk`,
      `${bytesText(quote.transfer_bytes)} dependencies and inputs`,
      `${bytesText(quote.output_allowance_bytes)} output allowance`,
      durationText(quote.duration_seconds),
      `Maximum total instance creates: ${
        positiveInteger(quote.max_instance_creates) ?? "unknown"
      }`,
      `approximately ${money(quote.approximate_max_active_charge)} ` +
        "active/storage",
      `template ${safeText(quote.template_hash_id, "unavailable")}`,
      `worker ${safeText(quote.worker_commit, "unavailable")}`,
      `worker archive ${safeText(
        quote.worker_archive_sha256,
        "unavailable",
      )}`,
      `protocol ${safeText(quote.protocol_version, "unavailable")}`,
      `manifest ${safeText(quote.manifest_digest, "unavailable")}`,
      "bandwidth pricing can change the final provider charge",
    ];
    for (const value of values) {
      quoteDetails.appendChild(element(document, "div", { text: value }));
    }
    quoteDetails.appendChild(element(document, "strong", {
      text:
        "No rental exists until Confirm & rent this GPU is pressed. " +
        "After confirmation, billing continues until verified destruction.",
    }));
    confirmButton.hidden = payload?.status !== "offer_selected";
    setBusy(busy);
  }

  function renderProvisioning(session) {
    const progress = session?.provisioning;
    const statusValue = String(session?.status ?? "");
    if (!new Set([
      "bootstrapping",
      "provisioning",
      "validating",
      "repairing",
    ]).has(statusValue)) {
      provisioningStatus.className = "cloud-run-provisioning-status";
      provisioningStatus.textContent = "";
      return;
    }
    const sessionPhase = {
      bootstrapping: "Worker authentication",
      provisioning: "Verified transfer/install",
      validating: "Remote /object_info validation",
      repairing: "One approved repair",
    }[statusValue];
    const workerPhases = {
      dependency_transfer: "Dependency transfer",
      model_transfer: "Model transfer",
      digest_verification: "Digest verification",
      comfyui_startup: "ComfyUI startup",
      environment_validation: "Environment validation",
      ready: "Provisioning ready",
    };
    const workerPhase = typeof progress?.phase === "string"
      && Object.hasOwn(workerPhases, progress.phase)
      ? progress.phase
      : null;
    const phase = workerPhase === null
      ? sessionPhase
      : workerPhases[workerPhase];
    const transferred = Number.isSafeInteger(progress?.transferred_bytes)
      && progress.transferred_bytes >= 0
      ? progress.transferred_bytes
      : null;
    const total = Number.isSafeInteger(progress?.total_bytes)
      && progress.total_bytes >= 0
      && transferred !== null
      && transferred <= progress.total_bytes
      ? progress.total_bytes
      : null;
    const bytes =
      transferred !== null && total !== null
        ? ` — ${bytesText(transferred)} of ${bytesText(total)}`
        : "";
    const currentModel = (
      workerPhase !== null
      && ["model_transfer", "digest_verification"].includes(workerPhase)
      && typeof progress?.current_model === "string"
      && progress.current_model
      && progress.current_model.length <= 500
      && !Array.from(progress.current_model).some(
        (character) => character.charCodeAt(0) < 32,
      )
    )
      ? progress.current_model
      : null;
    const installs = positiveInteger(progress?.installed_units);
    const validation = positiveInteger(progress?.validated_units);
    const secondsWithoutProgress = Number(progress?.seconds_without_progress);
    const safeSeconds = (
      Number.isSafeInteger(secondsWithoutProgress)
      && secondsWithoutProgress >= 0
    )
      ? secondsWithoutProgress
      : null;
    const stallBudget = positiveInteger(progress?.stall_budget_seconds) ?? 600;
    const stalled = safeSeconds !== null && safeSeconds >= stallBudget;
    provisioningStatus.className = stalled
      ? "cloud-run-provisioning-status cloud-run-danger"
      : "cloud-run-provisioning-status";
    provisioningStatus.textContent =
      `${phase}${bytes}` +
      `${currentModel === null ? "" : ` — model ${currentModel}`}` +
      `${installs === null ? "" : ` — ${installs} install units`}` +
      `${validation === null ? "" : ` — ${validation} validations`}` +
      (
        safeSeconds === null
          ? ` — aborts after ${stallBudget} seconds without meaningful progress.`
          : stalled
            ? ` — STALL DETECTED — ${safeSeconds} seconds since meaningful ` +
              `progress; ${stallBudget}-second server limit reached.`
            : ` — ${safeSeconds} seconds since meaningful progress; ` +
              `${stallBudget}-second server limit.`
      );
  }

  function renderDeadline(session) {
    const alerts = Array.isArray(session?.deadline_alerts)
      ? session.deadline_alerts
      : [];
    if (session?.deadline_mode === "none") {
      deadlineWarning.className =
        "cloud-run-deadline-warning cloud-run-danger";
      deadlineWarning.textContent =
        "RED WARNING — automatic deadline disabled; Vast billing has no " +
        "Cloud Run time limit.";
    } else {
      deadlineWarning.className = "cloud-run-deadline-warning";
      const deadline = finiteNumber(session?.deadline_at);
      deadlineWarning.textContent = deadline === null
        ? "Finite deadline is unavailable."
        : `Finite automatic destruction deadline: ${epochText(deadline)}.`;
    }
    if (session?.deadline_sync_pending === true) {
      deadlineWarning.textContent +=
        " A deadline update is pending worker synchronization; the earlier " +
        "finite boundary remains effective.";
    }
    const labels = {
      "15_minutes": "15 minutes remain",
      "5_minutes": "5 minutes remain",
      deadline_reached: "deadline reached — destruction is mandatory",
    };
    alertSummary.textContent = alerts
      .map((value) => labels[String(value)])
      .filter(Boolean)
      .join("; ");
  }

  function renderHistory() {
    historyRows.replaceChildren();
    const jobs = [...history.values()];
    historyPanel.hidden = jobs.length === 0;
    for (const job of jobs) {
      const error = typeof job.error === "string"
        ? job.error
        : job.error?.message;
      historyRows.appendChild(element(document, "div", {
        text:
          `${safeText(job.job_id, "unknown job")}: ` +
          `${safeText(job.status, "unknown")}` +
          `${error ? ` — ${String(error)}` : ""}`,
      }));
    }
  }

  function normalizedPreview(item) {
    const id = safeId(item?.id ?? item?.preview_id);
    return id ? { ...item, id } : null;
  }

  function normalizedOutput(item) {
    const id = safeId(item?.id ?? item?.artifact_id);
    return id ? { ...item, id } : null;
  }

  function renderJob(job) {
    if (!job || typeof job !== "object") return;
    const identifier = safeId(job.job_id);
    if (identifier && identifier !== currentJob?.job_id) {
      eventCursor = 0;
      eventState = {};
    }
    currentJob = { ...job };
    if (identifier) history.set(identifier, currentJob);
    jobPanel.hidden = false;
    jobStatus.textContent =
      `Job ${identifier ?? "unknown"}: ` +
      `${safeText(job.status, "unknown")}.`;
    const currentNode = job.current_node ?? eventState.currentNode;
    const progress = job.progress ?? eventState.progress;
    const progressText = safeText(
      job.progress_text ?? eventState.progressText,
    );
    const nodeTitle = safeText(
      currentNode?.title,
      safeText(currentNode?.id, "unknown node"),
    );
    const value = finiteNumber(progress?.value);
    const maximum = finiteNumber(progress?.max ?? progress?.total);
    nodeProgress.textContent =
      currentNode
        ? `Current remote node: ${nodeTitle}` +
          (
            value !== null && maximum !== null
              ? ` — ${value} of ${maximum}`
              : ""
          ) +
          `${progressText ? ` — ${progressText}` : ""}`
        : progressText;
    const error = job.error ?? eventState.error;
    const errorMessage = typeof error === "string" ? error : error?.message;
    jobError.textContent = errorMessage
      ? `Remote error: ${String(errorMessage)}`
      : "";

    const previewItems = [
      ...(Array.isArray(eventState.previews) ? eventState.previews : []),
      ...(Array.isArray(job.previews) ? job.previews : []),
    ].map(normalizedPreview).filter(Boolean);
    const uniquePreviews = new Map(
      previewItems.map((item) => [item.id, item]),
    );
    previews.replaceChildren();
    for (const item of uniquePreviews.values()) {
      if (
        typeof api.previewUrl !== "function"
        || !safeId(currentSession?.session_id)
        || !identifier
      ) {
        continue;
      }
      const image = element(document, "img");
      image.setAttribute(
        "src",
        api.previewUrl(currentSession.session_id, identifier, item.id),
      );
      image.setAttribute("alt", `Remote preview ${item.id}`);
      image.setAttribute("loading", "lazy");
      previews.appendChild(image);
    }

    const outputItems = Array.isArray(job.outputs)
      ? job.outputs.map(normalizedOutput).filter(Boolean)
      : [];
    outputs.replaceChildren();
    for (const item of outputItems) {
      const filename = safeText(item.filename, item.id);
      if (
        item.state === "local_verified"
        && typeof api.artifactUrl === "function"
        && safeId(currentSession?.session_id)
        && identifier
      ) {
        const link = element(document, "a", { text: filename });
        link.setAttribute(
          "href",
          api.artifactUrl(currentSession.session_id, identifier, item.id),
        );
        link.setAttribute("download", filename);
        outputs.appendChild(link);
      } else {
        outputs.appendChild(element(document, "div", {
          text: `${filename}: ${safeText(item.state, "unverified")}`,
        }));
      }
    }
    renderHistory();
    setBusy(busy);
  }

  function applyEvents(payload) {
    if (
      !payload
      || !Array.isArray(payload.events)
      || !Number.isSafeInteger(payload.last_sequence)
      || payload.last_sequence < eventCursor
    ) {
      return;
    }
    const previewMap = new Map(
      (eventState.previews ?? []).map((item) => [item.id, item]),
    );
    for (const event of payload.events) {
      const data = event?.data && typeof event.data === "object"
        ? event.data
        : {};
      if (event?.type === "executing" && safeId(data.node_id)) {
        eventState.currentNode = { id: data.node_id };
      } else if (event?.type === "progress") {
        eventState.progress = {
          value: finiteNumber(data.value),
          max: finiteNumber(data.max ?? data.total),
        };
      } else if (event?.type === "progress_text") {
        eventState.progressText = safeText(data.text);
      } else if (
        ["b_preview", "b_preview_with_metadata"].includes(event?.type)
        && safeId(data.preview_id)
      ) {
        previewMap.set(data.preview_id, {
          id: data.preview_id,
          state: "verified",
        });
      } else if (
        ["execution_error", "execution_interrupted"].includes(event?.type)
      ) {
        eventState.error = {
          code: safeText(data.code, "execution_error"),
          message: safeText(data.message, "Remote execution failed."),
        };
      }
    }
    eventState.previews = [...previewMap.values()];
    eventCursor = payload.last_sequence;
    if (currentJob) renderJob(currentJob);
  }

  function renderSession(session) {
    if (!session || typeof session !== "object") return;
    currentSession = session;
    if (session.offer) renderQuote(session, {
      idempotencyKey: sessionIdempotencyKey,
    });
    sessionPanel.hidden = false;
    sessionStatus.textContent =
      `${sessionMessage(session)} ` +
      `Session ${safeText(session.session_id, "unknown")}` +
      `${session.instance_id ? `; Vast instance ${String(session.instance_id)}` : ""}.`;
    const rate = finiteNumber(session?.offer?.dph_total);
    const elapsed = finiteNumber(session.elapsed_seconds)
      ?? (
        finiteNumber(session.created_at) !== null
        && typeof options.now === "function"
          ? Math.max(0, options.now() - Number(session.created_at))
          : null
      );
    const spend = finiteNumber(session.approximate_spend)
      ?? (
        rate !== null && elapsed !== null
          ? rate * elapsed / 3600
          : null
      );
    costSummary.textContent =
      `Rate: ${rate === null ? "unknown" : `${money(rate)}/h`}. ` +
      `Elapsed: ${elapsed === null ? "unknown" : `${Math.floor(elapsed)} seconds`}. ` +
      `Approximate active spend: ${spend === null ? "unknown" : money(spend)}.`;
    renderProvisioning(session);
    renderDeadline(session);
    sessionError.textContent = session.error
      ? `Session error: ${String(session.error)}`
      : "";
    if (session.billing_may_continue === true) {
      sessionError.textContent +=
        " Warning — Vast billing may still be active. " +
        safeText(
          session.emergency_action,
          "Use the Vast console immediately.",
        );
      sessionError.className = "cloud-run-error cloud-run-danger";
    } else {
      sessionError.className = "cloud-run-error";
    }

    const ready = session.status === "ready";
    runNextJobButton.hidden = !ready;
    deadlineControls.hidden = !deadlineCanSynchronize(session);
    destroyButton.hidden = !providerMutationMayHaveOccurred(session);
    if (session.status === "destroyed") {
      destroyReview.hidden = true;
      destroyReviewToken = null;
      clearPoll();
    }
    if (session.current_job) renderJob(session.current_job);
    if (Array.isArray(session.history)) {
      for (const job of session.history) {
        if (safeId(job?.job_id)) history.set(job.job_id, job);
      }
      renderHistory();
    }
    setBusy(busy);
    notifySession(session);
    schedulePoll();
  }

  async function refresh() {
    pollTimer = null;
    try {
      if (
        safeId(currentSession?.session_id)
        && typeof api.getSession === "function"
      ) {
        renderSession(await api.getSession(currentSession.session_id));
      }
      if (
        safeId(currentSession?.session_id)
        && safeId(currentJob?.job_id)
        && typeof api.getJob === "function"
      ) {
        renderJob(await api.getJob(
          currentSession.session_id,
          currentJob.job_id,
        ));
      }
      if (
        safeId(currentSession?.session_id)
        && safeId(currentJob?.job_id)
        && typeof api.getEvents === "function"
      ) {
        applyEvents(await api.getEvents(
          currentSession.session_id,
          currentJob.job_id,
          eventCursor,
        ));
      }
    } catch {
      status.textContent =
        "Session status is temporarily unavailable; safe polling continues.";
    }
    schedulePoll();
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
    explicitOutputAllowance = allowance;
    status.textContent = "Running free dependency preflight…";
    try {
      renderPreflight(await api.preflight(captureId, allowance));
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

  confirmButton.addEventListener("click", async () => {
    if (
      !safeId(currentSession?.session_id)
      || !sessionIdempotencyKey
      || typeof api.confirmSession !== "function"
    ) {
      return;
    }
    setBusy(true);
    status.textContent = "Submitting explicit paid confirmation…";
    try {
      renderSession(await api.confirmSession(
        currentSession.session_id,
        sessionIdempotencyKey,
      ));
      status.textContent =
        "Paid confirmation accepted; provisioning is server-driven.";
    } catch {
      status.textContent =
        "Paid confirmation was not accepted; no automatic retry was issued.";
    } finally {
      setBusy(false);
      schedulePoll();
    }
  });

  runNextJobButton.addEventListener("click", async () => {
    if (
      currentSession?.status !== "ready"
      || typeof options.capture !== "function"
      || typeof options.newIdempotencyKey !== "function"
      || typeof api.createJob !== "function"
    ) {
      return;
    }
    setBusy(true);
    status.textContent =
      "Compiling the current canvas without local execution…";
    try {
      const captured = await options.capture();
      const nextCaptureId = safeId(
        typeof captured === "string" ? captured : captured?.capture_id,
      );
      const key = safeId(options.newIdempotencyKey());
      if (!nextCaptureId || !key) throw new Error("invalid capture");
      setCapture(nextCaptureId);
      renderJob(await api.createJob(
        currentSession.session_id,
        nextCaptureId,
        key,
      ));
      status.textContent = "Remote job submitted to the current GPU session.";
    } catch {
      status.textContent =
        "The fresh canvas could not be submitted to this GPU session.";
    } finally {
      setBusy(false);
      schedulePoll();
    }
  });

  async function updateDeadline(action, acknowledged = undefined) {
    if (
      !safeId(currentSession?.session_id)
      || typeof api.updateDeadline !== "function"
    ) {
      return;
    }
    const payload = acknowledged === undefined
      ? { action }
      : { action, acknowledged };
    setBusy(true);
    status.textContent = "Synchronizing the deadline with the Remote Worker…";
    try {
      renderSession(await api.updateDeadline(
        currentSession.session_id,
        payload,
      ));
      status.textContent = "Deadline synchronized.";
    } catch {
      status.textContent =
        "Deadline synchronization failed; the earlier finite limit remains effective.";
    } finally {
      setBusy(false);
    }
  }

  extendThirtyButton.addEventListener("click", () => (
    updateDeadline("add_30_minutes")
  ));
  extendHourButton.addEventListener("click", () => (
    updateDeadline("add_1_hour")
  ));
  disableDeadlineButton.addEventListener("click", async () => {
    if (noLimitCheckbox.checked !== true) {
      status.textContent =
        "Explicitly acknowledge unlimited billing before disabling the limit.";
      return;
    }
    await updateDeadline("disable", true);
  });

  destroyButton.addEventListener("click", async () => {
    if (
      !safeId(currentSession?.session_id)
      || typeof api.reviewDestroy !== "function"
    ) {
      return;
    }
    setBusy(true);
    status.textContent = "Preparing irreversible destruction review…";
    try {
      const review = await api.reviewDestroy(currentSession.session_id);
      if (
        !review
        || !safeId(review.session_id)
        || typeof review.review_token !== "string"
        || review.review_token.length < 32
      ) {
        throw new Error("invalid review");
      }
      destroyReviewToken = review.review_token;
      dataLossCheckbox.checked = false;
      destroyReview.hidden = false;
      const artifacts = Array.isArray(review.unverified_artifact_ids)
        ? review.unverified_artifact_ids.map(String)
        : [];
      destroyReviewText.textContent =
        `${safeText(
          review.warning,
          "Incomplete results will be irreversibly lost.",
        )} ` +
        `Instance: ${safeText(review.instance_id, "pending identity")}. ` +
        (
          artifacts.length
            ? `Unverified outputs: ${artifacts.join(", ")}.`
            : "No unverified local output is currently listed."
        );
      status.textContent =
        "Review data loss, tick the acknowledgement, then Destroy now.";
    } catch {
      destroyReviewToken = null;
      destroyReview.hidden = true;
      status.textContent =
        "Destruction review is unavailable; check Vast inventory immediately.";
    } finally {
      setBusy(false);
    }
  });

  destroyNowButton.addEventListener("click", async () => {
    if (
      !destroyReviewToken
      || dataLossCheckbox.checked !== true
      || !safeId(currentSession?.session_id)
      || typeof api.destroySession !== "function"
    ) {
      status.textContent =
        "Final destruction requires the explicit data-loss acknowledgement.";
      return;
    }
    const token = destroyReviewToken;
    destroyReviewToken = null;
    setBusy(true);
    status.textContent =
      "Destroying the GPU and verifying fresh Vast inventory…";
    try {
      renderSession(await api.destroySession(
        currentSession.session_id,
        {
          review_token: token,
          acknowledge_data_loss: true,
        },
      ));
      destroyReview.hidden = true;
      status.textContent =
        currentSession?.status === "destroyed"
          ? "GPU destroyed; Vast inventory confirms billing stopped."
          : "Destruction is still being verified.";
    } catch {
      destroyReview.hidden = true;
      status.textContent =
        "Destruction could not be verified. Use the Vast console immediately.";
    } finally {
      setBusy(false);
      schedulePoll();
    }
  });

  setCapture(null);
  setBusy(false);

  return {
    root,
    settingsSummary,
    outputAllowanceInput,
    preflightButton,
    searchButton,
    status,
    quotePanel,
    confirmButton,
    sessionPanel,
    provisioningStatus,
    deadlineWarning,
    runNextJobButton,
    destroyButton,
    destroyReview,
    dataLossCheckbox,
    destroyNowButton,
    extendThirtyButton,
    extendHourButton,
    noLimitCheckbox,
    disableDeadlineButton,
    renderSettings,
    renderPreflight,
    renderQuote,
    renderSession,
    renderJob,
    applyEvents,
    setCapture,
    refresh,
    stopPolling: clearPoll,
    get preflightId() {
      return currentPreflightId;
    },
    get session() {
      return currentSession;
    },
    get job() {
      return currentJob;
    },
  };
}
