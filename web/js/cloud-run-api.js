export async function fetchJson(fetchImpl, endpoint, options) {
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


export async function postCapture(fetchImpl, capture) {
  const payload = await fetchJson(fetchImpl, "/cloud-run/api/captures", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      workflow: capture?.workflow,
      output: capture?.output,
      queue_options: capture?.queue_options,
    }),
  });
  if (
    payload.status !== "captured"
    || typeof payload.capture_id !== "string"
    || !payload.capture_id
    || typeof payload.prompt_digest !== "string"
    || !/^[0-9a-f]{64}$/.test(payload.prompt_digest)
  ) {
    throw new Error("request failed");
  }
  return {
    capture_id: payload.capture_id,
    prompt_digest: payload.prompt_digest,
    status: "captured",
  };
}
