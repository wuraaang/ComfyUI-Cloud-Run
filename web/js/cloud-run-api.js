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


export const SETTINGS_ENDPOINT = "/cloud-run/api/settings";
export const CAPTURES_ENDPOINT = "/cloud-run/api/captures";
export const PREFLIGHTS_ENDPOINT = "/cloud-run/api/preflights";
export const OFFERS_ENDPOINT = "/cloud-run/api/offers";
export const SESSIONS_ENDPOINT = "/cloud-run/api/sessions";


function identifier(value) {
  if (
    typeof value !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(value)
  ) {
    throw new Error("request failed");
  }
  return encodeURIComponent(value);
}


function jsonOptions(method, payload) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}


function sessionEndpoint(sessionId) {
  return `${SESSIONS_ENDPOINT}/${identifier(sessionId)}`;
}


function jobEndpoint(sessionId, jobId) {
  return `${sessionEndpoint(sessionId)}/jobs/${identifier(jobId)}`;
}


export function createCloudRunApi(fetchImpl) {
  if (typeof fetchImpl !== "function") throw new Error("request failed");
  return {
    getSettings() {
      return fetchJson(fetchImpl, SETTINGS_ENDPOINT);
    },

    updateSettings(payload) {
      return fetchJson(
        fetchImpl,
        SETTINGS_ENDPOINT,
        jsonOptions("PUT", payload),
      );
    },

    capture(capture) {
      return postCapture(fetchImpl, capture);
    },

    preflight(captureId, explicitOutputAllowanceBytes) {
      const payload = { capture_id: String(captureId) };
      if (explicitOutputAllowanceBytes !== null) {
        payload.explicit_output_allowance_bytes =
          explicitOutputAllowanceBytes;
      }
      return fetchJson(
        fetchImpl,
        PREFLIGHTS_ENDPOINT,
        jsonOptions("POST", payload),
      );
    },

    searchOffers(preflightId) {
      return fetchJson(
        fetchImpl,
        OFFERS_ENDPOINT,
        jsonOptions("POST", {
          preflight_id: String(preflightId),
        }),
      );
    },

    approveMapping(mappingId, candidateDigest) {
      return fetchJson(
        fetchImpl,
        `/cloud-run/api/mappings/${identifier(mappingId)}`,
        jsonOptions("PUT", {
          candidate_digest: String(candidateDigest),
        }),
      );
    },

    createSession(payload) {
      return fetchJson(
        fetchImpl,
        SESSIONS_ENDPOINT,
        jsonOptions("POST", payload),
      );
    },

    confirmSession(sessionId, idempotencyKey) {
      return fetchJson(
        fetchImpl,
        `${sessionEndpoint(sessionId)}/confirm`,
        jsonOptions("POST", {
          idempotency_key: String(idempotencyKey),
        }),
      );
    },

    getSession(sessionId) {
      return fetchJson(fetchImpl, sessionEndpoint(sessionId));
    },

    createJob(sessionId, captureId, idempotencyKey) {
      return fetchJson(
        fetchImpl,
        `${sessionEndpoint(sessionId)}/jobs`,
        jsonOptions("POST", {
          capture_id: String(captureId),
          idempotency_key: String(idempotencyKey),
        }),
      );
    },

    getJob(sessionId, jobId) {
      return fetchJson(fetchImpl, jobEndpoint(sessionId, jobId));
    },

    getEvents(sessionId, jobId, afterSequence = 0) {
      if (
        !Number.isSafeInteger(afterSequence)
        || afterSequence < 0
      ) {
        throw new Error("request failed");
      }
      return fetchJson(
        fetchImpl,
        `${jobEndpoint(sessionId, jobId)}/events` +
          `?after_sequence=${afterSequence}`,
      );
    },

    updateDeadline(sessionId, payload) {
      return fetchJson(
        fetchImpl,
        `${sessionEndpoint(sessionId)}/deadline`,
        jsonOptions("PUT", payload),
      );
    },

    reviewDestroy(sessionId) {
      return fetchJson(
        fetchImpl,
        `${sessionEndpoint(sessionId)}/destroy-review`,
        jsonOptions("POST", {}),
      );
    },

    destroySession(sessionId, confirmation) {
      return fetchJson(
        fetchImpl,
        sessionEndpoint(sessionId),
        jsonOptions("DELETE", confirmation),
      );
    },

    previewUrl(sessionId, jobId, previewId) {
      return `${jobEndpoint(sessionId, jobId)}/previews/` +
        identifier(previewId);
    },

    artifactUrl(sessionId, jobId, artifactId) {
      return `${jobEndpoint(sessionId, jobId)}/artifacts/` +
        identifier(artifactId);
    },
  };
}
