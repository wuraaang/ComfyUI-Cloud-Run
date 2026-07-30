import asyncio
import math
import sys
import types
import unittest


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        self.json_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def json(self):
        self.json_calls += 1
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class VastRequestTests(unittest.TestCase):
    def test_search_posts_exact_read_only_contract_and_normalizes_result(self):
        from cloud_run.vast import OFFER_SEARCH_URL, search_offers

        session = FakeSession(
            FakeResponse(
                200,
                {
                    "offers": [
                        {
                            "id": 42,
                            "gpu_name": "RTX 4090",
                            "gpu_ram": 24576,
                            "dph_total": 0.42,
                            "reliability": 0.96,
                            "reliability2": 0.987,
                            "host_id": 123,
                            "public_ipaddr": "192.0.2.10",
                        }
                    ]
                },
            )
        )

        offers = asyncio.run(
            search_offers(
                "synthetic-value",
                max_price_per_hour=0.75,
                min_vram_gb=24,
                session=session,
            )
        )

        self.assertEqual(
            OFFER_SEARCH_URL,
            "https://console.vast.ai/api/v0/bundles/",
        )
        self.assertEqual(len(session.calls), 1)
        url, request = session.calls[0]
        self.assertEqual(url, OFFER_SEARCH_URL)
        self.assertEqual(
            request["headers"],
            {
                "Authorization": "Bearer synthetic-value",
                "Accept": "application/json",
            },
        )
        self.assertEqual(
            request["json"],
            {
                "gpu_ram": {"gte": 24 * 1024},
                "reliability": {"gte": 0.95},
                "rentable": {"eq": True},
                "dph_total": {"lte": 0.75},
                "num_gpus": {"eq": 1},
                "verified": {"eq": True},
                "type": "ondemand",
                "limit": 20,
            },
        )
        self.assertEqual(
            offers,
            [
                {
                    "offer_id": 42,
                    "gpu_name": "RTX 4090",
                    "gpu_ram_gb": 24.0,
                    "dph_total": 0.42,
                    "reliability": 0.987,
                }
            ],
        )

    def test_missing_key_fails_before_request(self):
        from cloud_run.vast import OfferSearchConfigurationError, search_offers

        session = FakeSession(FakeResponse(200, {"offers": []}))
        with self.assertRaises(OfferSearchConfigurationError):
            asyncio.run(
                search_offers(
                    " ",
                    max_price_per_hour=0.75,
                    min_vram_gb=24,
                    session=session,
                )
            )
        self.assertEqual(session.calls, [])


class VastNormalizationAndErrorTests(unittest.TestCase):
    def test_normalization_sanitizes_filters_and_sorts_offers(self):
        from cloud_run.vast import normalize_offers

        payload = {
            "offers": [
                {
                    "id": 20,
                    "gpu_name": " RTX 4090 ",
                    "gpu_ram": 24576,
                    "dph_total": 0.5,
                    "reliability": 0.96,
                    "host_id": 88,
                    "public_ipaddr": "192.0.2.1",
                },
                {
                    "id": 10,
                    "gpu_name": "RTX 5090",
                    "gpu_ram": 32768,
                    "dph_total": 0.25,
                    "reliability": 0.97,
                    "reliability2": 0.99,
                    "machine_id": 77,
                },
                {
                    "id": 30,
                    "gpu_name": "too expensive",
                    "gpu_ram": 49152,
                    "dph_total": 0.76,
                    "reliability": 0.99,
                },
                {
                    "id": 31,
                    "gpu_name": "too little VRAM",
                    "gpu_ram": 16384,
                    "dph_total": 0.1,
                    "reliability": 0.99,
                },
                {
                    "id": 32,
                    "gpu_name": "not reliable",
                    "gpu_ram": 24576,
                    "dph_total": 0.1,
                    "reliability": 0.94,
                },
                {
                    "id": None,
                    "gpu_name": "missing id",
                    "gpu_ram": 24576,
                    "dph_total": 0.1,
                    "reliability": 0.99,
                },
                {
                    "id": 33,
                    "gpu_name": {"not": "text"},
                    "gpu_ram": 24576,
                    "dph_total": 0.1,
                    "reliability": 0.99,
                },
                {
                    "id": 34,
                    "gpu_name": "non-finite",
                    "gpu_ram": 24576,
                    "dph_total": math.nan,
                    "reliability": 0.99,
                },
            ],
            "provider_metadata": "must not survive",
        }

        offers = normalize_offers(
            payload,
            max_price_per_hour=0.75,
            min_vram_gb=24,
        )

        self.assertEqual(
            offers,
            [
                {
                    "offer_id": 10,
                    "gpu_name": "RTX 5090",
                    "gpu_ram_gb": 32.0,
                    "dph_total": 0.25,
                    "reliability": 0.99,
                },
                {
                    "offer_id": 20,
                    "gpu_name": "RTX 4090",
                    "gpu_ram_gb": 24.0,
                    "dph_total": 0.5,
                    "reliability": 0.96,
                },
            ],
        )
        self.assertEqual(
            set(offers[0]),
            {
                "offer_id",
                "gpu_name",
                "gpu_ram_gb",
                "dph_total",
                "reliability",
            },
        )

    def test_malformed_payload_is_a_sanitized_failure(self):
        from cloud_run.vast import OfferSearchError, search_offers

        sensitive_marker = "do-not-echo-this-marker"
        malformed_payloads = [
            [sensitive_marker],
            {"offers": sensitive_marker},
            ValueError(sensitive_marker),
        ]
        for payload in malformed_payloads:
            with self.subTest(payload=type(payload).__name__):
                session = FakeSession(FakeResponse(200, payload))
                with self.assertRaises(OfferSearchError) as raised:
                    asyncio.run(
                        search_offers(
                            "synthetic-value",
                            max_price_per_hour=0.75,
                            min_vram_gb=24,
                            session=session,
                        )
                    )
                self.assertEqual(
                    str(raised.exception),
                    "Vast returned an invalid offer response.",
                )
                self.assertNotIn(sensitive_marker, str(raised.exception))

    def test_http_and_transport_errors_never_expose_provider_details(self):
        from cloud_run.vast import OfferSearchError, search_offers

        sensitive_marker = "do-not-echo-this-marker"
        response = FakeResponse(500, {"error": sensitive_marker})
        with self.assertRaises(OfferSearchError) as http_error:
            asyncio.run(
                search_offers(
                    "synthetic-value",
                    max_price_per_hour=0.75,
                    min_vram_gb=24,
                    session=FakeSession(response),
                )
            )
        self.assertEqual(
            str(http_error.exception),
            "Vast offer search is temporarily unavailable.",
        )
        self.assertEqual(response.json_calls, 0)

        class FailingSession:
            def post(self, _url, **_kwargs):
                raise RuntimeError(sensitive_marker)

        with self.assertRaises(OfferSearchError) as transport_error:
            asyncio.run(
                search_offers(
                    "synthetic-value",
                    max_price_per_hour=0.75,
                    min_vram_gb=24,
                    session=FailingSession(),
                )
            )
        self.assertEqual(
            str(transport_error.exception),
            "Vast offer search is temporarily unavailable.",
        )
        self.assertNotIn(sensitive_marker, str(transport_error.exception))

    def test_host_session_uses_bounded_timeout(self):
        from cloud_run.vast import OFFER_TIMEOUT_SECONDS, search_offers

        captured = {}

        class FakeTimeout:
            def __init__(self, total):
                self.total = total
                captured["timeout"] = self

        class ManagedSession(FakeSession):
            def __init__(self, timeout):
                super().__init__(FakeResponse(200, {"offers": []}))
                captured["session_timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback):
                captured["closed"] = True
                return False

        fake_aiohttp = types.ModuleType("aiohttp")
        fake_aiohttp.ClientTimeout = FakeTimeout
        fake_aiohttp.ClientSession = ManagedSession
        prior_aiohttp = sys.modules.get("aiohttp")
        sys.modules["aiohttp"] = fake_aiohttp
        try:
            offers = asyncio.run(
                search_offers(
                    "synthetic-value",
                    max_price_per_hour=0.75,
                    min_vram_gb=24,
                )
            )
        finally:
            if prior_aiohttp is None:
                sys.modules.pop("aiohttp", None)
            else:
                sys.modules["aiohttp"] = prior_aiohttp

        self.assertEqual(offers, [])
        self.assertEqual(OFFER_TIMEOUT_SECONDS, 30)
        self.assertEqual(captured["timeout"].total, 30)
        self.assertIs(captured["session_timeout"], captured["timeout"])
        self.assertTrue(captured["closed"])


if __name__ == "__main__":
    unittest.main()
