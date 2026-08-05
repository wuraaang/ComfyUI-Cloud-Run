import importlib.util
import json
import math
import os
import tempfile
import unittest
from pathlib import Path


class OfferPolicyModuleContractTests(unittest.TestCase):
    def test_offer_policy_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec("cloud_run.offers"))


def normalized_offer(
    offer_id,
    *,
    gpu_name="RTX 4090",
    price=0.5,
    reliability=0.99,
    machine_id=None,
    host_id=None,
    public_ipaddr=None,
    inet_down_mbps=1000,
    disk_bw_mbps=600,
    dlperf=None,
    gpu_ram_gb=24.0,
    disk_gb=96,
):
    return {
        "offer_id": offer_id,
        "gpu_name": gpu_name,
        "gpu_ram_gb": gpu_ram_gb,
        "dph_total": price,
        "reliability": reliability,
        "machine_id": machine_id,
        "host_id": host_id,
        "public_ipaddr": public_ipaddr,
        "inet_down_mbps": inet_down_mbps,
        "disk_bw_mbps": disk_bw_mbps,
        "dlperf": dlperf,
        "disk_gb": disk_gb,
    }


class ProviderConstraintTests(unittest.TestCase):
    def test_raw_offers_must_still_satisfy_every_provider_constraint(self):
        from cloud_run.vast import normalize_offers

        base = {
            "id": 1,
            "gpu_name": "RTX 4090",
            "gpu_ram": 24576,
            "dph_total": 0.5,
            "reliability": 0.99,
            "rentable": True,
            "verified": True,
            "num_gpus": 1,
            "type": "ondemand",
            "inet_down": 500,
            "disk_bw": 600,
            "disk_space": 80,
            "gpu_arch": "nvidia",
            "cpu_arch": "amd64",
            "cuda_max_good": 12.9,
            "compute_cap": 750,
        }
        invalid_variants = {
            "VRAM": {"gpu_ram": 16384},
            "price cap": {"dph_total": 0.76},
            "on-demand": {"type": "bid"},
            "rentable": {"rentable": False},
            "verified": {"verified": False},
            "one GPU": {"num_gpus": 2},
            "reliability": {"reliability": 0.94},
            "network": {"inet_down": 499},
            "disk": {"disk_bw": 299},
        }

        for name, changes in invalid_variants.items():
            with self.subTest(constraint=name):
                raw = dict(base)
                raw.update(changes)
                self.assertEqual(
                    normalize_offers(
                        {"offers": [raw]},
                        max_price_per_hour=0.75,
                        min_vram_gb=24,
                        min_inet_down_mbps=500,
                        min_disk_bw_mbps=300,
                    ),
                    [],
                )

        valid = normalize_offers(
            {"offers": [base]},
            max_price_per_hour=0.75,
            min_vram_gb=24,
            min_inet_down_mbps=500,
            min_disk_bw_mbps=300,
        )
        self.assertEqual([offer["offer_id"] for offer in valid], [1])

    def test_missing_provider_gate_evidence_is_rejected(self):
        from cloud_run.vast import normalize_offers

        raw = {
            "id": 1,
            "gpu_name": "RTX 4090",
            "gpu_ram": 24576,
            "dph_total": 0.5,
            "reliability": 0.99,
        }
        self.assertEqual(
            normalize_offers(
                {"offers": [raw]},
                max_price_per_hour=0.75,
                min_vram_gb=24,
            ),
            [],
        )


class HostBlacklistTests(unittest.TestCase):
    def test_machine_host_and_ip_each_match_and_expire(self):
        from cloud_run.offers import HostBlacklist

        identities = (
            ("machine_id", "machine-1"),
            ("host_id", "host-1"),
            ("public_ipaddr", "192.0.2.10"),
        )
        for field, value in identities:
            with self.subTest(identity=field), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "private" / "blacklist.json"
                blacklist = HostBlacklist(path)
                source = normalized_offer(1, **{field: value})
                blacklist.add(
                    source,
                    reason="boot_timeout",
                    ttl_seconds=10,
                    now=100,
                )
                self.assertTrue(
                    blacklist.contains(
                        normalized_offer(2, **{field: value}),
                        now=109,
                    )
                )
                self.assertFalse(
                    blacklist.contains(
                        normalized_offer(3, **{field: value}),
                        now=110,
                    )
                )

    def test_blacklist_is_private_atomic_and_never_persists_raw_errors(self):
        from cloud_run.offers import HostBlacklist

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private" / "blacklist.json"
            blacklist = HostBlacklist(path)
            blacklist.add(
                normalized_offer(1, machine_id="machine-1"),
                reason="a provider secret should never survive",
                now=100,
            )

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"][0]["reason"], "provider_failure")
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))

    def test_corrupt_blacklist_fails_closed_to_an_empty_deterministic_set(self):
        from cloud_run.offers import HostBlacklist

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "blacklist.json"
            path.write_text("{invalid", encoding="utf-8")
            os.chmod(path, 0o644)
            blacklist = HostBlacklist(path)

            self.assertFalse(
                blacklist.contains(
                    normalized_offer(1, machine_id="machine-1"),
                    now=100,
                )
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_filter_never_silently_reuses_a_blacklisted_identity(self):
        from cloud_run.offers import HostBlacklist, apply_offer_policy

        with tempfile.TemporaryDirectory() as temp:
            blacklist = HostBlacklist(Path(temp) / "blacklist.json")
            offer = normalized_offer(
                1,
                machine_id="machine-1",
                host_id="host-1",
                public_ipaddr="192.0.2.10",
            )
            blacklist.add(offer, reason="boot_timeout", now=100)

            self.assertEqual(
                apply_offer_policy([offer], blacklist=blacklist, now=101),
                [],
            )


class RankingPolicyTests(unittest.TestCase):
    def test_higher_dlperf_beats_lower_dlperf_before_price(self):
        from cloud_run.offers import select_best_offer

        offers = [
            normalized_offer(1, dlperf=40, price=0.40),
            normalized_offer(2, dlperf=80, price=0.50),
        ]

        self.assertEqual(select_best_offer(offers)["offer_id"], 2)

    def test_valid_zero_dlperf_beats_missing_or_invalid_dlperf(self):
        from cloud_run.offers import select_best_offer

        invalid_values = (None, True, -1, math.nan, math.inf, "80")
        for value in invalid_values:
            with self.subTest(value=value):
                offers = [
                    normalized_offer(1, dlperf=value, price=0.40),
                    normalized_offer(2, dlperf=0, price=0.50),
                ]
                self.assertEqual(select_best_offer(offers)["offer_id"], 2)

    def test_equal_dlperf_retains_existing_quality_order(self):
        from cloud_run.offers import select_best_offer

        offers = [
            normalized_offer(
                1,
                dlperf=80,
                reliability=0.99,
                inet_down_mbps=1000,
            ),
            normalized_offer(
                2,
                dlperf=80,
                reliability=1.0,
                inet_down_mbps=1000,
            ),
        ]

        self.assertEqual(select_best_offer(offers)["offer_id"], 2)

    def test_bait_price_is_removed_only_with_a_meaningful_class_median(self):
        from cloud_run.offers import apply_offer_policy

        offers = [
            normalized_offer(1, price=0.1),
            normalized_offer(2, price=0.5),
            normalized_offer(3, price=0.6),
            normalized_offer(4, gpu_name="RTX 5090", price=0.1),
            normalized_offer(5, gpu_name="RTX 5090", price=0.5),
        ]

        kept = apply_offer_policy(offers)

        self.assertEqual(
            [offer["offer_id"] for offer in kept],
            [4, 2, 5, 3],
        )
        self.assertNotIn(
            1,
            [
                offer["offer_id"]
                for offer in kept
                if offer["gpu_name"] == "RTX 4090"
            ],
        )

    def test_target_class_beats_fallback_even_when_fallback_is_better_otherwise(self):
        from cloud_run.offers import select_best_offer

        offers = [
            normalized_offer(
                1,
                price=0.40,
                reliability=1.0,
                inet_down_mbps=999,
                disk_bw_mbps=900,
            ),
            normalized_offer(
                2,
                price=0.50,
                reliability=0.99,
                inet_down_mbps=1000,
                disk_bw_mbps=600,
            ),
        ]

        self.assertEqual(select_best_offer(offers)["offer_id"], 2)

    def test_target_speed_is_saturated_before_price(self):
        from cloud_run.offers import select_best_offer

        offers = [
            normalized_offer(
                1,
                price=0.40,
                reliability=0.99,
                inet_down_mbps=1000,
                disk_bw_mbps=600,
            ),
            normalized_offer(
                2,
                price=0.50,
                reliability=0.99,
                inet_down_mbps=5000,
                disk_bw_mbps=600,
            ),
        ]
        self.assertEqual(select_best_offer(offers)["offer_id"], 1)

    def test_fastest_fallback_wins_when_no_target_exists(self):
        from cloud_run.offers import select_best_offer

        offers = [
            normalized_offer(
                1,
                price=0.40,
                reliability=0.99,
                inet_down_mbps=600,
            ),
            normalized_offer(
                2,
                price=0.50,
                reliability=0.99,
                inet_down_mbps=900,
            ),
        ]
        self.assertEqual(select_best_offer(offers)["offer_id"], 2)

    def test_equal_capped_speed_uses_reliability_disk_price_then_id(self):
        from cloud_run.offers import select_best_offer

        cases = [
            (
                [
                    normalized_offer(
                        1, reliability=0.99, inet_down_mbps=1200
                    ),
                    normalized_offer(
                        2, reliability=1.0, inet_down_mbps=1000
                    ),
                ],
                2,
            ),
            (
                [
                    normalized_offer(
                        1,
                        reliability=0.99,
                        disk_bw_mbps=600,
                        inet_down_mbps=1200,
                    ),
                    normalized_offer(
                        2,
                        reliability=0.99,
                        disk_bw_mbps=700,
                        inet_down_mbps=1000,
                    ),
                ],
                2,
            ),
            (
                [
                    normalized_offer(1, price=0.50, inet_down_mbps=1200),
                    normalized_offer(2, price=0.40, inet_down_mbps=1000),
                ],
                2,
            ),
            (
                [
                    normalized_offer(2, price=0.50, inet_down_mbps=1200),
                    normalized_offer(1, price=0.50, inet_down_mbps=1000),
                ],
                1,
            ),
        ]
        for offers, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(select_best_offer(offers)["offer_id"], expected)

    def test_policy_order_matches_selection_priority(self):
        from cloud_run.offers import apply_offer_policy, select_best_offer

        offers = [
            normalized_offer(3, inet_down_mbps=900, price=0.3),
            normalized_offer(2, inet_down_mbps=1000, price=0.5),
            normalized_offer(1, inet_down_mbps=1200, price=0.4),
        ]

        ordered = apply_offer_policy(offers)
        self.assertEqual(
            [offer["offer_id"] for offer in ordered],
            [1, 2, 3],
        )
        self.assertEqual(ordered[0], select_best_offer(offers))

    def test_theoretical_transfer_estimate_is_conservative_and_inert(self):
        from cloud_run.offers import estimated_transfer_seconds

        self.assertEqual(
            estimated_transfer_seconds(29_347_330_907, 500),
            470,
        )
        self.assertEqual(
            estimated_transfer_seconds(29_347_330_907, 1000),
            235,
        )
        invalid_cases = (
            (True, 500),
            (-1, 500),
            (1, None),
            (1, math.nan),
            (1, math.inf),
            (1, 0),
        )
        for transfer_bytes, speed in invalid_cases:
            with self.subTest(transfer_bytes=transfer_bytes, speed=speed):
                self.assertIsNone(
                    estimated_transfer_seconds(transfer_bytes, speed)
                )

    def test_requested_gpu_is_exact_and_never_silently_downgraded(self):
        from cloud_run.offers import OfferSelectionError, select_best_offer

        offers = [
            normalized_offer(1, gpu_name="RTX 3090", price=0.2),
            normalized_offer(2, gpu_name="RTX 4090", price=0.5),
        ]
        self.assertEqual(
            select_best_offer(offers, requested_gpu="RTX 4090")["offer_id"],
            2,
        )
        with self.assertRaisesRegex(OfferSelectionError, "no eligible offer"):
            select_best_offer(offers, requested_gpu="RTX 5090")


class ExplainedOfferDecisionTests(unittest.TestCase):
    def test_empty_preferences_are_not_hard_filters(self):
        from cloud_run.offers import decide_offers

        decisions = decide_offers(
            [
                normalized_offer(1, gpu_ram_gb=24, price=0.51, dlperf=80),
                normalized_offer(2, gpu_ram_gb=48, price=1.25, dlperf=120),
            ],
            workflow_min_vram_gb=24,
            workflow_disk_gb=80,
            preferred_vram_gb=None,
            max_price_per_hour=None,
            transfer_bytes=0,
            cached_bytes=0,
            source_ready=True,
        )

        self.assertEqual(
            [decision.offer["offer_id"] for decision in decisions if decision.included],
            [2, 1],
        )
        self.assertTrue(all(decision.included for decision in decisions))

    def test_workflow_minimum_and_price_cap_are_hard_but_vram_is_soft(self):
        from cloud_run.offers import decide_offers

        decisions = decide_offers(
            [
                normalized_offer(1, gpu_ram_gb=12, price=0.20, dlperf=90),
                normalized_offer(2, gpu_ram_gb=24, price=0.51, dlperf=120),
                normalized_offer(3, gpu_ram_gb=24, price=0.50, dlperf=80),
                normalized_offer(4, gpu_ram_gb=24, price=0.49, dlperf=140),
            ],
            workflow_min_vram_gb=24,
            workflow_disk_gb=80,
            preferred_vram_gb=12,
            max_price_per_hour=0.50,
            transfer_bytes=0,
            cached_bytes=0,
            source_ready=True,
        )

        included = [decision.offer["offer_id"] for decision in decisions if decision.included]
        self.assertEqual(included, [4, 3])
        excluded = {
            decision.offer["offer_id"]: decision.excluded_reasons
            for decision in decisions
            if not decision.included
        }
        self.assertIn("vram", excluded[1])
        self.assertIn("price", excluded[2])

    def test_exclusions_are_typed_and_performant_offer_beats_cheap_weak_offer(self):
        from cloud_run.offers import decide_offers

        offers = [
            normalized_offer(1, price=0.35, dlperf=20),
            normalized_offer(2, price=0.45, dlperf=100),
            normalized_offer(3, disk_gb=79, dlperf=90),
            normalized_offer(4, reliability=0.90, dlperf=90),
            normalized_offer(5, inet_down_mbps=None, dlperf=90),
        ]
        decisions = decide_offers(
            offers,
            workflow_min_vram_gb=24,
            workflow_disk_gb=80,
            preferred_vram_gb=24,
            max_price_per_hour=0.50,
            transfer_bytes=1,
            cached_bytes=0,
            source_ready=True,
        )

        included = [decision.offer["offer_id"] for decision in decisions if decision.included]
        self.assertEqual(included, [2, 1])
        excluded = {
            decision.offer["offer_id"]: decision.excluded_reasons
            for decision in decisions
            if not decision.included
        }
        self.assertIn("disk", excluded[3])
        self.assertIn("reliability", excluded[4])
        self.assertIn("missing_metrics", excluded[5])

        unavailable = decide_offers(
            [normalized_offer(6, dlperf=80)],
            workflow_min_vram_gb=24,
            workflow_disk_gb=80,
            transfer_bytes=1,
            source_ready=False,
        )
        self.assertEqual(unavailable[0].excluded_reasons, ("source_readiness",))

    def test_readiness_estimate_uses_remaining_bytes_and_measured_megabytes(self):
        from cloud_run.offers import readiness_estimate

        for observed_mb_s, minimum_seconds in ((25, 1_173), (30, 978)):
            with self.subTest(observed_mb_s=observed_mb_s):
                estimate = readiness_estimate(
                    total_bytes=29_347_469_703,
                    cached_bytes=0,
                    assumed_mbps=observed_mb_s,
                    source_ready=True,
                )
                self.assertEqual(estimate.label, "cold")
                self.assertGreaterEqual(estimate.estimated_seconds, minimum_seconds)
                self.assertFalse(estimate.ten_minute_eligible)
                self.assertEqual(len(estimate.digest), 64)

        prepositioned = readiness_estimate(
            total_bytes=29_347_469_703,
            cached_bytes=20_000_000_000,
            assumed_mbps=30,
            source_ready=True,
        )
        self.assertEqual(prepositioned.label, "prepositioned")
        self.assertLessEqual(prepositioned.estimated_seconds, 600)
        self.assertTrue(prepositioned.ten_minute_eligible)

        warm = readiness_estimate(
            total_bytes=29_347_469_703,
            cached_bytes=29_347_469_703,
            assumed_mbps=25,
            source_ready=True,
        )
        self.assertEqual(warm.label, "warm")
        self.assertEqual(warm.remaining_bytes, 0)
        self.assertEqual(warm.estimated_seconds, 0)


if __name__ == "__main__":
    unittest.main()
