export const DESKTOP_CONTEXT_ENDPOINT = "/cloud-run/api/desktop-context";

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/;
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const MAX_BOOTSTRAP_BYTES = 16 * 1024 * 1024;
const installedPromptWrappers = new WeakMap();
const canvasStates = new WeakMap();


function contextError() {
  return new Error("ComfyUI Vast Desktop context is unavailable.");
}


function exactLoopbackAgentUrl(value) {
  if (typeof value !== "string" || value.length > 512) return false;
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  return (
    parsed.protocol === "ws:"
    && parsed.hostname === "127.0.0.1"
    && /^[1-9][0-9]{0,4}$/.test(parsed.port)
    && Number(parsed.port) <= 65535
    && parsed.pathname === "/cloud-run/api/agent/ws"
    && !parsed.username
    && !parsed.password
    && !parsed.search
    && !parsed.hash
  );
}


function nonnegativeInteger(value) {
  return Number.isSafeInteger(value) && value >= 0;
}


function normalizeContext(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw contextError();
  }
  if (value.role === "local") {
    if (Object.keys(value).some((key) => key !== "role")) {
      throw contextError();
    }
    return { role: "local" };
  }
  const optional = new Set([
    "bootstrap_revision",
    "active_revision",
    "remote_edit_revision",
    "bootstrap_workflow",
  ]);
  if (
    value.role !== "vast"
    || !IDENTIFIER.test(value.session_id ?? "")
    || !nonnegativeInteger(value.profile_revision)
    || !exactLoopbackAgentUrl(value.agent_bridge_url)
    || Object.keys(value).some((key) => !new Set([
      "role",
      "session_id",
      "profile_revision",
      "agent_bridge_url",
      ...optional,
    ]).has(key))
  ) {
    throw contextError();
  }
  for (const name of [
    "bootstrap_revision",
    "active_revision",
    "remote_edit_revision",
  ]) {
    if (name in value && !nonnegativeInteger(value[name])) {
      throw contextError();
    }
  }
  if (
    "bootstrap_workflow" in value
    && (
      !value.bootstrap_workflow
      || typeof value.bootstrap_workflow !== "object"
      || Array.isArray(value.bootstrap_workflow)
    )
  ) {
    throw contextError();
  }
  return { ...value };
}


export async function readDesktopContext(fetchImpl) {
  if (typeof fetchImpl !== "function") throw contextError();
  let response;
  let payload;
  try {
    response = await fetchImpl(DESKTOP_CONTEXT_ENDPOINT, undefined);
    if (!response || response.ok !== true) throw contextError();
    payload = await response.json();
  } catch {
    throw contextError();
  }
  return normalizeContext(payload);
}


export function isVastRole(context) {
  try {
    return normalizeContext(context).role === "vast";
  } catch {
    return false;
  }
}


function nativePromptPost(route, options) {
  return (
    route === "/prompt"
    && String(options?.method ?? "GET").toUpperCase() === "POST"
  );
}


export function installNativePromptIdentity(api, cryptoImpl = globalThis.crypto) {
  if (!api || typeof api.fetchApi !== "function") {
    throw new Error("ComfyUI native API is unavailable.");
  }
  const installed = installedPromptWrappers.get(api);
  if (installed) return installed.restore;
  if (typeof cryptoImpl?.randomUUID !== "function") {
    throw new Error("Secure prompt identity is unavailable.");
  }

  const original = api.fetchApi;
  const invoke = original.bind(api);
  const wrapped = async function cloudVastFetch(route, options = {}) {
    if (!nativePromptPost(route, options)) {
      return invoke(route, options);
    }
    const headers = new Headers(options.headers ?? {});
    if (headers.has("X-Cloud-Vast-Request-Id")) {
      throw new Error("Cloud Vast prompt identity was already set.");
    }
    const requestId = cryptoImpl.randomUUID();
    if (typeof requestId !== "string" || !UUID_V4.test(requestId)) {
      throw new Error("Secure prompt identity is unavailable.");
    }
    headers.set("X-Cloud-Vast-Request-Id", requestId);
    return invoke(route, { ...options, headers });
  };
  const restore = () => {
    const current = installedPromptWrappers.get(api);
    if (current?.wrapped === wrapped && api.fetchApi === wrapped) {
      api.fetchApi = original;
      installedPromptWrappers.delete(api);
    }
  };
  api.fetchApi = wrapped;
  installedPromptWrappers.set(api, { original, wrapped, restore });
  return restore;
}


function graphMarker(app) {
  const graph = app?.graph;
  if (!graph) return "missing";
  try {
    if (typeof graph.serialize === "function") {
      return JSON.stringify(graph.serialize());
    }
  } catch {
    return null;
  }
  if (Number.isSafeInteger(graph._version)) return `version:${graph._version}`;
  if (Array.isArray(graph._nodes)) {
    return JSON.stringify(graph._nodes.map((node) => [node?.id, node?.type]));
  }
  return null;
}


function bootstrapRecord(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const revision = value.bootstrap_revision;
  const activeRevision = value.active_revision;
  const remoteEditRevision = value.remote_edit_revision;
  const workflow = value.workflow ?? value.bootstrap_workflow;
  if (
    !Number.isSafeInteger(revision)
    || revision <= 0
    || !nonnegativeInteger(activeRevision)
    || !nonnegativeInteger(remoteEditRevision)
    || !workflow
    || typeof workflow !== "object"
    || Array.isArray(workflow)
  ) {
    return null;
  }
  let encoded;
  try {
    encoded = JSON.stringify(workflow);
  } catch {
    return null;
  }
  if (!encoded || new TextEncoder().encode(encoded).byteLength > MAX_BOOTSTRAP_BYTES) {
    return null;
  }
  return {
    bootstrap_revision: revision,
    active_revision: activeRevision,
    remote_edit_revision: remoteEditRevision,
    workflow: JSON.parse(encoded),
  };
}


export async function bootstrapVastCanvas(app, context, options = {}) {
  if (!app || typeof app.loadGraphData !== "function") {
    return { loaded: false, reason: "loader_unavailable" };
  }
  const before = options.initialGraphMarker ?? graphMarker(app);
  let raw = context;
  if (typeof options.loadBootstrap === "function") {
    try {
      raw = await options.loadBootstrap();
    } catch {
      return { loaded: false, reason: "bootstrap_unavailable" };
    }
  }
  const record = bootstrapRecord(raw);
  if (record === null) {
    return { loaded: false, reason: "bootstrap_unavailable" };
  }
  const after = graphMarker(app);
  if (before === null || after === null || before !== after) {
    return { loaded: false, reason: "canvas_changed" };
  }
  const state = canvasStates.get(app) ?? { activeRevision: 0 };
  const activeRevision = Math.max(
    state.activeRevision,
    record.active_revision,
  );
  if (record.bootstrap_revision <= activeRevision) {
    return { loaded: false, reason: "already_loaded" };
  }
  if (
    record.remote_edit_revision > activeRevision
    || typeof options.acknowledge !== "function"
  ) {
    return {
      loaded: false,
      reason: record.remote_edit_revision > activeRevision
        ? "newer_remote_edit"
        : "acknowledgement_unavailable",
    };
  }

  await app.loadGraphData(record.workflow);
  await options.acknowledge(record.bootstrap_revision);
  canvasStates.set(app, { activeRevision: record.bootstrap_revision });
  return {
    loaded: true,
    revision: record.bootstrap_revision,
  };
}


export async function startExtension({
  context,
  api,
  app,
  mountLifecycle,
  cryptoImpl = globalThis.crypto,
  loadBootstrap,
  acknowledgeBootstrap,
  initialGraphMarker,
} = {}) {
  const normalized = normalizeContext(context);
  if (normalized.role === "local") {
    return {
      role: "local",
      launcher: typeof mountLifecycle === "function" ? mountLifecycle() : null,
      restore: null,
      bootstrap: { loaded: false, reason: "local_role" },
    };
  }

  const restore = installNativePromptIdentity(api, cryptoImpl);
  const bootstrap = await bootstrapVastCanvas(app, normalized, {
    loadBootstrap,
    acknowledge: acknowledgeBootstrap,
    initialGraphMarker,
  });
  return {
    role: "vast",
    launcher: null,
    restore,
    bootstrap,
  };
}
