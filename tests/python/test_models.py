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
        disk_gb=96,
        transfer_bytes=12_000,
        output_allowance_bytes=4_000,
        inet_down_cost=0.01,
        inet_up_cost=0.02,
        duration_seconds=7_200,
        deadline_mode="finite",
        approximate_max_active_charge=0.84,
        template_hash_id="1" * 32,
        worker_commit="a" * 40,
        worker_archive_sha256="b" * 64,
        protocol_version="1",
        manifest_digest="c" * 64,
        machine_id="machine-7",
        host_id="host-3",
        public_ipaddr="203.0.113.7",
        max_instance_creates=1,
        inet_down_mbps=1200.0,
        disk_bw_mbps=600.0,
    )


class LifecycleModelTests(unittest.TestCase):
    def test_boundary_tokens_use_the_shared_private_contract(self):
        from dataclasses import replace

        from cloud_run.models import (
            AttemptState,
            CloudAttempt,
            CloudSession,
            OfferQuote,
            SessionState,
        )

        session = CloudSession.new(
            "session-key",
            session_id="session-1",
            manifest_digest="a" * 64,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
        )
        boundary_session = session.transition(
            SessionState.PREFLIGHT,
            provider_token="a" * 64,
        )
        self.assertEqual(boundary_session.provider_token, "a" * 64)
        self.assertNotIn("provider_token", boundary_session.public_payload())
        for value in ("a" * 63, "A" * 64, b"a" * 64):
            with self.subTest(session_provider_token=value):
                with self.assertRaises(ValueError):
                    session.transition(
                        SessionState.PREFLIGHT,
                        provider_token=value,
                    )

        attempt = CloudAttempt.new(
            "attempt-key",
            quote(OfferQuote),
            attempt_id="attempt-1",
            now=100.0,
        )
        boundary_attempt = attempt.transition(
            AttemptState.CREATING,
            provider_token="a" * 64,
        )
        self.assertEqual(boundary_attempt.provider_token, "a" * 64)
        self.assertNotIn("provider_token", boundary_attempt.public_payload())
        for value in ("a" * 65, "a" * 63 + "!", b"a" * 64):
            with self.subTest(attempt_provider_token=value):
                with self.assertRaises(ValueError):
                    attempt.transition(
                        AttemptState.CREATING,
                        provider_token=value,
                    )
        with self.assertRaises(ValueError):
            replace(
                attempt,
                provider_token="a" * 63,
            ).transition(AttemptState.CREATING)
        self.assertIsNone(
            attempt.transition(AttemptState.CREATING).provider_token
        )

    def test_quote_contains_complete_paid_session_contract_without_secrets(self):
        from cloud_run.models import OfferQuote

        values = quote(OfferQuote).to_record()
        values["dph_total"] = 0.50
        values["approximate_max_active_charge"] = 1.0
        paid_quote = OfferQuote.from_record(values)
        public = paid_quote.public_payload()

        self.assertIn("inet_down_mbps", values)
        self.assertIn("disk_bw_mbps", values)
        self.assertEqual(paid_quote.inet_down_mbps, 1200.0)
        self.assertEqual(paid_quote.disk_bw_mbps, 600.0)
        self.assertEqual(public["inet_down_mbps"], 1200.0)
        self.assertEqual(public["disk_bw_mbps"], 600.0)
        self.assertEqual(paid_quote.max_instance_creates, 1)
        self.assertEqual(paid_quote.to_record()["max_instance_creates"], 1)
        self.assertEqual(public["max_instance_creates"], 1)
        self.assertEqual(public["disk_gb"], 96)
        self.assertEqual(public["transfer_bytes"], 12_000)
        self.assertEqual(public["output_allowance_bytes"], 4_000)
        self.assertEqual(public["duration_seconds"], 7_200)
        self.assertEqual(public["approximate_max_active_charge"], 1.0)
        self.assertEqual(public["template_hash_id"], "1" * 32)
        self.assertEqual(public["worker_commit"], "a" * 40)
        self.assertEqual(public["worker_archive_sha256"], "b" * 64)
        self.assertEqual(public["protocol_version"], "1")
        self.assertEqual(public["manifest_digest"], "c" * 64)
        self.assertNotIn("session_secret_hex", public)

    def test_quote_quality_metrics_are_optional_finite_nonnegative_numbers(self):
        from cloud_run.models import OfferQuote

        values = quote(OfferQuote).to_record()
        values.update(inet_down_mbps=1200, disk_bw_mbps=600.5)
        restored = OfferQuote.from_record(values)

        self.assertEqual(restored.inet_down_mbps, 1200)
        self.assertEqual(restored.disk_bw_mbps, 600.5)
        for field in ("inet_down_mbps", "disk_bw_mbps"):
            for malformed in (True, -1, float("inf"), float("nan"), "500"):
                with self.subTest(field=field, malformed=malformed):
                    with self.assertRaises(ValueError):
                        OfferQuote.from_record({**values, field: malformed})

    def test_quote_without_quality_metrics_is_legacy_inspection_only(self):
        from cloud_run.models import OfferQuote

        values = quote(OfferQuote).to_record()
        values.pop("inet_down_mbps", None)
        values.pop("disk_bw_mbps", None)

        restored = OfferQuote.from_record(values)

        self.assertIsNone(restored.inet_down_mbps)
        self.assertIsNone(restored.disk_bw_mbps)
        self.assertIsNone(restored.public_payload()["inet_down_mbps"])
        self.assertIsNone(restored.public_payload()["disk_bw_mbps"])

    def test_quote_rejects_invalid_total_instance_create_limits(self):
        from cloud_run.models import OfferQuote

        values = quote(OfferQuote).to_record()
        for max_instance_creates in (None, True, 0, 3, 1.0, "1"):
            with self.subTest(max_instance_creates=max_instance_creates):
                with self.assertRaises(ValueError):
                    OfferQuote.from_record(
                        {
                            **values,
                            "max_instance_creates": max_instance_creates,
                        }
                    )

    def test_persisted_quote_without_create_limit_defaults_to_one(self):
        from cloud_run.models import OfferQuote

        values = quote(OfferQuote).to_record()
        del values["max_instance_creates"]

        restored = OfferQuote.from_record(values)

        self.assertEqual(restored.max_instance_creates, 1)

    def test_legacy_quote_records_remain_readable_but_cannot_claim_a_release(self):
        from cloud_run.models import OfferQuote

        legacy = {
            "offer_id": "42",
            "gpu_name": "RTX 4090",
            "gpu_ram_gb": 24.0,
            "dph_total": 0.42,
            "reliability": 0.99,
            "max_price_per_hour": 0.55,
            "expires_at": 160.0,
            "machine_id": "machine-7",
            "host_id": "host-3",
            "public_ipaddr": "203.0.113.7",
        }

        restored = OfferQuote.from_record(legacy)
        public = restored.public_payload()

        self.assertFalse(restored.reviewed_release_bound)
        self.assertEqual(restored.offer_id, "42")
        self.assertEqual(restored.max_instance_creates, 1)
        self.assertIsNone(public["template_hash_id"])
        self.assertIsNone(public["worker_commit"])
        self.assertIsNone(public["manifest_digest"])

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

    def test_session_rejects_disk_outside_the_provider_contract(self):
        from cloud_run.models import CloudSession

        for disk_gb in (79, 2049):
            with self.subTest(disk_gb=disk_gb):
                with self.assertRaises(ValueError):
                    CloudSession.new(
                        "session-key",
                        session_id="session-1",
                        disk_gb=disk_gb,
                        now=100.0,
                    )

    def test_new_session_requires_quote_manifest_disk_and_deadline_to_match(self):
        from cloud_run.models import CloudSession, OfferQuote

        paid_quote = quote(OfferQuote)
        valid = {
            "quote": paid_quote,
            "manifest_digest": "c" * 64,
            "deadline_at": 7_300.0,
            "deadline_mode": "finite",
            "disk_gb": 96,
            "now": 100.0,
        }

        session = CloudSession.new("session-key", **valid)
        self.assertEqual(session.quote, paid_quote)
        for field, value in (
            ("manifest_digest", "d" * 64),
            ("disk_gb", 80),
            ("deadline_at", 7_301.0),
            ("deadline_mode", "none"),
        ):
            with self.subTest(field=field):
                changed = {**valid, field: value}
                with self.assertRaises(ValueError):
                    CloudSession.new("session-key-" + field, **changed)

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
            provider_token="d" * 64,
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
            provider_token="d" * 64,
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
        self.assertNotIn("d" * 64, encoded)
        self.assertNotIn("http://8.8.8.8:30000", encoded)
        self.assertNotIn("c" * 64, encoded)
        self.assertNotIn("manifest_digest", encoded)
        self.assertIn('"billing_may_continue": true', encoded)

    def test_pending_deadline_is_public_but_earlier_finite_limit_stays_effective(self):
        from cloud_run.models import CloudSession, SessionState

        session = CloudSession.new(
            "session-key",
            session_id="session-1",
            manifest_digest="a" * 64,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        )
        pending = session.transition(
            SessionState.READY,
            now=101.0,
            pending_deadline_at=9_100.0,
            pending_deadline_mode="finite",
            pending_deadline_action="add_30_minutes",
        )

        public = pending.public_payload()

        self.assertEqual(pending.deadline_at, 7_300.0)
        self.assertEqual(public["deadline_at"], 7_300.0)
        self.assertTrue(public["deadline_sync_pending"])
        self.assertEqual(public["pending_deadline_at"], 9_100.0)
        with self.assertRaises(ValueError):
            pending.transition(
                SessionState.READY,
                pending_deadline_at=None,
                pending_deadline_mode="finite",
                pending_deadline_action="add_30_minutes",
            )
        with self.assertRaises(ValueError):
            session.transition(
                SessionState.READY,
                pending_deadline_at=7_200.0,
                pending_deadline_mode="finite",
                pending_deadline_action="add_30_minutes",
            )

    def test_destroy_intent_keeps_billing_warning_until_inventory_proves_absence(self):
        from cloud_run.models import CloudSession, SessionState

        session = CloudSession.new(
            "session-key",
            session_id="session-1",
            manifest_digest="a" * 64,
            deadline_at=7_300.0,
            deadline_mode="finite",
            disk_gb=80,
            now=100.0,
            state=SessionState.READY,
        ).transition(
            SessionState.DESTROY_REQUESTED,
            now=101.0,
            instance_id="77",
            destroy_requested=True,
        )

        self.assertTrue(session.public_payload()["billing_may_continue"])

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
            provider_token="d" * 64,
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
        self.assertNotIn("d" * 64, encoded)
        self.assertNotIn("machine-7", encoded)
        self.assertNotIn("203.0.113.7", encoded)


if __name__ == "__main__":
    unittest.main()
