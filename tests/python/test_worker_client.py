import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
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
    def test_boundary_401_is_typed_and_never_echoes_response(self):
        from cloud_run.worker_client import (
            WorkerBoundaryAuthenticationError,
            WorkerClient,
            WorkerTransportResponse,
        )

        private_marker = "private-boundary-response-marker"

        class RejectingTransport:
            requests = []

            async def request(self, request, *, max_bytes):
                self.requests.append(request)
                return WorkerTransportResponse(
                    status=401,
                    headers={
                        "Content-Type": "text/html",
                        "Content-Encoding": "gzip",
                        "X-Private-Marker": private_marker,
                    },
                    body=private_marker.encode("utf-8") + b"\xffnot-json",
                )

        transport = RejectingTransport()
        client = WorkerClient(
            base_url="http://8.8.8.8:30000",
            provider_token="a" * 64,
            session_id="session-1",
            session_secret=b"s" * 32,
            transport=transport,
            clock=lambda: 1000,
            nonce=lambda: "n-1",
        )

        with self.assertRaises(
            WorkerBoundaryAuthenticationError
        ) as raised:
            asyncio.run(client.health())

        self.assertIs(
            type(raised.exception),
            WorkerBoundaryAuthenticationError,
        )
        self.assertEqual(
            str(raised.exception),
            "Remote worker boundary authentication failed.",
        )
        self.assertNotIn(private_marker, str(raised.exception))
        self.assertNotIn(private_marker, repr(raised.exception))
        self.assertNotIn("a" * 64, str(raised.exception))
        self.assertNotIn("a" * 64, repr(raised.exception))
        self.assertEqual(len(transport.requests), 1)

    def test_client_rejects_invalid_boundary_tokens_before_transport(self):
        from cloud_run.worker_client import WorkerClient, WorkerClientError

        class Transport:
            requests = []

            async def request(self, request, *, max_bytes):
                self.requests.append(request)

        for value in (
            "a" * 63,
            "a" * 65,
            "A" * 64,
            " " + "a" * 63,
            "a" * 63 + "!",
            b"a" * 64,
            None,
        ):
            with self.subTest(provider_token=value):
                transport = Transport()
                with self.assertRaises(WorkerClientError):
                    WorkerClient(
                        base_url="http://8.8.8.8:30000",
                        provider_token=value,
                        session_id="session-1",
                        session_secret=b"s" * 32,
                        transport=transport,
                    )
                self.assertEqual(transport.requests, [])

    def test_client_claims_through_vast_bearer_then_uses_hmac(self):
        from cloud_run.worker_client import WorkerClient

        transport = RecordingTransport()
        client = WorkerClient(
            base_url="http://8.8.8.8:30000",
            provider_token="a" * 64,
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
            "Bearer " + "a" * 64,
        )
        self.assertNotIn(
            "X-Cloud-Run-Signature",
            transport.requests[0].headers,
        )
        self.assertEqual(
            transport.requests[1].headers["Authorization"],
            "Bearer " + "a" * 64,
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
        self.assertNotIn("a" * 64, exposed)
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
                        provider_token="a" * 64,
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
                provider_token="a" * 64,
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
            self.assertNotIn("a" * 64, str(raised.exception))

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
            provider_token="a" * 64,
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
            provider_token="a" * 64,
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

    def test_upload_resumes_in_bounded_signed_chunks_and_verifies_source(self):
        from cloud_run.worker_client import (
            MAX_WORKER_UPLOAD_CHUNK_BYTES,
            WorkerClient,
            WorkerTransportResponse,
        )

        content = b"x" * (MAX_WORKER_UPLOAD_CHUNK_BYTES + 3)
        digest = hashlib.sha256(content).hexdigest()

        class UploadTransport:
            def __init__(self):
                self.requests = []

            async def request(self, request, *, max_bytes):
                self.requests.append(request)
                content_range = request.headers["Content-Range"]
                start = int(content_range.split(" ", 1)[1].split("-", 1)[0])
                next_offset = start + len(request.body)
                return WorkerTransportResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps(
                        {
                            "artifact_id": "input-1",
                            "state": (
                                "verified"
                                if next_offset == len(content)
                                else "receiving"
                            ),
                            "next_offset": next_offset,
                            "size_bytes": len(content),
                            "sha256": digest,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8"),
                )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.bin"
            path.write_bytes(content)
            transport = UploadTransport()
            client = WorkerClient(
                base_url="http://8.8.8.8:30000",
                provider_token="a" * 64,
                session_id="session-1",
                session_secret=b"s" * 32,
                transport=transport,
                clock=lambda: 1000,
                nonce=iter(("upload-1", "upload-2")).__next__,
            )
            offsets = []

            receipt = asyncio.run(
                client.upload_artifact(
                    "input-1",
                    path=str(path),
                    size_bytes=len(content),
                    sha256=digest,
                    start=2,
                    on_progress=lambda value: offsets.append(value),
                )
            )

        self.assertEqual(
            [request.headers["Content-Range"] for request in transport.requests],
            [
                (
                    "bytes 2-"
                    + str(2 + MAX_WORKER_UPLOAD_CHUNK_BYTES - 1)
                    + "/"
                    + str(len(content))
                ),
                (
                    "bytes "
                    + str(2 + MAX_WORKER_UPLOAD_CHUNK_BYTES)
                    + "-"
                    + str(len(content) - 1)
                    + "/"
                    + str(len(content))
                ),
            ],
        )
        self.assertTrue(
            all(
                request.headers["Content-Type"]
                == "application/octet-stream"
                for request in transport.requests
            )
        )
        self.assertEqual(offsets[-1], len(content))
        self.assertEqual(receipt["state"], "verified")
        for request in transport.requests:
            path_qs = "/worker/v1/artifacts/input-1"
            verify_request(
                b"s" * 32,
                "PUT",
                path_qs,
                request.body,
                {
                    "protocol_version": request.headers[
                        "X-Cloud-Run-Protocol-Version"
                    ],
                    "timestamp": int(
                        request.headers["X-Cloud-Run-Timestamp"]
                    ),
                    "nonce": request.headers["X-Cloud-Run-Nonce"],
                    "signature": request.headers[
                        "X-Cloud-Run-Signature"
                    ],
                },
                now=1000,
                seen_nonces=set(),
            )

    def test_upload_status_reads_only_the_bound_transfer_transaction(self):
        from cloud_run.worker_client import (
            WorkerClient,
            WorkerTransportResponse,
        )

        class StatusTransport:
            def __init__(self):
                self.requests = []

            async def request(self, request, *, max_bytes):
                self.requests.append(request)
                return WorkerTransportResponse(
                    status=200,
                    headers={"Content-Type": "application/json"},
                    body=json.dumps(
                        {
                            "artifact_id": "input-1",
                            "state": "receiving",
                            "next_offset": 5,
                            "size_bytes": 10,
                            "sha256": "a" * 64,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8"),
                )

        transport = StatusTransport()
        client = WorkerClient(
            base_url="http://8.8.8.8:30000",
            provider_token="a" * 64,
            session_id="session-1",
            session_secret=b"s" * 32,
            transport=transport,
            clock=lambda: 1000,
            nonce=lambda: "status-1",
        )

        status = asyncio.run(client.upload_status("input-1"))

        self.assertEqual(status["next_offset"], 5)
        request = transport.requests[0]
        self.assertEqual(
            request.url,
            (
                "http://8.8.8.8:30000/worker/v1/transactions/"
                "transfer:input-1"
            ),
        )
        verify_request(
            b"s" * 32,
            "GET",
            "/worker/v1/transactions/transfer:input-1",
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


if __name__ == "__main__":
    unittest.main()
