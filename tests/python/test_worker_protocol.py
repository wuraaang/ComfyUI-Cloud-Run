import hashlib
import hmac
import unittest


class WorkerProtocolTests(unittest.TestCase):
    def test_controller_boundary_values_have_strict_shapes(self):
        from cloud_run.worker_protocol import (
            BOUNDARY_TOKEN_ENVIRONMENT,
            SESSION_ID_ENVIRONMENT,
            is_boundary_token,
            is_worker_session_id,
        )

        self.assertEqual(
            BOUNDARY_TOKEN_ENVIRONMENT,
            "CLOUD_RUN_BOUNDARY_TOKEN",
        )
        self.assertEqual(SESSION_ID_ENVIRONMENT, "CLOUD_RUN_SESSION_ID")
        self.assertTrue(is_boundary_token("a" * 64))
        for value in (
            "a" * 63,
            "a" * 65,
            "A" * 64,
            " " + "a" * 63,
            "a" * 63 + "!",
            b"a" * 64,
            None,
        ):
            with self.subTest(boundary_token=value):
                self.assertFalse(is_boundary_token(value))

        for value in (
            "session-1",
            "A" + "a" * 199,
            "session.id:1",
        ):
            with self.subTest(worker_session_id=value):
                self.assertTrue(is_worker_session_id(value))
        for value in ("", ".session", "a" * 201, "session/1", b"s"):
            with self.subTest(worker_session_id=value):
                self.assertFalse(is_worker_session_id(value))

    def test_signed_request_binds_method_path_body_time_and_nonce(self):
        from cloud_run.worker_protocol import (
            ProtocolAuthenticationError,
            sign_request,
            signing_material,
            verify_request,
        )

        secret = b"s" * 32
        body = b'{"manifest":"abc"}'
        envelope = sign_request(
            secret,
            "POST",
            "/worker/v1/manifests",
            body,
            timestamp=1000,
            nonce="n-1",
        )

        self.assertEqual(
            envelope,
            {
                "protocol_version": "2",
                "timestamp": 1000,
                "nonce": "n-1",
                "signature": hmac.new(
                    secret,
                    signing_material(
                        "POST",
                        "/worker/v1/manifests",
                        body,
                        1000,
                        "n-1",
                    ),
                    hashlib.sha256,
                ).hexdigest(),
            },
        )
        verify_request(
            secret,
            "POST",
            "/worker/v1/manifests",
            body,
            envelope,
            now=1002,
            seen_nonces=set(),
        )
        for changed in (
            ("GET", "/worker/v1/manifests", body),
            ("POST", "/worker/v1/jobs", body),
            ("POST", "/worker/v1/manifests", b"{}"),
        ):
            with self.subTest(changed=changed[:2]):
                with self.assertRaises(ProtocolAuthenticationError):
                    verify_request(
                        secret,
                        *changed,
                        envelope,
                        now=1002,
                        seen_nonces=set(),
                    )

    def test_reused_nonce_and_old_or_future_timestamp_are_rejected(self):
        from cloud_run.worker_protocol import (
            ProtocolAuthenticationError,
            sign_request,
            verify_request,
        )

        secret = b"s" * 32
        body = b"{}"
        envelope = sign_request(
            secret,
            "POST",
            "/worker/v1/manifests",
            body,
            timestamp=1000,
            nonce="n-1",
        )
        for now, seen in (
            (1001, {"n-1"}),
            (1031, set()),
            (969, set()),
        ):
            with self.subTest(now=now, seen=bool(seen)):
                with self.assertRaises(ProtocolAuthenticationError):
                    verify_request(
                        secret,
                        "POST",
                        "/worker/v1/manifests",
                        body,
                        envelope,
                        now=now,
                        seen_nonces=seen,
                    )

    def test_nonce_cache_is_bounded_and_purges_only_expired_entries(self):
        from cloud_run.worker_protocol import (
            NonceCache,
            ProtocolAuthenticationError,
            sign_request,
            verify_request,
        )

        secret = b"s" * 32
        cache = NonceCache(max_entries=2)

        for timestamp, nonce in ((1000, "n-1"), (1001, "n-2")):
            verify_request(
                secret,
                "GET",
                "/worker/v1/jobs/job-1",
                b"",
                sign_request(
                    secret,
                    "GET",
                    "/worker/v1/jobs/job-1",
                    b"",
                    timestamp=timestamp,
                    nonce=nonce,
                ),
                now=1002,
                seen_nonces=cache,
            )
        with self.assertRaises(ProtocolAuthenticationError):
            verify_request(
                secret,
                "GET",
                "/worker/v1/jobs/job-1",
                b"",
                sign_request(
                    secret,
                    "GET",
                    "/worker/v1/jobs/job-1",
                    b"",
                    timestamp=1002,
                    nonce="n-3",
                ),
                now=1002,
                seen_nonces=cache,
            )

        verify_request(
            secret,
            "GET",
            "/worker/v1/jobs/job-1",
            b"",
            sign_request(
                secret,
                "GET",
                "/worker/v1/jobs/job-1",
                b"",
                timestamp=1040,
                nonce="n-3",
            ),
            now=1040,
            seen_nonces=cache,
        )
        self.assertEqual(len(cache), 1)

    def test_malformed_authentication_never_echoes_secret_or_body(self):
        from cloud_run.worker_protocol import (
            ProtocolAuthenticationError,
            sign_request,
            verify_request,
        )

        secret = b"sensitive-secret-material-123456"
        body = b'{"private":"body-marker"}'
        valid = sign_request(
            secret,
            "POST",
            "/worker/v1/manifests",
            body,
            timestamp=1000,
            nonce="n-1",
        )
        invalid_envelopes = (
            {**valid, "signature": "0" * 64},
            {**valid, "protocol_version": "1"},
            {**valid, "timestamp": True},
            {**valid, "nonce": "bad\nnonce"},
            {**valid, "extra": "value"},
            None,
        )

        for envelope in invalid_envelopes:
            with self.subTest(envelope=type(envelope).__name__):
                with self.assertRaises(
                    ProtocolAuthenticationError
                ) as raised:
                    verify_request(
                        secret,
                        "POST",
                        "/worker/v1/manifests",
                        body,
                        envelope,
                        now=1000,
                        seen_nonces=set(),
                    )
                message = str(raised.exception)
                self.assertNotIn("sensitive", message)
                self.assertNotIn("body-marker", message)


if __name__ == "__main__":
    unittest.main()
