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
        self.responses = list(response) if isinstance(response, list) else [response]
        self.calls = []
        self.methods = []

    def _request(self, method, url, **kwargs):
        self.methods.append(method)
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def delete(self, url, **kwargs):
        return self._request("DELETE", url, **kwargs)

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._request("PUT", url, **kwargs)


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
                            "machine_id": 77,
                            "host_id": 123,
                            "public_ipaddr": "192.0.2.10",
                            "inet_down": 850.0,
                            "disk_bw": 640.0,
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
                    "machine_id": "77",
                    "host_id": "123",
                    "public_ipaddr": "192.0.2.10",
                    "inet_down_mbps": 850.0,
                    "disk_bw_mbps": 640.0,
                }
            ],
        )

    def test_exact_offer_lookup_uses_contract_id_and_requires_canonical_match(self):
        from cloud_run.vast import OFFER_SEARCH_URL, get_offer

        matching_offer = {
            "id": 42,
            "gpu_name": "RTX 4090",
            "gpu_ram": 24576,
            "dph_total": 0.42,
            "reliability2": 0.99,
            "rentable": True,
            "verified": True,
            "num_gpus": 1,
            "type": "ondemand",
        }
        matching_session = FakeSession(
            FakeResponse(200, {"offers": [matching_offer]})
        )

        selected = asyncio.run(
            get_offer(
                "synthetic-value",
                offer_id="42",
                max_price_per_hour=0.75,
                min_vram_gb=24,
                session=matching_session,
            )
        )

        self.assertEqual(selected["offer_id"], 42)
        url, request = matching_session.calls[0]
        self.assertEqual(url, OFFER_SEARCH_URL)
        self.assertEqual(
            request["json"]["ask_contract_id"],
            {"eq": 42},
        )
        self.assertEqual(request["json"]["limit"], 1)
        self.assertEqual(request["json"]["dph_total"], {"lte": 0.75})
        self.assertEqual(request["json"]["gpu_ram"], {"gte": 24576})

        mismatched_session = FakeSession(
            FakeResponse(200, {"offers": [{**matching_offer, "id": 99}]})
        )
        mismatched = asyncio.run(
            get_offer(
                "synthetic-value",
                offer_id=42,
                max_price_per_hour=0.75,
                min_vram_gb=24,
                session=mismatched_session,
            )
        )
        self.assertIsNone(mismatched)

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
                    "inet_down": 500,
                    "disk_bw": 400,
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
                    "machine_id": "77",
                    "host_id": None,
                    "public_ipaddr": None,
                    "inet_down_mbps": 500.0,
                    "disk_bw_mbps": 400.0,
                },
                {
                    "offer_id": 20,
                    "gpu_name": "RTX 4090",
                    "gpu_ram_gb": 24.0,
                    "dph_total": 0.5,
                    "reliability": 0.96,
                    "machine_id": None,
                    "host_id": "88",
                    "public_ipaddr": "192.0.2.1",
                    "inet_down_mbps": None,
                    "disk_bw_mbps": None,
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
                "machine_id",
                "host_id",
                "public_ipaddr",
                "inet_down_mbps",
                "disk_bw_mbps",
            },
        )

    def test_quality_filters_are_forwarded_to_vast(self):
        from cloud_run.vast import build_search_payload

        payload = build_search_payload(
            0.75,
            24,
            min_inet_down_mbps=250,
            min_disk_bw_mbps=300,
            min_reliability=0.98,
            verified_only=False,
            secure_cloud_only=True,
        )

        self.assertNotIn("verified", payload)
        self.assertEqual(payload["datacenter"], {"eq": True})
        self.assertEqual(payload["inet_down"], {"gte": 250})
        self.assertEqual(payload["disk_bw"], {"gte": 300})
        self.assertEqual(payload["reliability"], {"gte": 0.98})

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


class VastLifecycleRequestTests(unittest.TestCase):
    def test_create_uses_only_the_official_comfyui_template(self):
        from cloud_run.constants import OFFICIAL_TEMPLATE_ID
        from cloud_run.vast import VAST_API_V0, create_instance

        session = FakeSession(
            FakeResponse(200, {"success": True, "new_contract": 987})
        )

        instance_id = asyncio.run(
            create_instance(
                "synthetic-value",
                offer_id=42,
                disk_gb=80,
                label="comfy-cloud-run-attempt-1",
                session=session,
            )
        )

        self.assertEqual(instance_id, "987")
        self.assertEqual(session.methods, ["PUT"])
        self.assertEqual(session.calls, [
            (
                VAST_API_V0 + "/asks/42/",
                {
                    "headers": {
                        "Authorization": "Bearer synthetic-value",
                        "Accept": "application/json",
                    },
                    "json": {
                        "template_hash_id": OFFICIAL_TEMPLATE_ID,
                        "label": "comfy-cloud-run-attempt-1",
                        "disk": 80,
                    },
                },
            )
        ])

    def test_create_rejects_an_unapproved_template_before_request(self):
        from cloud_run.vast import VastConfigurationError, create_instance

        session = FakeSession(FakeResponse(200, {}))
        with self.assertRaises(VastConfigurationError):
            asyncio.run(
                create_instance(
                    "synthetic-value",
                    offer_id=42,
                    disk_gb=80,
                    label="comfy-cloud-run-attempt-1",
                    template_id="unapproved-template",
                    session=session,
                )
            )
        self.assertEqual(session.calls, [])

    def test_list_get_destroy_and_url_derivation_are_normalized(self):
        from cloud_run.vast import (
            VAST_API_V0,
            VAST_API_V1,
            derive_base_url,
            destroy_instance,
            get_instance,
            list_instances,
        )

        raw_instance = {
            "id": 987,
            "actual_status": "running",
            "public_ipaddr": "8.8.8.8",
            "ports": {"8188/tcp": [{"HostPort": "32100"}]},
            "label": "comfy-cloud-run-attempt-1",
            "dph_total": 0.42,
            "status_msg": "ready",
            "jupyter_token": "provider-secret",
        }
        list_session = FakeSession(
            FakeResponse(200, {"instances": [raw_instance]})
        )
        listed = asyncio.run(list_instances("synthetic-value", session=list_session))
        self.assertEqual(list_session.methods, ["GET"])
        self.assertEqual(list_session.calls[0][0], VAST_API_V1 + "/instances/")
        self.assertEqual(listed[0]["instance_id"], "987")
        self.assertEqual(listed[0]["jupyter_token"], "provider-secret")

        get_session = FakeSession(
            FakeResponse(200, {"instances": raw_instance})
        )
        fetched = asyncio.run(
            get_instance("synthetic-value", "987", session=get_session)
        )
        self.assertEqual(get_session.calls[0][0], VAST_API_V0 + "/instances/987/")
        self.assertEqual(fetched, listed[0])
        self.assertEqual(derive_base_url(fetched, 8188), "http://8.8.8.8:32100")
        self.assertIsNone(
            derive_base_url(
                {**fetched, "public_ipaddr": "not-an-ip"},
                8188,
            )
        )
        self.assertIsNone(
            derive_base_url(
                {
                    "public_ipaddr": "10.0.0.8",
                    "ports": {"8188/tcp": [{"HostPort": "32100"}]},
                },
                8188,
            )
        )

        for status, expected in ((200, True), (404, True), (500, False)):
            with self.subTest(status=status):
                destroy_session = FakeSession(FakeResponse(status, {}))
                destroyed = asyncio.run(
                    destroy_instance(
                        "synthetic-value",
                        "987",
                        session=destroy_session,
                    )
                )
                self.assertEqual(destroyed, expected)
                self.assertEqual(
                    destroy_session.calls[0][0],
                    VAST_API_V0 + "/instances/987/",
                )
                self.assertEqual(destroy_session.methods, ["DELETE"])

    def test_mutation_errors_are_sanitized(self):
        from cloud_run.vast import VastError, create_instance

        marker = "provider-secret-marker"
        response = FakeResponse(500, {"error": marker})
        with self.assertRaises(VastError) as raised:
            asyncio.run(
                create_instance(
                    "synthetic-value",
                    offer_id=42,
                    disk_gb=80,
                    label="comfy-cloud-run-attempt-1",
                    session=FakeSession(response),
                )
            )
        self.assertNotIn(marker, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
