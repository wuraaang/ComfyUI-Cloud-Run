import asyncio
import hashlib
import json
import unittest

from cloud_run.worker_protocol import verify_request


class RecordingTransport:
    def __init__(self):
        self.requests = []
        self.responses = [
            {
                "protocol_version": "1",
                "session_id": "session-1",
                "claimed": True,
            },
            {
                "protocol_version": "1",
                "claimed": True,
            },
        ]

    async def request(self, request, *, max_bytes):
        from cloud_run.worker_client import WorkerTransportResponse

        self.requests.append(request)
        payload = self.responses.pop(0)
        return WorkerTransportResponse(
            status=200,
            headers={"Content-Type": "application/json"},
            body=json.dumps(
                payload,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
        )


class WorkerClientTests(unittest.TestCase):
    def test_client_claims_through_vast_bearer_then_uses_hmac(self):
        from cloud_run.worker_client import WorkerClient

        transport = RecordingTransport()
        client = WorkerClient(
            base_url="http://8.8.8.8:30000",
            provider_token="vast-boundary-token",
            session_id="session-1",
            session_secret=b"s" * 32,
            transport=transport,
            clock=lambda: 1000,
            nonce=lambda: "n-1",
        )

        claim = asyncio.run(client.claim())
        health = asyncio.run(client.health())

        self.assertTrue(claim["claimed"])
        self.assertTrue(health["claimed"])
        self.assertEqual(
            transport.requests[0].headers["Authorization"],
            "Bearer vast-boundary-token",
        )
        self.assertNotIn(
            "X-Cloud-Run-Signature",
            transport.requests[0].headers,
        )
        self.assertEqual(
            transport.requests[1].headers["Authorization"],
            "Bearer vast-boundary-token",
        )
        self.assertIn(
            "X-Cloud-Run-Signature",
            transport.requests[1].headers,
        )
        verify_request(
            b"s" * 32,
            "GET",
            "/worker/v1/health",
            b"",
            {
                "protocol_version": transport.requests[1].headers[
                    "X-Cloud-Run-Protocol-Version"
                ],
                "timestamp": int(
                    transport.requests[1].headers[
                        "X-Cloud-Run-Timestamp"
                    ]
                ),
                "nonce": transport.requests[1].headers[
                    "X-Cloud-Run-Nonce"
                ],
                "signature": transport.requests[1].headers[
                    "X-Cloud-Run-Signature"
                ],
            },
            now=1000,
            seen_nonces=set(),
        )
        self.assertEqual(
            client.public_payload(),
            {"session_id": "session-1", "protocol_version": "1"},
        )
        exposed = repr(
            [
                client.public_payload(),
                transport.requests,
            ]
        )
        self.assertNotIn("vast-boundary-token", exposed)
        self.assertNotIn((b"s" * 32).hex(), exposed)
        self.assertNotIn("8.8.8.8", repr(client.public_payload()))

    def test_client_rejects_private_hostname_path_and_credential_urls(self):
        from cloud_run.worker_client import WorkerClientError, WorkerClient

        for url in (
            "http://127.0.0.1:8765",
            "http://10.0.0.1:8765",
            "http://169.254.169.254:80",
            "https://evil.example/worker",
            "http://user:pass@8.8.8.8:30000",
            "http://8.8.8.8:30000/path",
            "http://8.8.8.8",
        ):
            with self.subTest(url=url):
                with self.assertRaises(WorkerClientError):
                    WorkerClient(
                        base_url=url,
                        provider_token="provider-token",
                        session_id="session-1",
                        session_secret=b"s" * 32,
                        transport=RecordingTransport(),
                    )

    def test_redirect_or_oversized_json_is_sanitized_without_retry(self):
        from cloud_run.worker_client import (
            WorkerClient,
            WorkerClientError,
            WorkerTransportResponse,
        )

        class RefusingTransport:
            def __init__(self, response):
                self.response = response
                self.requests = []

            async def request(self, request, *, max_bytes):
                self.requests.append(request)
                return self.response

        for response in (
            WorkerTransportResponse(
                status=302,
                headers={"Location": "http://127.0.0.1/private"},
                body=b"",
            ),
            WorkerTransportResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b"{" + b"x" * (1024 * 1024) + b"}",
            ),
        ):
            transport = RefusingTransport(response)
            client = WorkerClient(
                base_url="http://8.8.8.8:30000",
                provider_token="provider-token",
                session_id="session-1",
                session_secret=b"s" * 32,
                transport=transport,
                clock=lambda: 1000,
                nonce=lambda: "n-1",
            )
            with self.assertRaises(WorkerClientError) as raised:
                asyncio.run(client.health())
            self.assertEqual(len(transport.requests), 1)
            self.assertNotIn("127.0.0.1", str(raised.exception))
            self.assertNotIn("provider-token", str(raised.exception))

    def test_download_binds_resume_offset_into_the_signed_request(self):
        from cloud_run.worker_client import (
            WorkerClient,
            WorkerTransportResponse,
        )

        content = b"wallpaper-output"

        class StreamingTransport:
            def __init__(self):
                self.request_seen = None

            async def request(self, request, *, max_bytes):
                return WorkerTransportResponse(
                    status=500,
                    headers={},
                    body=b"",
                )

            async def stream(
                self,
                request,
                *,
                on_headers,
                on_chunk,
                max_bytes,
            ):
                self.request_seen = request
                remaining = content[5:]
                on_headers(
                    206,
                    {
                        "Content-Range": "bytes 5-15/16",
                        "Content-Length": str(len(remaining)),
                        "Content-Type": "image/png",
                        "ETag": '"' + hashlib.sha256(content).hexdigest() + '"',
                    },
                )
                for chunk in (remaining[:3], remaining[3:]):
                    result = on_chunk(chunk)
                    if asyncio.iscoroutine(result):
                        await result
                return len(remaining)

        transport = StreamingTransport()
        client = WorkerClient(
            base_url="http://8.8.8.8:30000",
            provider_token="provider-token",
            session_id="session-1",
            session_secret=b"s" * 32,
            transport=transport,
            clock=lambda: 1000,
            nonce=lambda: "n-1",
        )
        chunks = []

        metadata = asyncio.run(
            client.download_artifact(
                "artifact-1",
                start=5,
                on_chunk=lambda chunk: chunks.append(chunk),
            )
        )

        request = transport.request_seen
        self.assertEqual(
            request.url,
            "http://8.8.8.8:30000/worker/v1/artifacts/artifact-1?start=5",
        )
        self.assertEqual(request.headers["Range"], "bytes=5-")
        verify_request(
            b"s" * 32,
            "GET",
            "/worker/v1/artifacts/artifact-1?start=5",
            b"",
            {
                "protocol_version": request.headers[
                    "X-Cloud-Run-Protocol-Version"
                ],
                "timestamp": int(
                    request.headers["X-Cloud-Run-Timestamp"]
                ),
                "nonce": request.headers["X-Cloud-Run-Nonce"],
                "signature": request.headers["X-Cloud-Run-Signature"],
            },
            now=1000,
            seen_nonces=set(),
        )
        self.assertEqual(b"".join(chunks), content[5:])
        self.assertEqual(metadata.total_size, len(content))
        self.assertEqual(metadata.sha256, hashlib.sha256(content).hexdigest())

    def test_internal_header_overrides_cannot_replace_authentication(self):
        from cloud_run.worker_client import WorkerClient, WorkerClientError

        client = WorkerClient(
            base_url="http://8.8.8.8:30000",
            provider_token="provider-token",
            session_id="session-1",
            session_secret=b"s" * 32,
            transport=RecordingTransport(),
        )

        with self.assertRaises(WorkerClientError):
            client._request(
                "GET",
                "/worker/v1/health",
                extra={"Authorization": "Bearer attacker"},
            )


if __name__ == "__main__":
    unittest.main()
