import json
import unittest

from cloud_run import models


def model_api(test_case):
    required = (
        "AttemptState",
        "CloudAttempt",
        "InvalidStateTransition",
        "OfferQuote",
    )
    for name in required:
        test_case.assertTrue(hasattr(models, name), name + " is required")
    return tuple(getattr(models, name) for name in required)


def quote(OfferQuote):
    return OfferQuote(
        offer_id="42",
        gpu_name="RTX 4090",
        gpu_ram_gb=24.0,
        dph_total=0.42,
        reliability=0.99,
        max_price_per_hour=0.55,
        expires_at=160.0,
        machine_id="machine-7",
        host_id="host-3",
        public_ipaddr="203.0.113.7",
    )


class LifecycleModelTests(unittest.TestCase):
    def test_session_and_job_state_contracts_are_exact(self):
        from cloud_run.models import JobState, SessionState, TransferState

        self.assertEqual(
            {state.value for state in SessionState},
            {
                "preflight",
                "offer_selected",
                "confirming",
                "creating",
                "bootstrapping",
                "provisioning",
                "validating",
                "ready",
                "running",
                "harvesting",
                "repairing",
                "destroy_requested",
                "destroying",
                "destroyed",
                "failed",
            },
        )
        self.assertEqual(
            {state.value for state in JobState},
            {
                "captured",
                "resolving",
                "queued",
                "running",
                "harvesting",
                "succeeded",
                "failed",
            },
        )
        self.assertEqual(
            {state.value for state in TransferState},
            {
                "pending",
                "transferring",
                "verified",
                "failed",
                "abandoned",
            },
        )

    def test_execution_failure_returns_a_healthy_session_to_ready(self):
        from cloud_run.models import CloudSession, SessionState

        session = CloudSession(
            session_id="session-1",
            idempotency_key="session-key",
            label="comfy-cloud-run-session-1",
            state=SessionState.RUNNING,
            quote=None,
            manifest_digest="a" * 64,
            installed_manifest_digest="a" * 64,
            instance_id="77",
            worker_base_url="http://8.8.8.8:30000",
            provider_token="private-provider-token",
            session_secret_hex="b" * 64,
            deadline_at=7200.0,
            deadline_mode="finite",
            disk_gb=80,
            retry_count=0,
            destroy_requested=False,
            residual_inventory=(),
            sanitized_error=None,
            created_at=10.0,
            updated_at=10.0,
            version=1,
        )

        saved = session.transition(SessionState.READY, now=20.0)

        self.assertEqual(saved.state, SessionState.READY)
        with self.assertRaises(models.InvalidStateTransition):
            saved.transition(SessionState.CREATING)

    def test_session_public_payload_omits_private_connection_material(self):
        from cloud_run.models import CloudSession, SessionState

        session = CloudSession(
            session_id="session-1",
            idempotency_key="private-idempotency-key",
            label="comfy-cloud-run-session-1",
            state=SessionState.FAILED,
            quote=None,
            manifest_digest="a" * 64,
            installed_manifest_digest="b" * 64,
            instance_id="77",
            worker_base_url="http://8.8.8.8:30000",
            provider_token="private-provider-token",
            session_secret_hex="c" * 64,
            deadline_at=7200.0,
            deadline_mode="finite",
            disk_gb=80,
            retry_count=0,
            destroy_requested=False,
            residual_inventory=("77",),
            sanitized_error="Sanitized failure.",
            created_at=10.0,
            updated_at=20.0,
            version=1,
        )

        encoded = json.dumps(session.public_payload(), sort_keys=True)

        self.assertNotIn("private-idempotency-key", encoded)
        self.assertNotIn("private-provider-token", encoded)
        self.assertNotIn("http://8.8.8.8:30000", encoded)
        self.assertNotIn("c" * 64, encoded)
        self.assertNotIn("manifest_digest", encoded)
        self.assertIn('"billing_may_continue": true', encoded)

    def test_new_attempt_has_a_durable_identity_and_confirming_state(self):
        AttemptState, CloudAttempt, _, OfferQuote = model_api(self)

        attempt = CloudAttempt.new(
            idempotency_key="idem-1",
            quote=quote(OfferQuote),
            attempt_id="attempt-1",
            now=100.0,
        )

        self.assertEqual(attempt.attempt_id, "attempt-1")
        self.assertEqual(attempt.label, "comfy-cloud-run-attempt-1")
        self.assertEqual(attempt.state, AttemptState.CONFIRMING)
        self.assertEqual(attempt.version, 1)
        self.assertEqual(attempt.created_at, 100.0)
        self.assertEqual(attempt.updated_at, 100.0)

    def test_happy_cancel_and_retry_transitions_are_explicit(self):
        AttemptState, CloudAttempt, _, OfferQuote = model_api(self)
        attempt = CloudAttempt.new(
            idempotency_key="idem-1",
            quote=quote(OfferQuote),
            attempt_id="attempt-1",
            now=100.0,
        )

        for state in (
            AttemptState.CREATING,
            AttemptState.STARTING,
            AttemptState.READY,
            AttemptState.DESTROYING,
            AttemptState.CANCELLED,
        ):
            attempt = attempt.transition(state, now=attempt.updated_at + 1)
        self.assertEqual(attempt.state, AttemptState.CANCELLED)

        cancelling = CloudAttempt.new(
            idempotency_key="idem-2",
            quote=quote(OfferQuote),
            attempt_id="attempt-2",
            now=200.0,
        ).transition(AttemptState.CREATING, now=201.0)
        cancelling = cancelling.transition(
            AttemptState.CANCEL_REQUESTED,
            now=202.0,
            cancel_requested=True,
        )
        cancelling = cancelling.transition(AttemptState.DESTROYING, now=203.0)
        cancelling = cancelling.transition(AttemptState.CANCELLED, now=204.0)
        self.assertTrue(cancelling.cancel_requested)

        retrying = CloudAttempt.new(
            idempotency_key="idem-3",
            quote=quote(OfferQuote),
            attempt_id="attempt-3",
            now=300.0,
        ).transition(AttemptState.CREATING, now=301.0)
        retrying = retrying.transition(AttemptState.STARTING, now=302.0)
        retrying = retrying.transition(
            AttemptState.FAILED,
            now=303.0,
            sanitized_error="pod did not become ready",
        )
        retrying = retrying.transition(
            AttemptState.RETRYING,
            now=304.0,
            retry_count=1,
            instance_id=None,
        )
        self.assertEqual(retrying.retry_count, 1)

    def test_invalid_or_terminal_transition_is_rejected(self):
        AttemptState, CloudAttempt, InvalidStateTransition, OfferQuote = model_api(self)
        attempt = CloudAttempt.new(
            idempotency_key="idem-1",
            quote=quote(OfferQuote),
            attempt_id="attempt-1",
            now=100.0,
        )

        with self.assertRaises(InvalidStateTransition):
            attempt.transition(AttemptState.READY, now=101.0)

        cancelled = attempt.transition(AttemptState.CANCELLED, now=101.0)
        with self.assertRaises(InvalidStateTransition):
            cancelled.transition(AttemptState.CREATING, now=102.0)

    def test_public_payload_exposes_cost_and_residual_instance_but_no_secrets(self):
        AttemptState, CloudAttempt, _, OfferQuote = model_api(self)
        attempt = CloudAttempt.new(
            idempotency_key="browser-secret-idempotency-key",
            quote=quote(OfferQuote),
            attempt_id="attempt-1",
            now=100.0,
        ).transition(
            AttemptState.FAILED,
            now=101.0,
            instance_id="instance-9",
            provider_token="provider-secret-token",
            sanitized_error="Sanitized failure.",
        )

        payload = attempt.public_payload()
        encoded = json.dumps(payload, sort_keys=True)

        self.assertEqual(payload["attempt_id"], "attempt-1")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["instance_id"], "instance-9")
        self.assertEqual(payload["offer"]["offer_id"], "42")
        self.assertEqual(payload["offer"]["max_price_per_hour"], 0.55)
        self.assertNotIn("idempotency", encoded)
        self.assertNotIn("provider-secret-token", encoded)
        self.assertNotIn("machine-7", encoded)
        self.assertNotIn("203.0.113.7", encoded)


if __name__ == "__main__":
    unittest.main()
