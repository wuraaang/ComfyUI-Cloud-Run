import asyncio
import json
import unittest

from cloud_run.registry import RegistryClient, RegistryError


class FakeResponse:
    def __init__(self, status, payload, *, headers=None):
        self.status = status
        self.payload = payload
        self.headers = headers or {}

    async def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.options = []

    async def get(self, url, **options):
        self.urls.append(url)
        self.options.append(options)
        return self.responses.pop(0)


class RegistryClientTests(unittest.TestCase):
    def test_registry_infers_node_then_reads_an_immutable_release(self):
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    {
                        "id": "acme.fancy",
                        "repository": "https://github.com/acme/fancy",
                    },
                ),
                FakeResponse(
                    200,
                    [
                        {
                            "version": "1.2.3",
                            "status": "NodeVersionStatusActive",
                            "git_commit": "a" * 40,
                        }
                    ],
                ),
            ]
        )

        result = asyncio.run(
            RegistryClient(session=session).infer_package("Fancy")
        )

        self.assertEqual(
            session.urls,
            [
                "https://api.comfy.org/comfy-nodes/Fancy/node",
                "https://api.comfy.org/nodes/acme.fancy/versions",
            ],
        )
        self.assertTrue(
            all(options["allow_redirects"] is False for options in session.options)
        )
        self.assertEqual(result.package_id, "acme.fancy")
        self.assertEqual(result.repository_url, "https://github.com/acme/fancy")
        self.assertEqual(result.revision, "a" * 40)
        self.assertEqual(result.version, "1.2.3")

    def test_registry_returns_none_for_unknown_and_sanitizes_bad_responses(self):
        unknown = RegistryClient(
            session=FakeSession([FakeResponse(404, {})])
        )
        self.assertIsNone(asyncio.run(unknown.infer_package("Missing")))

        for response in (
            FakeResponse(302, {}, headers={"Location": "https://evil.example"}),
            FakeResponse(200, {"id": "bad", "repository": "http://evil"}),
            FakeResponse(200, "x" * (2 * 1024 * 1024 + 1)),
        ):
            client = RegistryClient(session=FakeSession([response]))
            with self.assertRaisesRegex(RegistryError, "Registry"):
                asyncio.run(client.infer_package("Fancy"))


if __name__ == "__main__":
    unittest.main()
