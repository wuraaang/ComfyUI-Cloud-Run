import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

import {
  FRONTEND_VERSION,
  api,
  app,
  assertFrontend14710Contract,
  frontend14710State,
  resetFrontend14710Fixture,
  runRegisteredExtensionSetups,
} from "../fixtures/frontend-1.47.10/extension-api.mjs";
import { FakeDocument, FakeElement } from "./fake-dom.mjs";


const REPOSITORY_ROOT = fileURLToPath(new URL("../..", import.meta.url));
const BASELINE_LOCK_PATH = join(
  REPOSITORY_ROOT,
  "cloud_run",
  "certified_baseline.lock.json",
);
const FIXTURE_MODULE_URL = new URL(
  "../fixtures/frontend-1.47.10/extension-api.mjs",
  import.meta.url,
);
const FRONTEND_FIXTURE_ROOT = join(
  REPOSITORY_ROOT,
  "tests",
  "fixtures",
  "frontend-1.47.10",
);
const AGENT_PANEL_CURATED_ARCHIVE = join(
  FRONTEND_FIXTURE_ROOT,
  "agent-panel-c0e05111db15e8bc040c63ff9457fc142326afab66532f942b631f268fb606be.tar",
);
const EFFICIENCY_FRONTEND_ARCHIVE = Object.freeze({
  file_count: 2,
  path: join(
    FRONTEND_FIXTURE_ROOT,
    "efficiency-frontend-27862272e5ba1bc7b7066dd8475cb3234ba496ba57967358f5a636c16bad1a21.tar",
  ),
  sha256: "27862272e5ba1bc7b7066dd8475cb3234ba496ba57967358f5a636c16bad1a21",
  size_bytes: 40960,
});
const ENTRYPOINTS = Object.freeze({
  "comfyui-agent-panel": "web/js/comfyui-mcp-panel.js",
  "efficiency-nodes-comfyui": "js/previewfix.js",
  "hermes-nous": "web/hermes-nous.js",
});


function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}


function lockedRecord(lock, packageId) {
  const record = [...lock.ui_packages, ...lock.custom_nodes].find(
    (candidate) => candidate.package_id === packageId,
  );
  assert.ok(record, `missing ${packageId} from certified baseline lock`);
  return record;
}


function lockedFiles(record) {
  const files = record.source_archive?.files ?? record.files;
  assert.ok(Array.isArray(files), `${record.package_id} has no locked file list`);
  return files;
}


function lockedFile(record, path) {
  const matches = lockedFiles(record).filter((item) => item.path === path);
  assert.equal(matches.length, 1, `${record.package_id}/${path} is not uniquely locked`);
  return matches[0];
}


function tarText(header, offset, length) {
  const field = header.subarray(offset, offset + length);
  const end = field.indexOf(0);
  return field.subarray(0, end < 0 ? field.length : end).toString("utf8");
}


function tarSize(header, label) {
  const value = tarText(header, 124, 12).trim();
  assert.match(value, /^[0-7]+$/, `invalid ${label} tar size`);
  const size = Number.parseInt(value, 8);
  assert.ok(Number.isSafeInteger(size), `unsafe ${label} tar size`);
  return size;
}


async function extractCuratedArchive({
  archivePath,
  archiveRecord,
  destination,
  directoryName,
  expectedFileCount,
  label,
}) {
  const archive = await readFile(archivePath);
  assert.equal(
    archive.byteLength,
    archiveRecord.size_bytes,
    `${label} archive byte length changed`,
  );
  assert.equal(
    sha256(archive),
    archiveRecord.sha256,
    `${label} archive SHA-256 changed`,
  );

  const source = join(destination, directoryName);
  await mkdir(source, { recursive: true });
  let cursor = 0;
  let copied = 0;
  const seen = new Set();
  while (cursor + 512 <= archive.byteLength) {
    const header = archive.subarray(cursor, cursor + 512);
    if (header.every((value) => value === 0)) break;
    const name = tarText(header, 0, 100);
    const prefix = tarText(header, 345, 155);
    const path = prefix ? `${prefix}/${name}` : name;
    assert.ok(
      path
        && !path.startsWith("/")
        && !path.includes("\\")
        && path.split("/").every((part) => part && part !== "." && part !== ".."),
      `unsafe ${label} tar path`,
    );
    assert.ok(!seen.has(path), `duplicate ${label} tar path`);
    seen.add(path);
    const type = header[156];
    assert.ok(type === 0 || type === 48, `non-file ${label} tar entry`);
    const size = tarSize(header, label);
    const bodyStart = cursor + 512;
    const bodyEnd = bodyStart + size;
    assert.ok(bodyEnd <= archive.byteLength, `truncated ${label} tar`);
    const destinationPath = join(source, path);
    await mkdir(dirname(destinationPath), { recursive: true });
    await writeFile(destinationPath, archive.subarray(bodyStart, bodyEnd));
    copied += 1;
    cursor = bodyStart + Math.ceil(size / 512) * 512;
  }
  assert.equal(copied, expectedFileCount, `${label} tar file count changed`);
  return source;
}


async function extractCuratedAgentPanel(record, destination) {
  return extractCuratedArchive({
    archivePath: AGENT_PANEL_CURATED_ARCHIVE,
    archiveRecord: record.curated_archive,
    destination,
    directoryName: "audited-agent-panel",
    expectedFileCount: 69,
    label: "curated Agent Panel",
  });
}


async function copyLockedTree({ destination, prefix, record, source }) {
  let copied = 0;
  for (const item of lockedFiles(record)) {
    if (!item.path.startsWith(prefix)) continue;
    const relativePath = item.path.slice(prefix.length);
    const sourcePath = join(source, item.path);
    const body = await readFile(sourcePath);
    assert.equal(
      body.byteLength,
      item.size_bytes,
      `${record.package_id}/${item.path} byte length changed`,
    );
    assert.equal(
      sha256(body),
      item.sha256,
      `${record.package_id}/${item.path} SHA-256 changed`,
    );
    const destinationPath = join(destination, relativePath);
    await mkdir(dirname(destinationPath), { recursive: true });
    await writeFile(destinationPath, body);
    copied += 1;
  }
  assert.ok(copied > 0, `${record.package_id} locked tree is empty`);
}


async function readLockedFile({ path, record, source }) {
  const item = lockedFile(record, path);
  const body = await readFile(join(source, path));
  assert.equal(
    body.byteLength,
    item.size_bytes,
    `${record.package_id}/${path} byte length changed`,
  );
  assert.equal(
    sha256(body),
    item.sha256,
    `${record.package_id}/${path} SHA-256 changed`,
  );
  return body;
}


async function copyLockedFile({ destination, path, record, source }) {
  const body = await readLockedFile({ path, record, source });
  await mkdir(dirname(destination), { recursive: true });
  await writeFile(destination, body);
}


async function createBrowserModuleHarness(lock) {
  const root = await mkdtemp(join(tmpdir(), "cloud-run-frontend-14710-"));
  const extensions = join(root, "extensions");
  const scripts = join(root, "scripts");
  await mkdir(scripts, { recursive: true });
  await writeFile(
    join(root, "package.json"),
    `${JSON.stringify({ private: true, type: "module" })}\n`,
  );

  const records = {
    "comfyui-agent-panel": lockedRecord(lock, "comfyui-agent-panel"),
    "efficiency-nodes-comfyui": lockedRecord(
      lock,
      "efficiency-nodes-comfyui",
    ),
    "hermes-nous": lockedRecord(lock, "hermes-nous"),
  };
  const agentPanelSource = await extractCuratedAgentPanel(
    records["comfyui-agent-panel"],
    root,
  );
  const efficiencySource = await extractCuratedArchive({
    archivePath: EFFICIENCY_FRONTEND_ARCHIVE.path,
    archiveRecord: EFFICIENCY_FRONTEND_ARCHIVE,
    destination: root,
    directoryName: "audited-efficiency-frontend",
    expectedFileCount: EFFICIENCY_FRONTEND_ARCHIVE.file_count,
    label: "minimal Efficiency frontend",
  });
  await copyLockedTree({
    destination: join(extensions, "comfyui-agent-panel"),
    prefix: "web/",
    record: records["comfyui-agent-panel"],
    source: agentPanelSource,
  });
  await readLockedFile({
    path: "LICENSE",
    record: records["efficiency-nodes-comfyui"],
    source: efficiencySource,
  });
  await copyLockedFile({
    destination: join(
      extensions,
      "efficiency-nodes-comfyui",
      "previewfix.js",
    ),
    path: "js/previewfix.js",
    record: records["efficiency-nodes-comfyui"],
    source: efficiencySource,
  });
  await copyLockedTree({
    destination: join(extensions, "hermes-nous"),
    prefix: "web/",
    record: records["hermes-nous"],
    source: join(
      REPOSITORY_ROOT,
      "cloud_run",
      "baseline_assets",
      "hermes-nous",
    ),
  });

  const fixtureHref = FIXTURE_MODULE_URL.href;
  await writeFile(
    join(scripts, "app.js"),
    `export { app } from ${JSON.stringify(fixtureHref)};\n`,
  );
  await writeFile(
    join(scripts, "api.js"),
    `export { api } from ${JSON.stringify(fixtureHref)};\n`,
  );

  return {
    async cleanup() {
      await rm(root, { force: true, recursive: true });
    },
    entrypoint(packageId) {
      const path = ENTRYPOINTS[packageId].replace(/^(?:web|js)\//, "");
      return pathToFileURL(join(extensions, packageId, path)).href;
    },
    root,
    scripts,
  };
}


function storage() {
  const values = new Map();
  return {
    clear() {
      values.clear();
    },
    getItem(key) {
      return values.has(String(key)) ? values.get(String(key)) : null;
    },
    removeItem(key) {
      values.delete(String(key));
    },
    setItem(key, value) {
      values.set(String(key), String(value));
    },
  };
}


function decorateElement(element) {
  if (!Object.hasOwn(element, "dataset")) element.dataset = {};
  if (typeof element.removeAttribute !== "function") {
    element.removeAttribute = function removeAttribute(name) {
      this.attributes.delete(name);
    };
  }
  if (!element.classList) {
    element.classList = {
      [Symbol.iterator]: function* iterate() {
        yield* String(element.className).split(/\s+/).filter(Boolean);
      },
      add(...names) {
        const current = new Set(String(element.className).split(/\s+/).filter(Boolean));
        for (const name of names) current.add(name);
        element.className = [...current].join(" ");
      },
      contains(name) {
        return String(element.className).split(/\s+/).includes(name);
      },
      remove(...names) {
        const rejected = new Set(names);
        element.className = String(element.className)
          .split(/\s+/)
          .filter((name) => name && !rejected.has(name))
          .join(" ");
      },
    };
  }
  return element;
}


function installBrowserGlobals(document) {
  const documentListeners = new Map();
  const windowListeners = new Map();
  const originalCreateElement = document.createElement.bind(document);
  document.createElement = (tagName) => decorateElement(originalCreateElement(tagName));
  document.nodeType = 9;
  decorateElement(document.head);
  decorateElement(document.body);
  document.documentElement = decorateElement(originalCreateElement("html"));
  document.createTextNode = (value) => {
    const node = decorateElement(originalCreateElement("#text"));
    node.textContent = value;
    return node;
  };
  document.addEventListener = (type, listener) => {
    const listeners = documentListeners.get(type) ?? [];
    listeners.push(listener);
    documentListeners.set(type, listeners);
  };
  document.removeEventListener = (type, listener) => {
    documentListeners.set(
      type,
      (documentListeners.get(type) ?? []).filter((value) => value !== listener),
    );
  };

  const toolbar = decorateElement(originalCreateElement("div"));
  const originalQuerySelector = document.querySelector.bind(document);
  document.querySelector = (selector) => (
    selector === ".side-tool-bar-container"
      ? toolbar
      : originalQuerySelector(selector)
  );

  const windowRef = {
    addEventListener(type, listener) {
      const listeners = windowListeners.get(type) ?? [];
      listeners.push(listener);
      windowListeners.set(type, listeners);
    },
    app,
    api,
    comfyAPI: { api: { api }, app: { app } },
    crypto: globalThis.crypto,
    document,
    Element: FakeElement,
    localStorage: storage(),
    location: {
      href: "http://127.0.0.1:8188/",
      origin: "http://127.0.0.1:8188",
      reload() {},
      replace() {},
    },
    matchMedia() {
      return {
        addEventListener() {},
        addListener() {},
        matches: false,
        removeEventListener() {},
        removeListener() {},
      };
    },
    open() {},
    removeEventListener(type, listener) {
      windowListeners.set(
        type,
        (windowListeners.get(type) ?? []).filter((value) => value !== listener),
      );
    },
    requestAnimationFrame(callback) {
      callback(0);
      return 1;
    },
    sessionStorage: storage(),
  };
  globalThis.document = document;
  globalThis.window = windowRef;
  globalThis.MutationObserver = class MutationObserver {
    disconnect() {}
    observe() {}
  };
  return windowRef;
}


test("locked Agent, Hermes, and Efficiency entrypoints load on frontend 1.47.10", async () => {
  const lock = JSON.parse(await readFile(BASELINE_LOCK_PATH, "utf8"));
  assert.equal(lock.comfyui_frontend_version, "1.47.10");
  assert.equal(FRONTEND_VERSION, "1.47.10");
  const browser = await createBrowserModuleHarness(lock);
  const document = new FakeDocument();
  installBrowserGlobals(document);
  resetFrontend14710Fixture();

  try {
    for (const packageId of Object.keys(ENTRYPOINTS)) {
      await import(`${browser.entrypoint(packageId)}?baseline=${packageId}`);
    }
    assertFrontend14710Contract({ api, app });
    await runRegisteredExtensionSetups();
    await Promise.resolve();

    const state = frontend14710State();
    assert.deepEqual(
      state.extensionNames,
      [
        "comfyui-mcp.agent-panel",
        "efficiency.previewfix",
        "HermesNous.Theme",
      ],
    );
    assert.deepEqual(state.sidebarTabIds, ["comfyui-mcp.agent"]);
    assert.equal(state.backendAttempts.length, 0);
    assert.deepEqual(await readdir(browser.scripts), ["api.js", "app.js"]);
  } finally {
    await browser.cleanup();
  }
});


test("the compatibility gate fails closed when a required frontend API is absent", () => {
  const brokenApp = { ...app, registerExtension: undefined };
  assert.throws(
    () => assertFrontend14710Contract({ api, app: brokenApp }),
    /app\.registerExtension/,
  );
});


test("the frontend fixture rejects forbidden or credential-bearing backend routes", async () => {
  await assert.rejects(api.fetchApi("/manager/queue"), /forbidden backend route/i);
  await assert.rejects(
    api.fetchApi("https://example.invalid/object_info"),
    /same-origin relative route/i,
  );
  await assert.rejects(
    api.fetchApi("/prompt", {
      headers: { Authorization: "Bearer must-not-cross" },
      method: "POST",
    }),
    /credential forwarding/i,
  );
});
