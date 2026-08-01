import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  captureOfficialQueuePayload,
} from "../../web/js/canvas-adapter.js";


function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}


function executionDigest(capture) {
  return createHash("sha256")
    .update(JSON.stringify(canonicalize({
      output: capture.output,
      queue_options: capture.queue_options,
    })))
    .digest("hex");
}


test("captures through official queue preparation and never calls local prompt", async () => {
  const trace = [];
  const api = {
    async queuePrompt(number, data, options) {
      trace.push(["local-api", number, data, options]);
      return { prompt_id: "local", node_errors: {} };
    },
  };
  const app = {
    async queuePrompt(number, batchCount, queueNodeIds) {
      assert.equal(batchCount, 1);
      trace.push("beforeQueued");
      trace.push("promoted-beforeQueued");
      trace.push("virtual-applyToGraph");
      await Promise.resolve();
      trace.push("async-serializeValue");
      const data = {
        workflow: { version: 1, nodes: [{ id: 1 }] },
        output: {
          "1": {
            class_type: "KSampler",
            inputs: { seed: 44 },
          },
        },
      };
      const result = await api.queuePrompt(number, data, {
        partialExecutionTargets: queueNodeIds,
        previewMethod: "latent2rgb",
      });
      trace.push("afterQueued");
      trace.push("promoted-afterQueued");
      assert.equal(result.prompt_id, null);
      return true;
    },
  };

  const captured = await captureOfficialQueuePayload({
    app,
    api,
    number: -1,
    queueNodeIds: ["1"],
  });

  assert.deepEqual(trace, [
    "beforeQueued",
    "promoted-beforeQueued",
    "virtual-applyToGraph",
    "async-serializeValue",
    "afterQueued",
    "promoted-afterQueued",
  ]);
  assert.equal(captured.output["1"].inputs.seed, 44);
  assert.deepEqual(captured.queue_options, {
    front: true,
    partial_execution_targets: ["1"],
    preview_method: "latent2rgb",
  });
});


test("restores local Run before a queued local submission", async () => {
  const local = [];
  const api = {
    async queuePrompt(number, data) {
      local.push([number, data.output]);
      return { prompt_id: "real-local", node_errors: {} };
    },
  };
  const original = api.queuePrompt;
  const app = {
    async queuePrompt() {
      await api.queuePrompt(
        0,
        {
          workflow: { nodes: [] },
          output: { cloud: { class_type: "Cloud", inputs: {} } },
        },
        {},
      );
      await api.queuePrompt(
        0,
        {
          workflow: { nodes: [] },
          output: { local: { class_type: "Local", inputs: {} } },
        },
        {},
      );
      return true;
    },
  };

  await captureOfficialQueuePayload({ app, api });

  assert.strictEqual(api.queuePrompt, original);
  assert.deepEqual(local, [
    [0, { local: { class_type: "Local", inputs: {} } }],
  ]);
});


test("restores the official API after errors and rejects overlapping capture", async () => {
  let release;
  const blocked = new Promise((resolve) => {
    release = resolve;
  });
  const api = {
    async queuePrompt() {
      return { prompt_id: "local", node_errors: {} };
    },
  };
  const original = api.queuePrompt;
  const app = {
    async queuePrompt() {
      await blocked;
      throw new Error("synthetic queue failure");
    },
  };

  const first = captureOfficialQueuePayload({ app, api });
  await Promise.resolve();
  await assert.rejects(
    captureOfficialQueuePayload({ app, api }),
    /already in progress/,
  );
  release();
  await assert.rejects(first, /synthetic queue failure/);
  assert.strictEqual(api.queuePrompt, original);
});


test("captures queue number and clones mutable official payloads", async () => {
  const data = {
    workflow: { version: 1, nodes: [{ id: 1 }] },
    output: { "1": { class_type: "Node", inputs: { value: 3 } } },
  };
  const api = {
    async queuePrompt() {
      return { prompt_id: "local", node_errors: {} };
    },
  };
  const app = {
    async queuePrompt(number) {
      await api.queuePrompt(number, data, { previewMethod: "default" });
      data.output["1"].inputs.value = 99;
    },
  };

  const captured = await captureOfficialQueuePayload({
    app,
    api,
    number: 7,
  });

  assert.equal(captured.output["1"].inputs.value, 3);
  assert.deepEqual(captured.queue_options, { number: 7 });
});


test("preserves native model metadata at every workflow scope", async () => {
  const fixture = JSON.parse(
    await readFile(
      new URL(
        "../fixtures/native-model-metadata-workflow.json",
        import.meta.url,
      ),
      "utf8",
    ),
  );
  const nestedRecord = {
    name: "nested.safetensors",
    url: (
      "https://huggingface.co/example/nested/resolve/main/"
      + "models/nested.safetensors"
    ),
    directory: "vae",
    hash: "c".repeat(64),
    hash_type: "sha256",
  };
  const workflow = structuredClone(fixture);
  workflow.models = [{ ...nestedRecord }];
  workflow.definitions = {
    subgraphs: [
      {
        id: "inner",
        nodes: [
          {
            id: 4,
            type: "VAELoader",
            mode: 0,
            widgets_values: ["nested.safetensors"],
            properties: { models: [{ ...nestedRecord }] },
          },
        ],
      },
    ],
  };
  const expectedWorkflow = structuredClone(workflow);
  const output = {
    "1": {
      class_type: "UNETLoader",
      inputs: { unet_name: "cloud-run-native-proof.safetensors" },
    },
  };
  const api = {
    async queuePrompt() {
      throw new Error("local prompt execution is forbidden");
    },
  };
  const app = {
    async queuePrompt() {
      await api.queuePrompt(0, { workflow, output }, {});
      workflow.nodes[0].properties.models.length = 0;
      workflow.models.length = 0;
      workflow.definitions.subgraphs.length = 0;
      return true;
    },
  };

  const captured = await captureOfficialQueuePayload({ app, api });

  assert.deepEqual(Object.keys(captured).sort(), [
    "output",
    "queue_options",
    "workflow",
  ]);
  assert.deepEqual(captured.workflow, expectedWorkflow);
  assert.deepEqual(
    captured.workflow.nodes[0].properties.models,
    fixture.nodes[0].properties.models,
  );
  assert.deepEqual(
    captured.workflow.definitions.subgraphs[0].nodes[0].properties.models,
    [nestedRecord],
  );
  assert.deepEqual(captured.output, output);
  assert.deepEqual(captured.queue_options, {});
  assert.equal(
    executionDigest(captured),
    executionDigest({
      ...captured,
      workflow: { nodes: [], models: [] },
    }),
  );
});
