import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from cloud_run.manifest import ArtifactSpec, PythonWheelSpec, SourceSpec
from cloud_run.worker_protocol import sign_request


REVISION = "a" * 40
SOURCE_URL = (
    "https://huggingface.co/example/model/resolve/"
    + REVISION
    + "/model.bin"
)


def artifact_for(
    payload,
    *,
    artifact_id="model-1",
    destination="models/checkpoints/model.bin",
    digest=None,
    source_kind="huggingface",
):
    source = (
        SourceSpec(
            kind="local-upload",
            locator=f"local-upload:{artifact_id}",
        )
        if source_kind == "local-upload"
        else SourceSpec(
            kind="huggingface",
            locator=SOURCE_URL,
            immutable_revision=REVISION,
        )
    )
    return ArtifactSpec(
        artifact_id=artifact_id,
        kind="model",
        logical_name=artifact_id,
        destination=destination,
        size_bytes=len(payload),
        sha256=digest or hashlib.sha256(payload).hexdigest(),
        source=source,
    )


def archive_artifact_for(payload):
    return ArtifactSpec(
        artifact_id="fancy-archive",
        kind="custom_node_archive",
        logical_name="fancy",
        destination="custom_nodes/fancy",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        source=SourceSpec(
            kind="local-upload",
            locator="local-upload:fancy-archive",
        ),
    )


class FakeResponse:
    def __init__(self, *, status, headers, url, payload, chunk_size=3):
        self.status = status
        self.headers = headers
        self.url = url
        self.payload = payload
        self.chunk_size = chunk_size

    async def iter_chunks(self, maximum_bytes):
        size = min(self.chunk_size, maximum_bytes)
        for offset in range(0, len(self.payload), size):
            await asyncio.sleep(0)
            yield self.payload[offset : offset + size]


class FakeRangeClient:
    def __init__(
        self,
        payload,
        *,
        redirect=None,
        ignores_range=False,
        explicit_no_range=False,
        failures=0,
        gate=None,
        activity=None,
    ):
        self.payload = payload
        self.redirect = redirect
        self.ignores_range = ignores_range
        self.explicit_no_range = explicit_no_range
        self.failures = failures
        self.gate = gate
        self.activity = activity
        self.ranges = []
        self.requests = []

    async def get(self, url, *, headers, allow_redirects):
        self.requests.append((url, dict(headers), allow_redirects))
        self.ranges.append(headers.get("Range"))
        if self.failures:
            self.failures -= 1
            raise OSError("temporary transport failure")
        if self.redirect is not None:
            return FakeResponse(
                status=302,
                headers={"Location": self.redirect},
                url=url,
                payload=b"",
            )

        offset = 0
        range_header = headers.get("Range")
        if range_header is not None:
            offset = int(range_header.removeprefix("bytes=").removesuffix("-"))
        if self.activity is not None:
            self.activity["active"] += 1
            self.activity["maximum"] = max(
                self.activity["maximum"],
                self.activity["active"],
            )
        if self.gate is not None:
            await self.gate.wait()

        if range_header is not None and not self.ignores_range:
            body = self.payload[offset:]
            status = 206
            headers_out = {
                "Content-Range": (
                    f"bytes {offset}-{len(self.payload) - 1}/"
                    f"{len(self.payload)}"
                ),
                "Content-Length": str(len(body)),
            }
        else:
            body = self.payload
            status = 200
            headers_out = {"Content-Length": str(len(body))}
            if range_header is not None and self.explicit_no_range:
                headers_out["Accept-Ranges"] = "none"
        response = FakeResponse(
            status=status,
            headers=headers_out,
            url=url,
            payload=body,
        )
        if self.activity is not None:
            original_iterator = response.iter_chunks

            async def iter_chunks(maximum_bytes):
                try:
                    async for chunk in original_iterator(maximum_bytes):
                        yield chunk
                finally:
                    self.activity["active"] -= 1

            response.iter_chunks = iter_chunks
        return response


async def no_sleep(_seconds):
    return None


class TransferManagerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "comfy"
        self.root.mkdir()

    def manager(self, **kwargs):
        from remote_worker.transfers import TransferManager

        return TransferManager(
            root=self.root,
            sleeper=no_sleep,
            **kwargs,
        )

    def test_download_resumes_part_verifies_hash_and_renames_atomically(self):
        payload = b"verified-model"
        artifact = artifact_for(payload)
        manager = self.manager()
        destination = self.root / artifact.destination
        destination.parent.mkdir(parents=True)
        part = destination.with_suffix(".part")
        part.write_bytes(payload[:4])
        client = FakeRangeClient(payload)

        result = asyncio.run(manager.download(artifact, client=client))

        self.assertEqual(client.ranges, ["bytes=4-"])
        self.assertEqual(result.state, "verified")
        self.assertEqual(result.size_bytes, len(payload))
        self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse(part.exists())

    def test_progress_enters_verification_before_verified_completion(self):
        payload = b"verified-progress-model"
        artifact = artifact_for(payload)
        destination = self.root / artifact.destination
        events = []

        def progress(artifact_id, offset, event):
            events.append((artifact_id, offset, event))
            if event == "verified":
                self.assertEqual(destination.read_bytes(), payload)

        result = asyncio.run(
            self.manager(progress=progress).download(
                artifact,
                client=FakeRangeClient(payload),
            )
        )

        self.assertEqual(result.state, "verified")
        names = [event for _artifact_id, _offset, event in events]
        self.assertIn("transferring", names)
        self.assertLess(names.index("transferring"), names.index("verifying"))
        self.assertLess(names.index("verifying"), names.index("verified"))
        self.assertEqual(events[-1], (artifact.artifact_id, len(payload), "verified"))
        self.assertTrue(
            all(
                artifact_id == artifact.artifact_id
                and 0 <= offset <= artifact.size_bytes
                for artifact_id, offset, _event in events
            )
        )

    def test_failed_digest_never_reports_verified_completion(self):
        from remote_worker.transfers import TransferError

        payload = b"digest-mismatch"
        artifact = artifact_for(payload, digest="0" * 64)
        events = []

        with self.assertRaises(TransferError):
            asyncio.run(
                self.manager(
                    max_retries=0,
                    progress=lambda artifact_id, offset, event: events.append(
                        (artifact_id, offset, event)
                    ),
                ).download(
                    artifact,
                    client=FakeRangeClient(payload),
                )
            )

        self.assertIn("verifying", [event for *_rest, event in events])
        self.assertNotIn("verified", [event for *_rest, event in events])

    def test_wrong_length_hash_redirect_and_non_range_resume_are_rejected(self):
        from remote_worker.transfers import TransferError

        expected = b"payload"
        cases = (
            (
                artifact_for(expected),
                FakeRangeClient(b"wrong"),
                False,
            ),
            (
                artifact_for(expected, digest="0" * 64),
                FakeRangeClient(expected),
                False,
            ),
            (
                artifact_for(expected),
                FakeRangeClient(
                    expected,
                    redirect="https://evil.example/x",
                ),
                False,
            ),
            (
                artifact_for(expected),
                FakeRangeClient(expected, ignores_range=True),
                True,
            ),
        )
        for artifact, client, seed_part in cases:
            with self.subTest(
                redirect=client.redirect,
                ignores_range=client.ignores_range,
                digest=artifact.sha256[:4],
            ):
                manager = self.manager(max_retries=0)
                destination = self.root / artifact.destination
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.unlink(missing_ok=True)
                destination.with_suffix(".part").unlink(missing_ok=True)
                if seed_part:
                    destination.with_suffix(".part").write_bytes(
                        expected[:2]
                    )
                with self.assertRaises(TransferError):
                    asyncio.run(manager.download(artifact, client=client))
                self.assertFalse(destination.exists())

    def test_download_retries_transport_only_and_caps_concurrency_at_four(self):
        payload = b"concurrent-payload"
        retrying = FakeRangeClient(payload, failures=2)
        retry_result = asyncio.run(
            self.manager(max_retries=3).download(
                artifact_for(payload, artifact_id="retry"),
                client=retrying,
            )
        )
        self.assertEqual(retry_result.state, "verified")
        self.assertEqual(len(retrying.requests), 3)

        activity = {"active": 0, "maximum": 0}
        artifacts = tuple(
            artifact_for(
                payload,
                artifact_id=f"model-{index}",
                destination=f"models/checkpoints/model-{index}.bin",
            )
            for index in range(6)
        )

        async def exercise():
            gate = asyncio.Event()
            client = FakeRangeClient(
                payload,
                gate=gate,
                activity=activity,
            )
            manager = self.manager(max_concurrency=4)
            task = asyncio.create_task(
                manager.download_many(artifacts, client=client)
            )
            for _ in range(100):
                if activity["active"] == 4:
                    break
                await asyncio.sleep(0)
            self.assertEqual(activity["active"], 4)
            gate.set()
            return await task

        results = asyncio.run(exercise())
        self.assertEqual(activity["maximum"], 4)
        self.assertEqual(len(results), 6)
        self.assertTrue(all(result.state == "verified" for result in results))

    def test_explicit_non_resumable_response_resets_once_to_byte_zero(self):
        payload = b"non-resumable-payload"
        artifact = artifact_for(payload)
        destination = self.root / artifact.destination
        destination.parent.mkdir(parents=True)
        destination.with_suffix(".part").write_bytes(payload[:3])
        client = FakeRangeClient(
            payload,
            ignores_range=True,
            explicit_no_range=True,
        )

        result = asyncio.run(
            self.manager(max_retries=0).download(
                artifact,
                client=client,
            )
        )

        self.assertEqual(client.ranges, ["bytes=3-", None])
        self.assertEqual(result.state, "verified")
        self.assertEqual(destination.read_bytes(), payload)

    def test_destination_symlink_and_invalid_existing_final_fail_closed(self):
        from remote_worker.transfers import TransferError

        payload = b"payload"
        outside = self.root.parent / "outside"
        outside.mkdir()
        models = self.root / "models"
        os.symlink(outside, models)
        with self.assertRaises(TransferError):
            asyncio.run(
                self.manager(max_retries=0).download(
                    artifact_for(payload),
                    client=FakeRangeClient(payload),
                )
            )
        models.unlink()

        destination = self.root / "models/checkpoints/model.bin"
        destination.parent.mkdir(parents=True)
        outside_part = outside / "shared-part"
        outside_part.write_bytes(payload[:3])
        os.link(outside_part, destination.with_suffix(".part"))
        with self.assertRaises(TransferError):
            asyncio.run(
                self.manager(max_retries=0).download(
                    artifact_for(payload),
                    client=FakeRangeClient(payload),
                )
            )
        self.assertEqual(outside_part.read_bytes(), payload[:3])
        destination.with_suffix(".part").unlink()

        destination.write_bytes(b"not-the-manifest-content")
        with self.assertRaises(TransferError):
            asyncio.run(
                self.manager(max_retries=0).download(
                    artifact_for(payload),
                    client=FakeRangeClient(payload),
                )
            )
        self.assertEqual(
            destination.read_bytes(),
            b"not-the-manifest-content",
        )

    def test_upload_requires_exact_durable_offset_and_resumes_after_reopen(self):
        from remote_worker.transfers import TransferError

        payload = b"verified-upload"
        artifact = artifact_for(payload)
        first = payload[:5]
        manager = self.manager()

        partial = asyncio.run(
            manager.upload(
                artifact,
                content_range=f"bytes 0-4/{len(payload)}",
                body=first,
            )
        )
        self.assertEqual(partial.state, "receiving")
        self.assertEqual(partial.next_offset, 5)

        reopened = self.manager()
        with self.assertRaises(TransferError):
            asyncio.run(
                reopened.upload(
                    artifact,
                    content_range=(
                        f"bytes 4-{len(payload) - 1}/{len(payload)}"
                    ),
                    body=payload[4:],
                )
            )
        completed = asyncio.run(
            reopened.upload(
                artifact,
                content_range=(
                    f"bytes 5-{len(payload) - 1}/{len(payload)}"
                ),
                body=payload[5:],
            )
        )

        destination = self.root / artifact.destination
        self.assertEqual(completed.state, "verified")
        self.assertEqual(completed.next_offset, len(payload))
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse(destination.with_suffix(".part").exists())

    def test_custom_node_archive_uses_private_staging_not_final_code_path(self):
        payload = b"deterministic-tar-payload"
        artifact = archive_artifact_for(payload)
        artifact_root = self.root.parent / "worker-artifacts"
        artifact_root.mkdir()
        from remote_worker.transfers import TransferManager

        manager = TransferManager(
            root=self.root,
            artifact_root=artifact_root,
            sleeper=no_sleep,
        )

        result = asyncio.run(
            manager.upload(
                artifact,
                content_range=f"bytes 0-{len(payload) - 1}/{len(payload)}",
                body=payload,
            )
        )

        self.assertEqual(
            result.destination,
            artifact_root.resolve() / "fancy-archive.tar",
        )
        self.assertEqual(result.destination.read_bytes(), payload)
        self.assertFalse((self.root / "custom_nodes/fancy").exists())

    def test_manifest_wheel_transfers_to_exact_private_wheel_filename(self):
        from remote_worker.transfers import TransferManager, wheel_artifact

        payload = b"verified-wheel"
        wheel = PythonWheelSpec(
            filename="dep-1.0-py3-none-any.whl",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            source=SourceSpec(
                kind="local-upload",
                locator="local-upload:wheel-dep",
            ),
        )
        wheel_root = self.root.parent / "worker-wheels"
        wheel_root.mkdir()
        manager = TransferManager(
            root=self.root,
            wheel_root=wheel_root,
            sleeper=no_sleep,
        )
        artifact = wheel_artifact(wheel)

        result = asyncio.run(
            manager.upload(
                artifact,
                content_range=f"bytes 0-{len(payload) - 1}/{len(payload)}",
                body=payload,
            )
        )

        self.assertEqual(artifact.artifact_id, "wheel-dep")
        self.assertEqual(artifact.kind, "python_wheel")
        self.assertEqual(
            result.destination,
            wheel_root.resolve() / wheel.filename,
        )
        self.assertEqual(result.destination.read_bytes(), payload)

    def test_verify_and_explicit_repair_reset_stay_confined(self):
        from remote_worker.transfers import TransferManager

        payload = b"verified-reset"
        artifact = artifact_for(payload)
        manager = TransferManager(root=self.root, sleeper=no_sleep)
        self.assertIsNone(manager.verify(artifact))
        uploaded = asyncio.run(
            manager.upload(
                artifact,
                content_range=f"bytes 0-{len(payload) - 1}/{len(payload)}",
                body=payload,
            )
        )

        self.assertEqual(manager.verify(artifact), uploaded)
        manager.reset(artifact)
        self.assertIsNone(manager.verify(artifact))
        self.assertFalse(uploaded.destination.exists())


class FakeWorkerRequest:
    def __init__(
        self,
        method,
        path,
        *,
        body=b"",
        envelope=None,
        content_range=None,
    ):
        self.method = method
        self.path = path
        self.body = body
        self.boundary_authenticated = True
        self.auth_envelope = envelope
        self.headers = (
            {"Content-Range": content_range}
            if content_range is not None
            else {}
        )


class WorkerUploadRouteTests(unittest.TestCase):
    def test_signed_upload_route_accepts_only_manifest_artifact_id(self):
        from remote_worker.server import WorkerApplication
        from remote_worker.transfers import TransferManager

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state" / "worker.json"
            transfer_root = root / "comfy"
            transfer_root.mkdir()
            payload = b"route-upload"
            artifact = artifact_for(payload, source_kind="local-upload")
            manager = TransferManager(root=transfer_root, sleeper=no_sleep)
            with self.assertRaises(ValueError):
                WorkerApplication(
                    state_path=state_path,
                    transfer_manager=manager,
                    upload_artifacts=(artifact_for(payload),),
                )
            worker = WorkerApplication(
                state_path=state_path,
                clock=lambda: 1000,
                transfer_manager=manager,
                upload_artifacts=(artifact,),
            )

            claim = json.dumps(
                {
                    "protocol_version": "1",
                    "session_id": "session-1",
                    "session_secret_hex": "a" * 64,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            accepted_claim = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "POST",
                        "/worker/v1/claim",
                        body=claim,
                    )
                )
            )
            self.assertEqual(accepted_claim.status, 200)

            path = "/worker/v1/artifacts/model-1"
            envelope = sign_request(
                bytes.fromhex("a" * 64),
                "PUT",
                path,
                payload,
                timestamp=1000,
                nonce="upload-1",
            )
            accepted = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "PUT",
                        path,
                        body=payload,
                        envelope=envelope,
                        content_range=(
                            f"bytes 0-{len(payload) - 1}/{len(payload)}"
                        ),
                    )
                )
            )
            transfer_path = "/worker/v1/transactions/transfer:model-1"
            transfer_status = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "GET",
                        transfer_path,
                        envelope=sign_request(
                            bytes.fromhex("a" * 64),
                            "GET",
                            transfer_path,
                            b"",
                            timestamp=1000,
                            nonce="upload-status",
                        ),
                    )
                )
            )

            unknown_path = "/worker/v1/artifacts/not-in-manifest"
            unknown = asyncio.run(
                worker.handle(
                    FakeWorkerRequest(
                        "PUT",
                        unknown_path,
                        body=payload,
                        envelope=sign_request(
                            bytes.fromhex("a" * 64),
                            "PUT",
                            unknown_path,
                            payload,
                            timestamp=1000,
                            nonce="upload-2",
                        ),
                        content_range=(
                            f"bytes 0-{len(payload) - 1}/{len(payload)}"
                        ),
                    )
                )
            )

            self.assertEqual(
                accepted.payload,
                {
                    "artifact_id": "model-1",
                    "state": "verified",
                    "next_offset": len(payload),
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                },
            )
            self.assertEqual(transfer_status.payload, accepted.payload)
            self.assertEqual(unknown.status, 404)
            transfer_record = (
                worker.state.load()["transactions"]["transfer:model-1"]
            )
            self.assertEqual(
                transfer_record,
                {
                    "kind": "artifact_transfer",
                    "artifact_id": "model-1",
                    "state": "verified",
                    "offset": len(payload),
                    "size_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                },
            )


if __name__ == "__main__":
    unittest.main()
