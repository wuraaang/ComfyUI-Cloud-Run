import asyncio
import json
import logging
import math
import ssl
import sys
import types
import unittest


def worker_release():
    from cloud_run.worker_release import WorkerRelease

    return WorkerRelease.from_payload(
        {
            "schema_version": 1,
            "template_hash_id": "1" * 32,
            "worker_commit": "a" * 40,
            "worker_archive_sha256": "b" * 64,
            "protocol_version": "2",
            "comfyui_core_version": "0.29.0",
            "comfyui_frontend_version": "1.47.10",
            "python_version": "3.12",
            "worker_port": 8765,
        }
    )


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
            [
                FakeResponse(
                    200,
                    {
                        "offers": [
                            {
                                "id": 42,
                                "gpu_name": "RTX 4090",
                                "gpu_ram": 24576,
                                "dph_total": 0.42,
                                "reliability2": 0.99,
                                "machine_id": 77,
                                "host_id": 123,
                                "public_ipaddr": "192.0.2.10",
                                "inet_down": 1200.0,
                                "disk_bw": 640.0,
                                "inet_down_cost": 0.01,
                                "inet_up_cost": 0.02,
                                "dlperf": 72.5,
                                "disk_space": 96,
                                "gpu_arch": "nvidia",
                                "cpu_arch": "amd64",
                                "cuda_max_good": 12.9,
                                "compute_cap": 750,
                                "num_gpus": 1,
                                "rentable": True,
                                "verified": True,
                            }
                        ]
                    },
                ),
                FakeResponse(200, {"offers": []}),
            ]
        )

        offers = asyncio.run(
            search_offers(
                "synthetic-value",
                max_price_per_hour=0.75,
                min_vram_gb=24,
                disk_gb=96,
                session=session,
            )
        )

        self.assertEqual(
            OFFER_SEARCH_URL,
            "https://console.vast.ai/api/v0/bundles/",
        )
        self.assertEqual(len(session.calls), 2)
        for url, request in session.calls:
            self.assertEqual(url, OFFER_SEARCH_URL)
            self.assertEqual(
                request["headers"],
                {
                    "Authorization": "Bearer synthetic-value",
                    "Accept": "application/json",
                },
            )
        common = {
                "gpu_ram": {"gte": 24 * 1024},
                "reliability": {"gte": 0.99},
                "rentable": {"eq": True},
                "dph_total": {"lte": 0.75},
                "disk_space": {"gte": 96},
                "allocated_storage": 96,
                "gpu_arch": {"eq": "nvidia"},
                "cpu_arch": {"eq": "amd64"},
                "cuda_max_good": {"gte": 12.9},
                "compute_cap": {"gte": 750},
                "num_gpus": {"eq": 1},
                "verified": {"eq": True},
                "type": "ondemand",
                "limit": 20,
        }
        self.assertEqual(
            session.calls[0][1]["json"],
            {
                **common,
                "inet_down": {"gte": 1000},
                "order": [
                    ["dlperf", "desc"],
                    ["reliability", "desc"],
                    ["disk_bw", "desc"],
                    ["dph_total", "asc"],
                    ["id", "asc"],
                ],
            },
        )
        self.assertEqual(
            session.calls[1][1]["json"],
            {
                **common,
                "inet_down": {"gte": 500, "lt": 1000},
                "order": [
                    ["dlperf", "desc"],
                    ["inet_down", "desc"],
                    ["reliability", "desc"],
                    ["disk_bw", "desc"],
                    ["dph_total", "asc"],
                    ["id", "asc"],
                ],
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
                    "reliability": 0.99,
                    "machine_id": "77",
                    "host_id": "123",
                    "public_ipaddr": "192.0.2.10",
                    "inet_down_mbps": 1200.0,
                    "disk_bw_mbps": 640.0,
                    "inet_down_cost": 0.01,
                    "inet_up_cost": 0.02,
                    "dlperf": 72.5,
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
            "inet_down": 500,
            "disk_bw": 400,
            "disk_space": 96,
            "gpu_arch": "nvidia",
            "cpu_arch": "amd64",
            "cuda_max_good": 12.9,
            "compute_cap": 750,
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
                disk_gb=96,
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
        self.assertEqual(request["json"]["disk_space"], {"gte": 96})
        self.assertEqual(request["json"]["allocated_storage"], 96)
        self.assertEqual(request["json"]["reliability"], {"gte": 0.99})
        self.assertEqual(request["json"]["inet_down"], {"gte": 500})
        self.assertEqual(request["json"]["gpu_arch"], {"eq": "nvidia"})
        self.assertEqual(request["json"]["cpu_arch"], {"eq": "amd64"})
        self.assertEqual(request["json"]["cuda_max_good"], {"gte": 12.9})
        self.assertEqual(request["json"]["compute_cap"], {"gte": 750})

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
    def test_dlperf_is_optional_finite_nonnegative_provider_evidence(self):
        from cloud_run.vast import normalize_offers

        base = {
            "id": 10,
            "gpu_name": "RTX 5090",
            "gpu_ram": 32768,
            "dph_total": 0.25,
            "reliability": 0.99,
            "inet_down": 1000,
            "disk_bw": 400,
            "disk_space": 80,
            "gpu_arch": "nvidia",
            "cpu_arch": "amd64",
            "cuda_max_good": 12.9,
            "compute_cap": 750,
            "type": "ondemand",
            "num_gpus": 1,
            "rentable": True,
            "verified": True,
        }
        arguments = {
            "max_price_per_hour": 0.75,
            "min_vram_gb": 24,
        }

        for raw_value, expected in (
            (72.5, 72.5),
            (0, 0.0),
            (None, None),
            (-1, None),
            (True, None),
            (math.nan, None),
            (math.inf, None),
            ("72.5", None),
        ):
            with self.subTest(raw_value=raw_value):
                offer = normalize_offers(
                    {"offers": [{**base, "dlperf": raw_value}]},
                    **arguments,
                )[0]
                self.assertEqual(offer["dlperf"], expected)

    def test_missing_response_type_requires_trusted_ondemand_request(self):
        from cloud_run.vast import normalize_offers

        raw = {
            "id": 10,
            "gpu_name": "RTX 5090",
            "gpu_ram": 32768,
            "dph_total": 0.25,
            "reliability": 0.99,
            "inet_down": 500,
            "disk_bw": 400,
            "disk_space": 80,
            "gpu_arch": "nvidia",
            "cpu_arch": "amd64",
            "cuda_max_good": 12.9,
            "compute_cap": 750,
            "num_gpus": 1,
            "rentable": True,
            "verification": "verified",
        }
        arguments = {
            "max_price_per_hour": 0.75,
            "min_vram_gb": 24,
        }

        self.assertEqual(normalize_offers({"offers": [raw]}, **arguments), [])
        accepted = normalize_offers(
            {"offers": [raw]},
            requested_rental_type="ondemand",
            **arguments,
        )
        self.assertEqual([offer["offer_id"] for offer in accepted], [10])
        self.assertEqual(
            normalize_offers(
                {"offers": [{**raw, "type": "bid"}]},
                requested_rental_type="ondemand",
                **arguments,
            ),
            [],
        )
        self.assertEqual(
            normalize_offers(
                {"offers": [{**raw, "type": None}]},
                requested_rental_type="ondemand",
                **arguments,
            ),
            [],
        )

    def test_normalization_requires_complete_quality_and_provider_evidence(self):
        from cloud_run.vast import normalize_offers

        base = {
            "id": 10,
            "gpu_name": "RTX 5090",
            "gpu_ram": 32768,
            "dph_total": 0.25,
            "reliability2": 0.99,
            "machine_id": 77,
            "inet_down": 500,
            "disk_bw": 400,
            "inet_down_cost": -1,
            "inet_up_cost": math.inf,
            "disk_space": 80,
            "gpu_arch": "nvidia",
            "cpu_arch": "amd64",
            "cuda_max_good": 12.9,
            "compute_cap": 750,
            "type": "ondemand",
            "num_gpus": 1,
            "rentable": True,
            "verified": True,
        }
        invalid_variants = {
            "missing inet_down": {"inet_down": None},
            "non-finite inet_down": {"inet_down": math.inf},
            "below bandwidth floor": {"inet_down": 499},
            "below reliability floor": {"reliability2": 0.989},
            "missing type": {"type": None},
            "missing GPU count": {"num_gpus": None},
            "missing rentable": {"rentable": None},
            "missing verification": {"verified": None},
            "missing disk space": {"disk_space": None},
        }
        for name, changes in invalid_variants.items():
            with self.subTest(case=name):
                raw = {**base, **changes}
                self.assertEqual(
                    normalize_offers(
                        {"offers": [raw]},
                        max_price_per_hour=0.75,
                        min_vram_gb=24,
                    ),
                    [],
                )

        accepted = normalize_offers(
            {"offers": [base]},
            max_price_per_hour=0.75,
            min_vram_gb=24,
        )
        self.assertEqual([offer["offer_id"] for offer in accepted], [10])

    def test_normalization_rejects_invalid_hardware_contract_evidence(self):
        from cloud_run.vast import normalize_offers

        base = {
            "id": 11,
            "gpu_name": "RTX 5090",
            "gpu_ram": 32768,
            "dph_total": 0.25,
            "reliability": 0.99,
            "inet_down": 500,
            "disk_bw": 400,
            "disk_space": 80,
            "gpu_arch": "nvidia",
            "cpu_arch": "amd64",
            "cuda_max_good": 12.9,
            "compute_cap": 750,
            "type": "ondemand",
            "num_gpus": 1,
            "rentable": True,
            "verified": True,
        }
        invalid_variants = {
            "missing GPU architecture": {"gpu_arch": None},
            "wrong GPU architecture type": {"gpu_arch": 1},
            "wrong GPU architecture": {"gpu_arch": "amd"},
            "missing CPU architecture": {"cpu_arch": None},
            "wrong CPU architecture type": {"cpu_arch": 1},
            "wrong CPU architecture": {"cpu_arch": "arm64"},
            "missing CUDA maximum": {"cuda_max_good": None},
            "wrong CUDA maximum type": {"cuda_max_good": "12.9"},
            "non-finite CUDA maximum": {"cuda_max_good": math.inf},
            "below CUDA maximum floor": {"cuda_max_good": 12.8},
            "missing compute capability": {"compute_cap": None},
            "wrong compute capability type": {"compute_cap": "750"},
            "non-finite compute capability": {"compute_cap": math.nan},
            "below compute capability floor": {"compute_cap": 749},
        }
        for name, changes in invalid_variants.items():
            with self.subTest(case=name):
                self.assertEqual(
                    normalize_offers(
                        {"offers": [{**base, **changes}]},
                        max_price_per_hour=0.75,
                        min_vram_gb=24,
                    ),
                    [],
                )

        self.assertEqual(
            [
                offer["offer_id"]
                for offer in normalize_offers(
                    {"offers": [base]},
                    max_price_per_hour=0.75,
                    min_vram_gb=24,
                )
            ],
            [11],
        )

    def test_documented_verification_string_is_accepted_without_conflicts(self):
        from cloud_run.vast import normalize_offers

        base = {
            "id": 10,
            "gpu_name": "RTX 5090",
            "gpu_ram": 32768,
            "dph_total": 0.25,
            "reliability": 0.99,
            "inet_down": 500,
            "disk_bw": 400,
            "disk_space": 80,
            "gpu_arch": "nvidia",
            "cpu_arch": "amd64",
            "cuda_max_good": 12.9,
            "compute_cap": 750,
            "type": "ondemand",
            "num_gpus": 1,
            "rentable": True,
            "verification": "verified",
        }
        accepted = normalize_offers(
            {"offers": [base]},
            max_price_per_hour=0.75,
            min_vram_gb=24,
        )
        self.assertEqual([offer["offer_id"] for offer in accepted], [10])
        self.assertEqual(
            normalize_offers(
                {"offers": [{**base, "verified": False}]},
                max_price_per_hour=0.75,
                min_vram_gb=24,
            ),
            [],
        )

    def test_search_merges_disjoint_results_by_id_and_caps_unique_candidates(self):
        from cloud_run.vast import OFFER_SEARCH_LIMIT, search_offers

        def raw(offer_id, speed):
            return {
                "id": offer_id,
                "gpu_name": "RTX 4090",
                "gpu_ram": 24576,
                "dph_total": 0.5,
                "reliability": 0.99,
                "inet_down": speed,
                "disk_bw": 600,
                "disk_space": 80,
                "gpu_arch": "nvidia",
                "cpu_arch": "amd64",
                "cuda_max_good": 12.9,
                "compute_cap": 750,
                "type": "ondemand",
                "num_gpus": 1,
                "rentable": True,
                "verified": True,
            }

        target = [raw(index, 1000 + index) for index in range(OFFER_SEARCH_LIMIT)]
        fallback = [raw(0, 999)] + [
            raw(index, 999 - index)
            for index in range(OFFER_SEARCH_LIMIT, 2 * OFFER_SEARCH_LIMIT + 5)
        ]
        session = FakeSession(
            [
                FakeResponse(200, {"offers": target}),
                FakeResponse(200, {"offers": fallback}),
            ]
        )
        offers = asyncio.run(
            search_offers(
                "synthetic-value",
                max_price_per_hour=0.75,
                min_vram_gb=24,
                session=session,
            )
        )
        self.assertEqual(len(offers), 2 * OFFER_SEARCH_LIMIT - 1)
        self.assertLessEqual(len(offers), 2 * OFFER_SEARCH_LIMIT)
        self.assertEqual(len({offer["offer_id"] for offer in offers}), len(offers))
        self.assertEqual(sum(offer["offer_id"] == 0 for offer in offers), 1)

    def test_explicit_floors_cannot_weaken_policy_and_stricter_values_shape_queries(self):
        from cloud_run.vast import OfferSearchConfigurationError, search_offers

        for kwargs in (
            {"min_reliability": 0.989},
            {"min_inet_down_mbps": 499},
        ):
            with self.subTest(kwargs=kwargs):
                session = FakeSession(FakeResponse(200, {"offers": []}))
                with self.assertRaises(OfferSearchConfigurationError):
                    asyncio.run(
                        search_offers(
                            "synthetic-value",
                            max_price_per_hour=0.75,
                            min_vram_gb=24,
                            session=session,
                            **kwargs,
                        )
                    )
                self.assertEqual(session.calls, [])

        session = FakeSession(
            [FakeResponse(200, {"offers": []}), FakeResponse(200, {"offers": []})]
        )
        asyncio.run(
            search_offers(
                "synthetic-value",
                max_price_per_hour=0.75,
                min_vram_gb=24,
                min_reliability=0.995,
                min_inet_down_mbps=750,
                session=session,
            )
        )
        self.assertEqual(session.calls[0][1]["json"]["inet_down"], {"gte": 1000})
        self.assertEqual(
            session.calls[1][1]["json"]["inet_down"],
            {"gte": 750, "lt": 1000},
        )
        self.assertEqual(session.calls[0][1]["json"]["reliability"], {"gte": 0.995})

        target_only = FakeSession(FakeResponse(200, {"offers": []}))
        asyncio.run(
            search_offers(
                "synthetic-value",
                max_price_per_hour=0.75,
                min_vram_gb=24,
                min_inet_down_mbps=1200,
                session=target_only,
            )
        )
        self.assertEqual(len(target_only.calls), 1)
        self.assertEqual(
            target_only.calls[0][1]["json"]["inet_down"],
            {"gte": 1200},
        )

    def test_normalization_rejects_explicitly_insufficient_disk_space(self):
        from cloud_run.vast import normalize_offers

        payload = {
            "offers": [
                {
                    "id": 42,
                    "gpu_name": "RTX 3090",
                    "gpu_ram": 24576,
                    "dph_total": 0.2,
                    "reliability2": 0.99,
                    "disk_space": 79.9,
                    "gpu_arch": "nvidia",
                    "cpu_arch": "amd64",
                    "cuda_max_good": 12.9,
                    "compute_cap": 750,
                    "inet_down": 500,
                    "disk_bw": 400,
                    "rentable": True,
                    "verified": True,
                    "num_gpus": 1,
                    "type": "ondemand",
                }
            ]
        }

        self.assertEqual(
            normalize_offers(
                payload,
                max_price_per_hour=0.75,
                min_vram_gb=24,
            ),
            [],
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
                super().__init__(
                    [
                        FakeResponse(200, {"offers": []}),
                        FakeResponse(200, {"offers": []}),
                    ]
                )
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
    def test_official_template_hash_matches_current_vast_catalog_pin(self):
        from cloud_run.constants import OFFICIAL_TEMPLATE_ID

        self.assertEqual(
            OFFICIAL_TEMPLATE_ID,
            "027fba7753c024be019030fb42aed900",
        )

    def test_create_uses_only_the_injected_reviewed_project_template(self):
        from cloud_run.constants import VAST_CREATE_CONFIGURATION_REVISION
        from cloud_run.vast import VAST_API_V0, create_instance

        session = FakeSession(
            FakeResponse(200, {"success": True, "new_contract": 987})
        )

        instance_id = asyncio.run(
            create_instance(
                "synthetic-value",
                offer_id=42,
                disk_gb=96,
                label="comfy-cloud-run-session-1",
                release=worker_release(),
                boundary_token="a" * 64,
                session_id="session-1",
                session=session,
            )
        )

        self.assertEqual(instance_id, "987")
        self.assertEqual(
            VAST_CREATE_CONFIGURATION_REVISION,
            "typed-env-object-v1",
        )
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
                        "template_hash_id": "1" * 32,
                        "label": "comfy-cloud-run-session-1",
                        "disk": 96,
                        # Vast's generated endpoint reference disagrees, but
                        # the workflow guide and official client revision
                        # bc735648 use this exact object contract.
                        "env": {
                            "CLOUD_RUN_BOUNDARY_TOKEN": "a" * 64,
                            "CLOUD_RUN_SESSION_ID": "session-1",
                        },
                    },
                },
            )
        ])

    def test_worker_environment_has_exactly_two_validated_keys(self):
        from cloud_run.vast import _worker_environment

        environment = _worker_environment("a" * 64, "session-1")

        self.assertEqual(
            environment,
            {
                "CLOUD_RUN_BOUNDARY_TOKEN": "a" * 64,
                "CLOUD_RUN_SESSION_ID": "session-1",
            },
        )
        self.assertEqual(len(environment), 2)

    def test_create_rejects_missing_release_before_request(self):
        from cloud_run.vast import VastConfigurationError, create_instance

        session = FakeSession(FakeResponse(200, {}))
        with self.assertRaises(VastConfigurationError):
            asyncio.run(
                create_instance(
                    "synthetic-value",
                    offer_id=42,
                    disk_gb=80,
                    label="comfy-cloud-run-attempt-1",
                    release=None,
                    boundary_token="a" * 64,
                    session_id="attempt-1",
                    session=session,
                )
            )
        self.assertEqual(session.calls, [])

    def test_create_rejects_invalid_worker_boundary_before_request(self):
        from cloud_run.vast import VastConfigurationError, create_instance

        cases = (
            ("A" * 64, "session-1"),
            ("a" * 63, "session-1"),
            ("a" * 64, "session/1"),
            ("a" * 64, ""),
        )
        for boundary_token, session_id in cases:
            with self.subTest(
                boundary_token=boundary_token,
                session_id=session_id,
            ):
                session = FakeSession(FakeResponse(200, {}))
                with self.assertRaises(VastConfigurationError):
                    asyncio.run(
                        create_instance(
                            "synthetic-value",
                            offer_id=42,
                            disk_gb=80,
                            label="comfy-cloud-run-attempt-1",
                            release=worker_release(),
                            boundary_token=boundary_token,
                            session_id=session_id,
                            session=session,
                        )
                    )
                self.assertEqual(session.calls, [])

    def test_create_rejects_non_integer_or_out_of_range_disk_before_request(self):
        from cloud_run.vast import VastConfigurationError, create_instance

        for disk_gb in (True, 79, 96.0, 2049):
            with self.subTest(disk_gb=disk_gb):
                session = FakeSession(FakeResponse(200, {}))
                with self.assertRaises(VastConfigurationError):
                    asyncio.run(
                        create_instance(
                            "synthetic-value",
                            offer_id=42,
                            disk_gb=disk_gb,
                            label="comfy-cloud-run-session-1",
                            release=worker_release(),
                            boundary_token="a" * 64,
                            session_id="session-1",
                            session=session,
                        )
                    )
                self.assertEqual(session.calls, [])

    def test_create_rejects_every_invalid_local_field_before_request(self):
        from cloud_run.vast import VastConfigurationError, create_instance

        valid = {
            "api_key": "synthetic-value",
            "offer_id": 42,
            "disk_gb": 80,
            "label": "comfy-cloud-run-attempt-1",
            "release": worker_release(),
            "boundary_token": "a" * 64,
            "session_id": "attempt-1",
        }
        cases = (
            {"api_key": " "},
            {"offer_id": "offer/42"},
            {"offer_id": " 42 "},
            {"offer_id": "1" * 161},
            {"disk_gb": 79},
            {"label": "foreign-label"},
            {"label": " comfy-cloud-run-attempt-1"},
            {"label": "comfy-cloud-run-attempt-1 "},
            {"release": None},
            {"boundary_token": "a" * 63},
            {"session_id": "attempt/1"},
        )
        for changes in cases:
            with self.subTest(changes=tuple(changes)):
                session = FakeSession(FakeResponse(200, {}))
                with self.assertRaises(VastConfigurationError):
                    asyncio.run(
                        create_instance(
                            **{**valid, **changes},
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
            "jupyter_token": "f" * 64,
        }
        list_session = FakeSession(
            FakeResponse(
                200,
                {
                    "success": True,
                    "instances": [raw_instance],
                    "instances_found": 1,
                    "total_instances": 1,
                    "next_token": None,
                },
            )
        )
        listed = asyncio.run(list_instances("synthetic-value", session=list_session))
        self.assertEqual(list_session.methods, ["GET"])
        self.assertEqual(list_session.calls[0][0], VAST_API_V1 + "/instances/")
        self.assertEqual(list_session.calls[0][1]["params"], {"limit": 25})
        self.assertEqual(listed[0]["instance_id"], "987")
        self.assertNotIn("jupyter_token", listed[0])

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
                    release=worker_release(),
                    boundary_token="a" * 64,
                    session_id="attempt-1",
                    session=FakeSession(response),
                )
            )
        self.assertNotIn(marker, str(raised.exception))

    def test_create_maps_provider_status_without_echoing_response_details(self):
        from cloud_run.vast import VastError, create_instance

        marker = "provider-secret-marker"
        cases = (
            (
                400,
                "configuration_rejected",
                False,
                "Vast rejected the instance configuration.",
            ),
            (
                401,
                "api_key_rejected",
                False,
                "Vast API key cannot create instances.",
            ),
            (
                403,
                "api_key_rejected",
                False,
                "Vast API key cannot create instances.",
            ),
            (
                404,
                "offer_unavailable",
                False,
                "The selected Vast offer or template is no longer available.",
            ),
            (
                410,
                "offer_unavailable",
                False,
                "The selected Vast offer or template is no longer available.",
            ),
            (408, "retryable_http", True, "Vast instance creation failed."),
            (409, "retryable_http", True, "Vast instance creation failed."),
            (429, "rate_limited", True, "Vast instance creation failed."),
            (500, "retryable_http", True, "Vast instance creation failed."),
        )
        for status, code, retryable, expected in cases:
            with self.subTest(status=status):
                response = FakeResponse(status, {"error": marker})
                with self.assertRaises(VastError) as raised:
                    asyncio.run(
                        create_instance(
                            "synthetic-value",
                            offer_id=42,
                            disk_gb=80,
                            label="comfy-cloud-run-attempt-1",
                            release=worker_release(),
                            boundary_token="a" * 64,
                            session_id="attempt-1",
                            session=FakeSession(response),
                        )
                    )
                self.assertEqual(str(raised.exception), expected)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.status, status)
                self.assertIs(raised.exception.retryable, retryable)
                self.assertNotIn(marker, str(raised.exception))
                self.assertNotIn(marker, repr(raised.exception))
                self.assertEqual(response.json_calls, 0)

    def test_create_transport_failures_have_stable_safe_codes(self):
        from cloud_run.vast import VastError, create_instance

        marker = "provider-transport-secret"
        cases = (
            (TimeoutError(marker), "timeout"),
            (ssl.SSLError(marker), "tls"),
            (ConnectionResetError(marker), "server_disconnected"),
            (ConnectionRefusedError(marker), "connection"),
            (RuntimeError(marker), "transport_unknown"),
        )

        class FailingSession:
            def __init__(self, error):
                self.error = error
                self.calls = 0

            def put(self, _url, **_kwargs):
                self.calls += 1
                raise self.error

        for error, expected_code in cases:
            with self.subTest(code=expected_code):
                session = FailingSession(error)
                with self.assertRaises(VastError) as raised:
                    asyncio.run(
                        create_instance(
                            "synthetic-value",
                            offer_id=42,
                            disk_gb=80,
                            label="comfy-cloud-run-attempt-1",
                            release=worker_release(),
                            boundary_token="a" * 64,
                            session_id="attempt-1",
                            session=session,
                        )
                    )
                self.assertEqual(session.calls, 1)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertIsNone(raised.exception.status)
                self.assertTrue(raised.exception.retryable)
                self.assertNotIn(marker, str(raised.exception))
                self.assertNotIn(marker, repr(raised.exception))

        for interruption in (asyncio.CancelledError(), KeyboardInterrupt()):
            with self.subTest(interruption=type(interruption).__name__):
                with self.assertRaises(type(interruption)):
                    asyncio.run(
                        create_instance(
                            "synthetic-value",
                            offer_id=42,
                            disk_gb=80,
                            label="comfy-cloud-run-attempt-1",
                            release=worker_release(),
                            boundary_token="a" * 64,
                            session_id="attempt-1",
                            session=FailingSession(interruption),
                        )
                    )

    def test_aiohttp_tls_subclasses_are_not_downgraded_to_connection(self):
        from cloud_run.vast import VastError, create_instance

        marker = "provider-aiohttp-tls-secret"

        class FakeClientConnectionError(Exception):
            pass

        class FakeClientSSLError(FakeClientConnectionError):
            pass

        class FakeServerFingerprintMismatch(FakeClientConnectionError):
            pass

        class FailingSession:
            def __init__(self, error):
                self.error = error

            def put(self, _url, **_kwargs):
                raise self.error

        fake_aiohttp = types.ModuleType("aiohttp")
        fake_aiohttp.ClientSSLError = FakeClientSSLError
        fake_aiohttp.ServerFingerprintMismatch = (
            FakeServerFingerprintMismatch
        )
        fake_aiohttp.ClientConnectionError = FakeClientConnectionError
        prior_aiohttp = sys.modules.get("aiohttp")
        sys.modules["aiohttp"] = fake_aiohttp
        try:
            for error in (
                FakeClientSSLError(marker),
                FakeServerFingerprintMismatch(marker),
            ):
                with self.subTest(error=type(error).__name__):
                    with self.assertRaises(VastError) as raised:
                        asyncio.run(
                            create_instance(
                                "synthetic-value",
                                offer_id=42,
                                disk_gb=80,
                                label="comfy-cloud-run-attempt-1",
                                release=worker_release(),
                                boundary_token="a" * 64,
                                session_id="attempt-1",
                                session=FailingSession(error),
                            )
                        )
                    self.assertEqual(raised.exception.code, "tls")
                    self.assertNotIn(marker, str(raised.exception))
                    self.assertNotIn(marker, repr(raised.exception))
        finally:
            if prior_aiohttp is None:
                sys.modules.pop("aiohttp", None)
            else:
                sys.modules["aiohttp"] = prior_aiohttp

    def test_create_managed_session_failure_is_sanitized(self):
        from cloud_run.vast import VastError, create_instance

        marker = "managed-session-private-marker"

        class FakeTimeout:
            def __init__(self, total):
                self.total = total

        class FailingManagedSession:
            failure = RuntimeError(marker)

            def __init__(self, timeout):
                self.timeout = timeout

            async def __aenter__(self):
                raise self.failure

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return False

        fake_aiohttp = types.ModuleType("aiohttp")
        fake_aiohttp.ClientTimeout = FakeTimeout
        fake_aiohttp.ClientSession = FailingManagedSession
        prior_aiohttp = sys.modules.get("aiohttp")
        sys.modules["aiohttp"] = fake_aiohttp
        try:
            for failure, expected_code in (
                (RuntimeError(marker), "transport_unknown"),
                (TimeoutError(marker), "timeout"),
            ):
                with self.subTest(code=expected_code):
                    FailingManagedSession.failure = failure
                    with self.assertRaises(VastError) as raised:
                        asyncio.run(
                            create_instance(
                                "synthetic-value",
                                offer_id=42,
                                disk_gb=80,
                                label="comfy-cloud-run-attempt-1",
                                release=worker_release(),
                                boundary_token="a" * 64,
                                session_id="attempt-1",
                            )
                        )
                    self.assertEqual(raised.exception.code, expected_code)
                    self.assertTrue(raised.exception.retryable)
                    self.assertNotIn(marker, str(raised.exception))
                    self.assertNotIn(marker, repr(raised.exception))
        finally:
            if prior_aiohttp is None:
                sys.modules.pop("aiohttp", None)
            else:
                sys.modules["aiohttp"] = prior_aiohttp

    def test_invalid_success_response_is_ambiguous_and_sanitized(self):
        from cloud_run.vast import VastError, create_instance

        marker = "provider-invalid-success-secret"
        invalid_payloads = (
            ValueError(marker),
            [marker],
            {"success": False, "new_contract": 987, "error": marker},
            {"success": True, "error": marker},
            {"success": True, "new_contract": "not/an/id", "error": marker},
            {"success": True, "new_contract": " 987 ", "error": marker},
            {"success": True, "new_contract": "1" * 161, "error": marker},
            {"success": True, "new_contract": True, "error": marker},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=type(payload).__name__):
                with self.assertRaises(VastError) as raised:
                    asyncio.run(
                        create_instance(
                            "synthetic-value",
                            offer_id=42,
                            disk_gb=80,
                            label="comfy-cloud-run-attempt-1",
                            release=worker_release(),
                            boundary_token="a" * 64,
                            session_id="attempt-1",
                            session=FakeSession(FakeResponse(200, payload)),
                        )
                    )
                self.assertEqual(
                    str(raised.exception),
                    "Vast instance creation returned an invalid response.",
                )
                self.assertEqual(raised.exception.code, "invalid_response")
                self.assertEqual(raised.exception.status, 200)
                self.assertTrue(raised.exception.retryable)
                self.assertNotIn(marker, str(raised.exception))
                self.assertNotIn(marker, repr(raised.exception))

    def test_create_returns_the_canonical_numeric_contract_id(self):
        from cloud_run.vast import create_instance

        instance_id = asyncio.run(
            create_instance(
                "synthetic-value",
                offer_id="00042",
                disk_gb=80,
                label="comfy-cloud-run-attempt-1",
                release=worker_release(),
                boundary_token="a" * 64,
                session_id="attempt-1",
                session=FakeSession(
                    FakeResponse(
                        200,
                        {"success": True, "new_contract": "000987"},
                    )
                ),
            )
        )

        self.assertEqual(instance_id, "987")

    def test_create_error_repr_never_exposes_provider_or_boundary_markers(self):
        from cloud_run.vast import VastError, create_instance

        provider_marker = "provider-exception-private-marker"
        boundary_token = "d" * 64
        session_id = "private-session-marker"

        class FailingSession:
            def put(self, _url, **_kwargs):
                raise RuntimeError(provider_marker)

        records = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = CaptureHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            with self.assertRaises(VastError) as raised:
                asyncio.run(
                    create_instance(
                        "synthetic-value",
                        offer_id=42,
                        disk_gb=80,
                        label="comfy-cloud-run-attempt-1",
                        release=worker_release(),
                        boundary_token=boundary_token,
                        session_id=session_id,
                        session=FailingSession(),
                    )
                )
        finally:
            root_logger.removeHandler(handler)

        public_surfaces = (
            str(raised.exception),
            repr(raised.exception),
            json.dumps(vars(raised.exception), sort_keys=True),
            "\n".join(record.getMessage() for record in records),
        )
        for surface in public_surfaces:
            self.assertNotIn(provider_marker, surface)
            self.assertNotIn(boundary_token, surface)
            self.assertNotIn(session_id, surface)

    @staticmethod
    def _inventory_instance(instance_id, **changes):
        return {
            "id": instance_id,
            "actual_status": "running",
            "label": "comfy-cloud-run-attempt-" + str(instance_id),
            **changes,
        }

    @staticmethod
    def _inventory_page(instances, *, total, next_token=None, **changes):
        return {
            "success": True,
            "instances": instances,
            "instances_found": len(instances),
            "total_instances": total,
            "next_token": next_token,
            **changes,
        }

    def test_list_instances_follows_every_next_token_before_returning(self):
        from cloud_run.vast import VAST_API_V1, list_instances

        first = self._inventory_instance(101)
        second = self._inventory_instance(202)
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page([first], total=2, next_token="page-2"),
                ),
                FakeResponse(
                    200,
                    self._inventory_page([second], total=2),
                ),
            ]
        )

        instances = asyncio.run(
            list_instances("synthetic-value", session=session)
        )

        self.assertEqual(
            [instance["instance_id"] for instance in instances],
            ["101", "202"],
        )
        self.assertEqual(
            session.calls,
            [
                (
                    VAST_API_V1 + "/instances/",
                    {
                        "headers": {
                            "Authorization": "Bearer synthetic-value",
                            "Accept": "application/json",
                        },
                        "params": {"limit": 25},
                    },
                ),
                (
                    VAST_API_V1 + "/instances/",
                    {
                        "headers": {
                            "Authorization": "Bearer synthetic-value",
                            "Accept": "application/json",
                        },
                        "params": {"limit": 25, "after_token": "page-2"},
                    },
                ),
            ],
        )

    def test_list_instances_rejects_repeated_or_malformed_pagination(self):
        from cloud_run.vast import VastError, list_instances

        repeated = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(101)],
                        total=3,
                        next_token="repeat-token",
                    ),
                ),
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(202)],
                        total=3,
                        next_token="repeat-token",
                    ),
                ),
            ]
        )
        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=repeated))
        self.assertEqual(len(repeated.calls), 2)

        for token in (True, 7, "", " padded-token "):
            with self.subTest(token=token):
                malformed = FakeSession(
                    FakeResponse(
                        200,
                        self._inventory_page(
                            [],
                            total=1,
                            next_token=token,
                        ),
                    )
                )
                with self.assertRaises(VastError):
                    asyncio.run(
                        list_instances("synthetic-value", session=malformed)
                    )
                self.assertEqual(len(malformed.calls), 1)

    def test_list_instances_rejects_oversized_or_empty_nonterminal_page(self):
        from cloud_run.vast import VastError, list_instances

        oversized = FakeSession(
            FakeResponse(
                200,
                self._inventory_page(
                    [self._inventory_instance(index) for index in range(26)],
                    total=26,
                ),
            )
        )
        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=oversized))

        empty_nonterminal = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page([], total=1, next_token="page-2"),
                ),
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(101)],
                        total=1,
                    ),
                ),
            ]
        )
        with self.assertRaises(VastError):
            asyncio.run(
                list_instances(
                    "synthetic-value",
                    session=empty_nonterminal,
                )
            )
        self.assertEqual(len(empty_nonterminal.calls), 1)

    def test_list_instances_never_treats_429_or_partial_pages_as_empty(self):
        from cloud_run.vast import VastError, list_instances

        rate_limited = FakeSession(FakeResponse(429, {"error": "private"}))
        with self.assertRaises(VastError) as raised:
            asyncio.run(
                list_instances("synthetic-value", session=rate_limited)
            )
        self.assertEqual(raised.exception.status, 429)
        self.assertTrue(raised.exception.retryable)

        partial = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(101)],
                        total=2,
                        next_token="page-2",
                    ),
                ),
                FakeResponse(429, {"error": "private"}),
            ]
        )
        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=partial))
        self.assertEqual(len(partial.calls), 2)

    def test_list_instances_rejects_any_invalid_instance_row(self):
        from cloud_run.vast import VastError, list_instances

        invalid_rows = (
            None,
            [],
            {},
            {"id": True},
            {"id": "not/an/id"},
            {"id": " 101 "},
            {"id": "1" * 161},
        )
        for row in invalid_rows:
            with self.subTest(row=row):
                session = FakeSession(
                    FakeResponse(
                        200,
                        self._inventory_page([row], total=1),
                    )
                )
                with self.assertRaises(VastError):
                    asyncio.run(
                        list_instances("synthetic-value", session=session)
                    )

    def test_list_instances_requires_success_and_exact_page_counts(self):
        from cloud_run.vast import VastError, list_instances

        instance = self._inventory_instance(101)
        invalid_pages = (
            self._inventory_page([instance], total=1, success=False),
            self._inventory_page([instance], total=1, success=1),
            {
                "instances": [instance],
                "instances_found": 1,
                "total_instances": 1,
                "next_token": None,
            },
            self._inventory_page([instance], total=1, instances_found=True),
            self._inventory_page([instance], total=1, instances_found=-1),
            self._inventory_page([instance], total=1, instances_found=0),
            self._inventory_page([instance], total=True),
            self._inventory_page([instance], total=-1),
            self._inventory_page([instance], total="1"),
        )
        for payload in invalid_pages:
            with self.subTest(payload=payload):
                with self.assertRaises(VastError):
                    asyncio.run(
                        list_instances(
                            "synthetic-value",
                            session=FakeSession(FakeResponse(200, payload)),
                        )
                    )

    def test_list_instances_rejects_incoherent_totals_and_conflicting_duplicates(self):
        from cloud_run.vast import VastError, list_instances

        incoherent = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(101)],
                        total=2,
                        next_token="page-2",
                    ),
                ),
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(202)],
                        total=3,
                    ),
                ),
            ]
        )
        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=incoherent))

        conflicting = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(101)],
                        total=2,
                        next_token="page-2",
                    ),
                ),
                FakeResponse(
                    200,
                    self._inventory_page(
                        [
                            self._inventory_instance(
                                101,
                                actual_status="exited",
                            )
                        ],
                        total=2,
                    ),
                ),
            ]
        )
        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=conflicting))

    def test_list_instances_rejects_any_duplicate_id_across_pages(self):
        from cloud_run.vast import VastError, list_instances

        duplicate = self._inventory_instance(101)
        session = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page(
                        [duplicate],
                        total=2,
                        next_token="page-2",
                    ),
                ),
                FakeResponse(
                    200,
                    self._inventory_page([dict(duplicate)], total=2),
                ),
            ]
        )
        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=session))

    def test_list_instances_rejects_duplicate_numeric_id_spellings(self):
        from cloud_run.vast import VastError, list_instances

        session = FakeSession(
            [
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance(101)],
                        total=2,
                        next_token="page-2",
                    ),
                ),
                FakeResponse(
                    200,
                    self._inventory_page(
                        [self._inventory_instance("00101")],
                        total=2,
                    ),
                ),
            ]
        )

        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=session))

    def test_list_instances_rejects_terminal_short_snapshot(self):
        from cloud_run.vast import VastError, list_instances

        terminal_short = FakeSession(
            FakeResponse(
                200,
                self._inventory_page(
                    [self._inventory_instance(101)],
                    total=2,
                ),
            )
        )
        with self.assertRaises(VastError):
            asyncio.run(
                list_instances("synthetic-value", session=terminal_short)
            )

        nonterminal_complete = FakeSession(
            FakeResponse(
                200,
                self._inventory_page(
                    [self._inventory_instance(101)],
                    total=1,
                    next_token="unexpected-more",
                ),
            )
        )
        with self.assertRaises(VastError):
            asyncio.run(
                list_instances("synthetic-value", session=nonterminal_complete)
            )
        self.assertEqual(len(nonterminal_complete.calls), 1)

    def test_list_instances_proves_zero_only_from_one_complete_zero_snapshot(self):
        from cloud_run.vast import VastError, list_instances

        complete = FakeSession(
            FakeResponse(200, self._inventory_page([], total=0))
        )
        self.assertEqual(
            asyncio.run(list_instances("synthetic-value", session=complete)),
            [],
        )
        self.assertEqual(complete.calls[0][1]["params"], {"limit": 25})

        incomplete = FakeSession(
            FakeResponse(
                200,
                self._inventory_page([], total=0, next_token="impossible-more"),
            )
        )
        with self.assertRaises(VastError):
            asyncio.run(list_instances("synthetic-value", session=incomplete))
        self.assertEqual(len(incomplete.calls), 1)


if __name__ == "__main__":
    unittest.main()
