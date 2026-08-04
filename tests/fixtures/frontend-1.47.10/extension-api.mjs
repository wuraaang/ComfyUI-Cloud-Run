export const FRONTEND_VERSION = "1.47.10";


const state = {
  backendAttempts: [],
  canvasDirtyCalls: [],
  extensions: [],
  listeners: new Map(),
  queueCalls: [],
  settings: new Map(),
  sidebarTabs: [],
};


function requiredFunction(value, label) {
  if (typeof value !== "function") {
    throw new TypeError(`frontend 1.47.10 requires ${label}`);
  }
}


function cloneHeaders(headers) {
  if (headers === undefined) return new Headers();
  try {
    return new Headers(headers);
  } catch (error) {
    throw new TypeError("invalid frontend request headers", { cause: error });
  }
}


function checkedRoute(rawRoute, options = {}) {
  if (
    typeof rawRoute !== "string"
    || !rawRoute.startsWith("/")
    || rawRoute.startsWith("//")
    || rawRoute.includes("\\")
    || rawRoute.includes("\0")
  ) {
    throw new TypeError("frontend requests require a same-origin relative route");
  }
  const parsed = new URL(rawRoute, "http://127.0.0.1:8188");
  if (parsed.origin !== "http://127.0.0.1:8188") {
    throw new TypeError("frontend requests require a same-origin relative route");
  }

  const headers = cloneHeaders(options.headers);
  for (const name of [
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
    "x-civitai-api-key",
    "x-vast-api-key",
  ]) {
    if (headers.has(name)) {
      throw new TypeError("credential forwarding is forbidden by the fixture");
    }
  }

  const allowed = [
    /^\/history(?:\/[^/?#]+)?$/,
    /^\/object_info$/,
    /^\/prompt$/,
    /^\/queue$/,
    /^\/system_stats$/,
    /^\/upload\/image$/,
    /^\/userdata\/[^?#]+$/,
    /^\/view$/,
    /^\/cloud-run\/api\/integrations\/agent-panel\/suggestions$/,
  ];
  if (!allowed.some((pattern) => pattern.test(parsed.pathname))) {
    throw new TypeError(`forbidden backend route: ${parsed.pathname}`);
  }
  return { headers, parsed };
}


function responseFor(pathname) {
  const payload = pathname === "/object_info"
    ? { KSampler: { input: { required: {} } } }
    : {};
  const body = JSON.stringify(payload);
  return {
    headers: new Headers({ "Content-Type": "application/json" }),
    ok: true,
    status: 200,
    async arrayBuffer() {
      return new TextEncoder().encode(body).buffer;
    },
    async blob() {
      return new Blob([body], { type: "application/json" });
    },
    async json() {
      return structuredClone(payload);
    },
    async text() {
      return body;
    },
  };
}


const graph = {
  _nodes: [],
  links: {},
  add(node) {
    this._nodes.push(node);
    node.graph = this;
  },
  afterChange() {},
  beforeChange() {},
  getNodeById(id) {
    return this._nodes.find((node) => String(node.id) === String(id)) ?? null;
  },
  remove(node) {
    this._nodes = this._nodes.filter((candidate) => candidate !== node);
  },
  serialize() {
    return { links: [], nodes: [] };
  },
};


const canvas = {
  graph,
  selectNode() {},
  setDirty(...args) {
    state.canvasDirtyCalls.push(args);
  },
  setGraph(nextGraph) {
    this.graph = nextGraph;
  },
};


const settings = {
  getSettingValue(id, fallback) {
    return state.settings.has(id) ? state.settings.get(id) : fallback;
  },
  setSettingValue(id, value) {
    state.settings.set(id, value);
  },
  async setSettingValueAsync(id, value) {
    state.settings.set(id, value);
  },
};


export const app = {
  canvas,
  extensionManager: {
    registerSidebarTab(spec) {
      if (!spec || typeof spec.id !== "string" || typeof spec.render !== "function") {
        throw new TypeError("frontend 1.47.10 requires a valid sidebar tab spec");
      }
      state.sidebarTabs.push(spec);
    },
    workflow: {
      activeWorkflow: null,
      openWorkflows: [],
    },
  },
  frontendVersion: FRONTEND_VERSION,
  graph,
  async queuePrompt(...args) {
    state.queueCalls.push({ owner: "app", args });
    return { node_errors: {}, prompt_id: "fixture-prompt" };
  },
  registerExtension(extension) {
    if (!extension || typeof extension.name !== "string") {
      throw new TypeError("frontend 1.47.10 requires a named extension");
    }
    if (state.extensions.some((candidate) => candidate.name === extension.name)) {
      throw new TypeError(`duplicate extension registration: ${extension.name}`);
    }
    state.extensions.push(extension);
    for (const setting of extension.settings ?? []) {
      if (
        setting
        && typeof setting.id === "string"
        && !state.settings.has(setting.id)
        && Object.hasOwn(setting, "defaultValue")
      ) {
        state.settings.set(setting.id, structuredClone(setting.defaultValue));
      }
    }
  },
  ui: { settings },
};


export const api = {
  addEventListener(type, listener) {
    if (typeof type !== "string" || typeof listener !== "function") {
      throw new TypeError("frontend 1.47.10 requires a valid event listener");
    }
    const listeners = state.listeners.get(type) ?? [];
    listeners.push(listener);
    state.listeners.set(type, listeners);
  },
  apiURL(route) {
    return checkedRoute(route).parsed.pathname;
  },
  clientId: "frontend-14710-fixture",
  async fetchApi(route, options = {}) {
    let checked;
    try {
      checked = checkedRoute(route, options);
    } catch (error) {
      state.backendAttempts.push({ allowed: false, route: String(route) });
      throw error;
    }
    state.backendAttempts.push({
      allowed: true,
      method: options.method ?? "GET",
      route: `${checked.parsed.pathname}${checked.parsed.search}`,
    });
    return responseFor(checked.parsed.pathname);
  },
  async getNodeDefs() {
    return { KSampler: { input: { required: {} } } };
  },
  async queuePrompt(...args) {
    state.queueCalls.push({ owner: "api", args });
    return { node_errors: {}, prompt_id: "fixture-prompt" };
  },
  removeEventListener(type, listener) {
    state.listeners.set(
      type,
      (state.listeners.get(type) ?? []).filter((candidate) => candidate !== listener),
    );
  },
};


export function assertFrontend14710Contract(frontend = { api, app }) {
  requiredFunction(frontend.app?.registerExtension, "app.registerExtension");
  requiredFunction(
    frontend.app?.extensionManager?.registerSidebarTab,
    "app.extensionManager.registerSidebarTab",
  );
  requiredFunction(frontend.app?.queuePrompt, "app.queuePrompt");
  requiredFunction(frontend.api?.addEventListener, "api.addEventListener");
  requiredFunction(frontend.api?.fetchApi, "api.fetchApi");
  requiredFunction(frontend.api?.queuePrompt, "api.queuePrompt");
  requiredFunction(
    frontend.app?.ui?.settings?.getSettingValue,
    "settings.getSettingValue",
  );
  requiredFunction(
    frontend.app?.ui?.settings?.setSettingValue,
    "settings.setSettingValue",
  );
  if (!frontend.app?.graph || !frontend.app?.canvas) {
    throw new TypeError("frontend 1.47.10 requires graph and canvas access");
  }
  return true;
}


export function frontend14710State() {
  return {
    backendAttempts: state.backendAttempts.map((item) => ({ ...item })),
    extensionNames: state.extensions.map((extension) => extension.name),
    listenerTypes: [...state.listeners.keys()].sort(),
    queueCalls: state.queueCalls.map((item) => ({ ...item })),
    settings: Object.fromEntries(state.settings),
    sidebarTabIds: state.sidebarTabs.map((tab) => tab.id),
  };
}


export function resetFrontend14710Fixture() {
  state.backendAttempts.length = 0;
  state.canvasDirtyCalls.length = 0;
  state.extensions.length = 0;
  state.listeners.clear();
  state.queueCalls.length = 0;
  state.settings.clear();
  state.sidebarTabs.length = 0;
  graph._nodes = [];
  graph.links = {};
  canvas.graph = graph;
}


export async function runRegisteredExtensionSetups() {
  for (const extension of [...state.extensions]) {
    if (typeof extension.setup === "function") {
      await extension.setup();
    }
  }
}
