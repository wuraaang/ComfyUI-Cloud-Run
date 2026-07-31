let captureInFlight = false;


function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}


export async function captureOfficialQueuePayload({
  app,
  api,
  number = 0,
  queueNodeIds,
}) {
  if (!app || typeof app.queuePrompt !== "function") {
    throw new Error("Pinned ComfyUI queue API is unavailable.");
  }
  if (!api || typeof api.queuePrompt !== "function") {
    throw new Error("Pinned ComfyUI prompt API is unavailable.");
  }
  if (captureInFlight) {
    throw new Error("A Cloud Run canvas capture is already in progress.");
  }

  captureInFlight = true;
  const original = api.queuePrompt;
  let captured = null;

  async function intercept(queueNumber, data, options = {}) {
    if (api.queuePrompt === intercept) api.queuePrompt = original;
    captured = {
      workflow: cloneJson(data.workflow),
      output: cloneJson(data.output),
      queue_options: {
        ...(queueNumber === -1 ? { front: true } : {}),
        ...(queueNumber !== 0 && queueNumber !== -1
          ? { number: queueNumber }
          : {}),
        ...(options.partialExecutionTargets?.length
          ? {
              partial_execution_targets: cloneJson(
                options.partialExecutionTargets,
              ),
            }
          : {}),
        ...(options.previewMethod && options.previewMethod !== "default"
          ? { preview_method: String(options.previewMethod) }
          : {}),
      },
    };
    return { prompt_id: null, node_errors: {} };
  }

  api.queuePrompt = intercept;
  try {
    await app.queuePrompt(number, 1, queueNodeIds);
    if (!captured) {
      throw new Error("ComfyUI was busy; no Cloud Run payload was captured.");
    }
    return captured;
  } finally {
    if (api.queuePrompt === intercept) api.queuePrompt = original;
    captureInFlight = false;
  }
}
