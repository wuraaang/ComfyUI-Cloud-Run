import importlib.util
import json
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
    reliability=0.98,
    machine_id=None,
    host_id=None,
    public_ipaddr=None,
    inet_down_mbps=None,
    disk_bw_mbps=None,
):
    return {
        "offer_id": offer_id,
        "gpu_name": gpu_name,
        "gpu_ram_gb": 24.0,
        "dph_total": price,
        "reliability": reliability,
        "machine_id": machine_id,
        "host_id": host_id,
        "public_ipaddr": public_ipaddr,
        "inet_down_mbps": inet_down_mbps,
        "disk_bw_mbps": disk_bw_mbps,
    }


class ProviderConstraintTests(unittest.TestCase):
    def test_raw_offers_must_still_satisfy_every_provider_constraint(self):
        from cloud_run.vast import normalize_offers

        base = {
            "id": 1,
            "gpu_name": "RTX 4090",
            "gpu_ram": 24576,
            "dph_total": 0.5,
            "reliability": 0.98,
            "rentable": True,
            "verified": True,
            "num_gpus": 1,
            "type": "ondemand",
            "inet_down": 500,
            "disk_bw": 600,
        }
        invalid_variants = {
            "VRAM": {"gpu_ram": 16384},
            "price cap": {"dph_total": 0.76},
            "on-demand": {"type": "bid"},
            "rentable": {"rentable": False},
            "verified": {"verified": False},
            "one GPU": {"num_gpus": 2},
            "reliability": {"reliability": 0.94},
            "network": {"inet_down": 249},
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
                        min_inet_down_mbps=250,
                        min_disk_bw_mbps=300,
                    ),
                    [],
                )

        valid = normalize_offers(
            {"offers": [base]},
            max_price_per_hour=0.75,
            min_vram_gb=24,
            min_inet_down_mbps=250,
            min_disk_bw_mbps=300,
        )
        self.assertEqual([offer["offer_id"] for offer in valid], [1])

    def test_optional_provider_fields_have_conservative_deterministic_defaults(self):
        from cloud_run.vast import normalize_offers

        raw = {
            "id": 1,
            "gpu_name": "RTX 4090",
            "gpu_ram": 24576,
            "dph_total": 0.5,
            "reliability": 0.98,
        }
        self.assertEqual(
            len(
                normalize_offers(
                    {"offers": [raw]},
                    max_price_per_hour=0.75,
                    min_vram_gb=24,
                )
            ),
            1,
        )
        self.assertEqual(
            normalize_offers(
                {"offers": [raw]},
                max_price_per_hour=0.75,
                min_vram_gb=24,
                min_inet_down_mbps=1,
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

    def test_selection_prefers_reliability_within_ten_percent_of_cheapest(self):
        from cloud_run.offers import select_best_offer

        offers = [
            normalized_offer(1, price=0.50, reliability=0.96),
            normalized_offer(2, price=0.54, reliability=0.99),
            normalized_offer(3, price=0.56, reliability=1.0),
        ]

        self.assertEqual(select_best_offer(offers)["offer_id"], 2)

    def test_selection_uses_quality_then_price_then_id_as_stable_ties(self):
        from cloud_run.offers import select_best_offer

        offers = [
            normalized_offer(
                3,
                price=0.5,
                reliability=None,
                inet_down_mbps=None,
                disk_bw_mbps=None,
            ),
            normalized_offer(
                2,
                price=0.5,
                reliability=None,
                inet_down_mbps=100,
                disk_bw_mbps=None,
            ),
            normalized_offer(
                1,
                price=0.5,
                reliability=None,
                inet_down_mbps=100,
                disk_bw_mbps=200,
            ),
        ]

        self.assertEqual(select_best_offer(offers)["offer_id"], 1)
        self.assertEqual(
            select_best_offer(list(reversed(offers)))["offer_id"],
            1,
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


if __name__ == "__main__":
    unittest.main()
