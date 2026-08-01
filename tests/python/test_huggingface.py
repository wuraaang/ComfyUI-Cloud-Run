import asyncio
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cloud_run.huggingface import (
    HUGGINGFACE_ORIGIN,
    MAX_HUGGINGFACE_RESPONSE_BYTES,
    HuggingFaceClient,
    HuggingFaceError,
    HuggingFaceReference,
    ResolvedHuggingFaceFile,
    parse_huggingface_url,
)


_UNSET = object()


class FakeContent:
    def __init__(self, body):
        self.body = body
        self.offset = 0
        self.read_sizes = []

    async def read(self, size):
        self.read_sizes.append(size)
        if not isinstance(size, int) or size <= 0:
            raise AssertionError("The response must be read in bounded chunks.")
        start = self.offset
        self.offset = min(len(self.body), start + size)
        return self.body[start : self.offset]


class FakeResponse:
    def __init__(
        self,
        status,
        payload=_UNSET,
        *,
        raw_body=None,
        url=None,
        headers=None,
    ):
        if raw_body is None:
            if payload is _UNSET:
                raise ValueError("A fake response body is required.")
            raw_body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.url = url
        self.headers = dict(headers or {})
        self.content = FakeContent(raw_body)
        self.release_count = 0

    async def read(self):
        raise AssertionError("Unbounded response.read() is forbidden.")

    def release(self):
        self.release_count += 1


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.close_count = 0

    async def get(self, url, *, allow_redirects, timeout):
        self.requests.append(
            {
                "url": url,
                "allow_redirects": allow_redirects,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if response.url is None:
            response.url = url
        return response

    async def close(self):
        self.close_count += 1


class HuggingFaceUrlTests(unittest.TestCase):
    def test_accepts_mutable_resolve_url_and_removes_download_query(self):
        reference = parse_huggingface_url(
            "https://huggingface.co/black-forest-labs/FLUX.1-Fill-dev/"
            "resolve/main/flux1-fill-dev.safetensors?download=true"
        )

        self.assertEqual(
            reference,
            HuggingFaceReference(
                repository_id="black-forest-labs/FLUX.1-Fill-dev",
                revision="main",
                file_path="flux1-fill-dev.safetensors",
            ),
        )

    def test_accepts_exact_commit_and_nested_file_without_query(self):
        revision = "a" * 40

        reference = parse_huggingface_url(
            "https://huggingface.co/example/public-model/resolve/"
            + revision
            + "/models/example.safetensors"
        )

        self.assertEqual(reference.repository_id, "example/public-model")
        self.assertEqual(reference.revision, revision)
        self.assertEqual(
            reference.file_path,
            "models/example.safetensors",
        )

    def test_rejects_noncanonical_or_ambiguous_urls(self):
        valid_path = (
            "example/public-model/resolve/main/models/example.safetensors"
        )
        invalid_urls = {
            "http": "http://huggingface.co/" + valid_path,
            "hf scheme": "hf://huggingface.co/" + valid_path,
            "another host": "https://example.com/" + valid_path,
            "user info": "https://user@huggingface.co/" + valid_path,
            "port": "https://huggingface.co:443/" + valid_path,
            "fragment": "https://huggingface.co/" + valid_path + "#part",
            "percent escape": (
                "https://huggingface.co/example/public-model/resolve/main/"
                "models/%65xample.safetensors"
            ),
            "backslash": (
                "https://huggingface.co/example/public-model/resolve/main/"
                "models\\example.safetensors"
            ),
            "duplicate query": (
                "https://huggingface.co/"
                + valid_path
                + "?download=true&download=true"
            ),
            "unknown query": (
                "https://huggingface.co/" + valid_path + "?raw=true"
            ),
            "wrong query value": (
                "https://huggingface.co/" + valid_path + "?download=false"
            ),
            "absolute-like file": (
                "https://huggingface.co/example/public-model/resolve/main/"
                "/etc/passwd"
            ),
            "traversal file": (
                "https://huggingface.co/example/public-model/resolve/main/"
                "../example.safetensors"
            ),
            "dot file segment": (
                "https://huggingface.co/example/public-model/resolve/main/"
                "./example.safetensors"
            ),
            "missing owner": (
                "https://huggingface.co/public-model/resolve/main/"
                "example.safetensors"
            ),
            "missing repository": (
                "https://huggingface.co/example//resolve/main/"
                "example.safetensors"
            ),
            "missing revision": (
                "https://huggingface.co/example/public-model/resolve//"
                "example.safetensors"
            ),
            "missing file": (
                "https://huggingface.co/example/public-model/resolve/main"
            ),
            "extra path prefix": (
                "https://huggingface.co/models/example/public-model/resolve/"
                "main/example.safetensors"
            ),
            "blob path": (
                "https://huggingface.co/example/public-model/blob/main/"
                "example.safetensors"
            ),
            "dataset path": (
                "https://huggingface.co/datasets/example/public-model/"
                "resolve/main/example.safetensors"
            ),
            "space": (
                "https://huggingface.co/example/public-model/resolve/main/"
                "example model.safetensors"
            ),
            "overlong owner": (
                "https://huggingface.co/"
                + "a" * 97
                + "/public-model/resolve/main/example.safetensors"
            ),
            "overlong repository": (
                "https://huggingface.co/example/"
                + "a" * 97
                + "/resolve/main/example.safetensors"
            ),
            "overlong revision": (
                "https://huggingface.co/example/public-model/resolve/"
                + "a" * 201
                + "/example.safetensors"
            ),
            "overlong file segment": (
                "https://huggingface.co/example/public-model/resolve/main/"
                + "a" * 201
            ),
        }

        for label, value in invalid_urls.items():
            with self.subTest(label=label):
                with self.assertRaises(HuggingFaceError):
                    parse_huggingface_url(value)


class HuggingFaceClientTests(unittest.TestCase):
    repository_id = "example/public-model"
    file_path = "models/example.safetensors"
    immutable_revision = "a" * 40
    digest = "b" * 64

    def _reference(self, revision=None):
        return HuggingFaceReference(
            repository_id=self.repository_id,
            revision=revision or self.immutable_revision,
            file_path=self.file_path,
        )

    def _mutable_payload(self, **overrides):
        payload = {
            "id": self.repository_id,
            "sha": self.immutable_revision,
            "private": False,
            "gated": False,
            "siblings": [],
        }
        payload.update(overrides)
        return payload

    def _sibling(self, **overrides):
        sibling = {
            "rfilename": self.file_path,
            "size": 4096,
            "lfs": {"sha256": self.digest, "size": 4096},
        }
        sibling.update(overrides)
        return sibling

    def _pinned_payload(self, **overrides):
        payload = {
            "id": self.repository_id,
            "sha": self.immutable_revision,
            "private": False,
            "gated": False,
            "siblings": [self._sibling()],
        }
        payload.update(overrides)
        return payload

    def _resolve(self, responses, *, reference=None, expected_sha256=None):
        session = FakeSession(responses)
        result = asyncio.run(
            HuggingFaceClient(session=session).resolve(
                reference or self._reference(),
                expected_sha256=expected_sha256,
            )
        )
        return result, session

    def test_mutable_revision_resolves_then_queries_exact_commit(self):
        mutable_response = FakeResponse(200, self._mutable_payload())
        pinned_response = FakeResponse(200, self._pinned_payload())

        result, session = self._resolve(
            [mutable_response, pinned_response],
            reference=self._reference("main"),
            expected_sha256=self.digest,
        )

        self.assertEqual(
            result,
            ResolvedHuggingFaceFile(
                repository_id=self.repository_id,
                file_path=self.file_path,
                immutable_revision=self.immutable_revision,
                locator=(
                    HUGGINGFACE_ORIGIN
                    + "/"
                    + self.repository_id
                    + "/resolve/"
                    + self.immutable_revision
                    + "/"
                    + self.file_path
                ),
                size_bytes=4096,
                sha256=self.digest,
            ),
        )
        self.assertEqual(
            [request["url"] for request in session.requests],
            [
                HUGGINGFACE_ORIGIN
                + "/api/models/example/public-model/revision/main",
                HUGGINGFACE_ORIGIN
                + "/api/models/example/public-model/revision/"
                + self.immutable_revision
                + "?blobs=true",
            ],
        )
        self.assertTrue(
            all(
                request["allow_redirects"] is False
                and request["timeout"] == 30.0
                and set(request) == {"url", "allow_redirects", "timeout"}
                for request in session.requests
            )
        )
        self.assertEqual(mutable_response.release_count, 1)
        self.assertEqual(pinned_response.release_count, 1)
        self.assertEqual(session.close_count, 0)

    def test_exact_commit_queries_only_pinned_metadata(self):
        result, session = self._resolve(
            [FakeResponse(200, self._pinned_payload())]
        )

        self.assertEqual(result.immutable_revision, self.immutable_revision)
        self.assertEqual(
            [request["url"] for request in session.requests],
            [
                HUGGINGFACE_ORIGIN
                + "/api/models/example/public-model/revision/"
                + self.immutable_revision
                + "?blobs=true"
            ],
        )

    def test_rejects_http_redirect_substitution_and_invalid_bodies(self):
        request_url = (
            HUGGINGFACE_ORIGIN
            + "/api/models/example/public-model/revision/"
            + self.immutable_revision
            + "?blobs=true"
        )
        cases = {
            "not found": [FakeResponse(404, {})],
            "redirect": [
                FakeResponse(
                    302,
                    {},
                    headers={"Location": "https://evil.example/model"},
                )
            ],
            "response URL substitution": [
                FakeResponse(
                    200,
                    self._pinned_payload(),
                    url=request_url + "&other=true",
                )
            ],
            "invalid JSON": [FakeResponse(200, raw_body=b"{")],
            "invalid UTF-8": [FakeResponse(200, raw_body=b"\xff")],
            "oversized body": [
                FakeResponse(
                    200,
                    raw_body=b"x" * (MAX_HUGGINGFACE_RESPONSE_BYTES + 1),
                )
            ],
        }

        for label, responses in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(HuggingFaceError):
                    self._resolve(responses)
                self.assertEqual(responses[0].release_count, 1)

    def test_rejects_substituted_or_nonpublic_repository_metadata(self):
        cases = {
            "non-object body": [],
            "substituted repository": self._pinned_payload(
                id="other/public-model"
            ),
            "substituted commit": self._pinned_payload(sha="c" * 40),
            "uppercase commit": self._pinned_payload(sha="A" * 40),
            "private": self._pinned_payload(private=True),
            "missing private": {
                key: value
                for key, value in self._pinned_payload().items()
                if key != "private"
            },
            "gated": self._pinned_payload(gated="auto"),
            "missing gated": {
                key: value
                for key, value in self._pinned_payload().items()
                if key != "gated"
            },
        }

        for label, payload in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(HuggingFaceError):
                    self._resolve([FakeResponse(200, payload)])

    def test_rejects_missing_duplicate_or_unverifiable_exact_file(self):
        cases = {
            "siblings is not a list": self._pinned_payload(siblings={}),
            "missing sibling": self._pinned_payload(
                siblings=[self._sibling(rfilename="models/other.bin")]
            ),
            "duplicate sibling": self._pinned_payload(
                siblings=[self._sibling(), self._sibling()]
            ),
            "non-object sibling": self._pinned_payload(siblings=[None]),
            "non-LFS sibling": self._pinned_payload(
                siblings=[
                    {
                        "rfilename": self.file_path,
                        "size": 4096,
                    }
                ]
            ),
            "non-object LFS": self._pinned_payload(
                siblings=[self._sibling(lfs=None)]
            ),
            "missing LFS size": self._pinned_payload(
                siblings=[
                    self._sibling(lfs={"sha256": self.digest})
                ]
            ),
            "zero LFS size": self._pinned_payload(
                siblings=[
                    self._sibling(
                        size=0,
                        lfs={"sha256": self.digest, "size": 0},
                    )
                ]
            ),
            "boolean LFS size": self._pinned_payload(
                siblings=[
                    self._sibling(
                        size=True,
                        lfs={"sha256": self.digest, "size": True},
                    )
                ]
            ),
            "missing LFS digest": self._pinned_payload(
                siblings=[self._sibling(lfs={"size": 4096})]
            ),
            "uppercase LFS digest": self._pinned_payload(
                siblings=[
                    self._sibling(
                        lfs={"sha256": "B" * 64, "size": 4096}
                    )
                ]
            ),
            "short LFS digest": self._pinned_payload(
                siblings=[
                    self._sibling(
                        lfs={"sha256": "b" * 63, "size": 4096}
                    )
                ]
            ),
            "size disagreement": self._pinned_payload(
                siblings=[self._sibling(size=4097)]
            ),
        }

        for label, payload in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(HuggingFaceError):
                    self._resolve([FakeResponse(200, payload)])

    def test_rejects_embedded_digest_mismatch(self):
        with self.assertRaises(HuggingFaceError):
            self._resolve(
                [FakeResponse(200, self._pinned_payload())],
                expected_sha256="c" * 64,
            )

    def test_mutable_lookup_rejects_substituted_identity(self):
        cases = {
            "repository": self._mutable_payload(id="other/model"),
            "missing sha": {
                key: value
                for key, value in self._mutable_payload().items()
                if key != "sha"
            },
            "uppercase sha": self._mutable_payload(sha="A" * 40),
        }

        for label, payload in cases.items():
            with self.subTest(label=label):
                session = FakeSession([FakeResponse(200, payload)])
                with self.assertRaises(HuggingFaceError):
                    asyncio.run(
                        HuggingFaceClient(session=session).resolve(
                            self._reference("main")
                        )
                    )
                self.assertEqual(len(session.requests), 1)

    def test_constructor_rejects_unbounded_transport_settings(self):
        for options in (
            {"timeout": 0},
            {"timeout": 30.1},
            {"max_response_bytes": 0},
            {"max_response_bytes": MAX_HUGGINGFACE_RESPONSE_BYTES + 1},
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    HuggingFaceClient(**options)

    def test_owned_session_disables_environment_and_is_closed(self):
        response = FakeResponse(200, self._pinned_payload())
        created = []

        class FakeClientTimeout:
            def __init__(self, *, total):
                self.total = total

        def create_session(*, timeout, trust_env):
            session = FakeSession([response])
            session.constructor_timeout = timeout
            session.trust_env = trust_env
            created.append(session)
            return session

        fake_aiohttp = SimpleNamespace(
            ClientSession=create_session,
            ClientTimeout=FakeClientTimeout,
        )
        with patch.dict(sys.modules, {"aiohttp": fake_aiohttp}):
            result = asyncio.run(HuggingFaceClient().resolve(self._reference()))

        self.assertEqual(result.sha256, self.digest)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].constructor_timeout.total, 30.0)
        self.assertIs(created[0].trust_env, False)
        self.assertEqual(created[0].close_count, 1)


if __name__ == "__main__":
    unittest.main()
