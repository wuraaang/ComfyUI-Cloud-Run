"""Thin, sanitized Vast.ai HTTP contracts used only by the backend."""

from __future__ import annotations

import ipaddress
import math

from .constants import (
    DEFAULT_DISK_GB,
    MAX_SESSION_DISK_GB,
    MIN_SESSION_DISK_GB,
)
from .worker_release import WorkerRelease, WorkerReleaseError


VAST_API_V0 = "https://console.vast.ai/api/v0"
VAST_API_V1 = "https://console.vast.ai/api/v1"
OFFER_SEARCH_URL = VAST_API_V0 + "/bundles/"
OFFER_SEARCH_LIMIT = 20
OFFER_TIMEOUT_SECONDS = 30
MIN_RELIABILITY = 0.95


class VastError(RuntimeError):
    def __init__(self, message, *, status=None, retryable=False):
        super().__init__(message)
        self.status = status
        self.retryable = bool(retryable)


class VastConfigurationError(VastError):
    pass


class OfferSearchError(VastError):
    """A sanitized Vast offer-search failure."""


class OfferSearchConfigurationError(OfferSearchError, VastConfigurationError):
    """Offer search cannot run with the saved configuration."""


def _finite_number(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        return None
    return float(value)


def _safe_identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text[:160] if text else None


def _validate_disk_gb(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise VastConfigurationError(
            "A valid instance disk size is required."
        )
    disk = value
    if not MIN_SESSION_DISK_GB <= disk <= MAX_SESSION_DISK_GB:
        raise VastConfigurationError(
            "A valid instance disk size is required."
        )
    return disk


def _optional_nonnegative_number(value):
    normalized = _finite_number(value)
    return normalized if normalized is not None and normalized >= 0 else None


def _require_key(api_key):
    if not isinstance(api_key, str) or not api_key.strip():
        raise VastConfigurationError("Vast API key is not configured.")
    return api_key.strip()


def _headers(api_key):
    return {
        "Authorization": "Bearer " + _require_key(api_key),
        "Accept": "application/json",
    }


def build_search_payload(
    max_price_per_hour,
    min_vram_gb,
    *,
    disk_gb=DEFAULT_DISK_GB,
    min_inet_down_mbps=0,
    min_disk_bw_mbps=0,
    min_reliability=MIN_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
):
    """Build the on-demand, one-GPU search query."""
    disk = _validate_disk_gb(disk_gb)
    payload = {
        "gpu_ram": {"gte": int(min_vram_gb) * 1024},
        "reliability": {"gte": float(min_reliability)},
        "rentable": {"eq": True},
        "dph_total": {"lte": float(max_price_per_hour)},
        "disk_space": {"gte": disk},
        "allocated_storage": disk,
        "num_gpus": {"eq": 1},
        "type": "ondemand",
        "limit": OFFER_SEARCH_LIMIT,
        "order": [["dph_total", "asc"]],
    }
    if verified_only:
        payload["verified"] = {"eq": True}
    if secure_cloud_only:
        payload["datacenter"] = {"eq": True}
    if min_inet_down_mbps:
        payload["inet_down"] = {"gte": int(min_inet_down_mbps)}
    if min_disk_bw_mbps:
        payload["disk_bw"] = {"gte": int(min_disk_bw_mbps)}
    return payload


def normalize_offers(
    payload,
    max_price_per_hour,
    min_vram_gb,
    *,
    disk_gb=DEFAULT_DISK_GB,
    min_inet_down_mbps=0,
    min_disk_bw_mbps=0,
    min_reliability=MIN_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
):
    disk_required = _validate_disk_gb(disk_gb)
    if not isinstance(payload, dict):
        raise OfferSearchError("Vast returned an invalid offer response.")
    raw_offers = payload.get("offers")
    if raw_offers is None:
        return []
    if not isinstance(raw_offers, list):
        raise OfferSearchError("Vast returned an invalid offer response.")

    offers = []
    for raw in raw_offers:
        if not isinstance(raw, dict):
            continue
        offer_id = raw.get("id")
        gpu_name = raw.get("gpu_name")
        gpu_ram = _finite_number(raw.get("gpu_ram"))
        price = _finite_number(raw.get("dph_total"))
        reliability = _finite_number(
            raw.get("reliability2")
            if raw.get("reliability2") is not None
            else raw.get("reliability")
        )
        inet_down = _finite_number(raw.get("inet_down"))
        disk_bw = _finite_number(raw.get("disk_bw"))
        disk_space = _finite_number(raw.get("disk_space"))
        rental_type = raw.get("type")
        num_gpus = raw.get("num_gpus")
        rentable = raw.get("rentable")
        verified = raw.get("verified")
        datacenter = raw.get("datacenter")
        if (
            isinstance(offer_id, bool)
            or not isinstance(offer_id, int)
            or offer_id < 0
            or not isinstance(gpu_name, str)
            or not gpu_name.strip()
            or gpu_ram is None
            or gpu_ram < int(min_vram_gb) * 1024
            or price is None
            or price < 0
            or price > float(max_price_per_hour)
            or reliability is None
            or not float(min_reliability) <= reliability <= 1
            or (
                rental_type is not None
                and (
                    not isinstance(rental_type, str)
                    or rental_type.casefold() != "ondemand"
                )
            )
            or (
                num_gpus is not None
                and (
                    isinstance(num_gpus, bool)
                    or not isinstance(num_gpus, (int, float))
                    or float(num_gpus) != 1
                )
            )
            or (rentable is not None and rentable is not True)
            or (verified_only and verified is not None and verified is not True)
            or (
                secure_cloud_only
                and datacenter is not None
                and datacenter is not True
            )
            or (
                min_inet_down_mbps
                and (inet_down is None or inet_down < float(min_inet_down_mbps))
            )
            or (
                min_disk_bw_mbps
                and (disk_bw is None or disk_bw < float(min_disk_bw_mbps))
            )
            or (
                disk_space is not None
                and disk_space < disk_required
            )
        ):
            continue
        offers.append(
            {
                "offer_id": offer_id,
                "gpu_name": gpu_name.strip()[:160],
                "gpu_ram_gb": round(gpu_ram / 1024.0, 1),
                "dph_total": price,
                "reliability": reliability,
                "machine_id": _safe_identifier(raw.get("machine_id")),
                "host_id": _safe_identifier(raw.get("host_id")),
                "public_ipaddr": _safe_identifier(raw.get("public_ipaddr")),
                "inet_down_mbps": inet_down,
                "disk_bw_mbps": disk_bw,
                "inet_down_cost": _optional_nonnegative_number(
                    raw.get("inet_down_cost")
                ),
                "inet_up_cost": _optional_nonnegative_number(
                    raw.get("inet_up_cost")
                ),
            }
        )
    offers.sort(key=lambda offer: offer["dph_total"])
    return offers[:OFFER_SEARCH_LIMIT]


async def _search_with_session(
    session,
    api_key,
    max_price_per_hour,
    min_vram_gb,
    search_options,
    *,
    offer_id=None,
):
    try:
        request_payload = build_search_payload(
            max_price_per_hour,
            min_vram_gb,
            **search_options,
        )
        if offer_id is not None:
            request_payload["ask_contract_id"] = {"eq": int(offer_id)}
            request_payload["limit"] = 1
        async with session.post(
            OFFER_SEARCH_URL,
            headers=_headers(api_key),
            json=request_payload,
        ) as response:
            if response.status in (401, 403):
                raise OfferSearchError("Vast rejected the saved API key.")
            if response.status != 200:
                raise OfferSearchError(
                    "Vast offer search is temporarily unavailable.",
                    status=response.status,
                    retryable=response.status == 429 or response.status >= 500,
                )
            try:
                payload = await response.json()
            except Exception:
                raise OfferSearchError(
                    "Vast returned an invalid offer response."
                ) from None
    except OfferSearchError:
        raise
    except VastConfigurationError as error:
        raise OfferSearchConfigurationError(str(error)) from None
    except Exception:
        raise OfferSearchError(
            "Vast offer search is temporarily unavailable.",
            retryable=True,
        ) from None

    try:
        return normalize_offers(
            payload,
            max_price_per_hour,
            min_vram_gb,
            disk_gb=search_options["disk_gb"],
            min_inet_down_mbps=search_options["min_inet_down_mbps"],
            min_disk_bw_mbps=search_options["min_disk_bw_mbps"],
            min_reliability=search_options["min_reliability"],
            verified_only=search_options["verified_only"],
            secure_cloud_only=search_options["secure_cloud_only"],
        )
    except OfferSearchError:
        raise
    except Exception:
        raise OfferSearchError("Vast returned an invalid offer response.") from None


async def _run_with_session(operation, session):
    if session is not None:
        return await operation(session)
    try:
        import aiohttp
    except ImportError:
        raise VastError("Vast is temporarily unavailable.") from None
    timeout = aiohttp.ClientTimeout(total=OFFER_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        return await operation(client)


async def search_offers(
    api_key,
    max_price_per_hour,
    min_vram_gb,
    session=None,
    *,
    disk_gb=DEFAULT_DISK_GB,
    min_inet_down_mbps=0,
    min_disk_bw_mbps=0,
    min_reliability=MIN_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
):
    if not isinstance(api_key, str) or not api_key.strip():
        raise OfferSearchConfigurationError("Vast API key is not configured.")
    options = {
        "disk_gb": disk_gb,
        "min_inet_down_mbps": min_inet_down_mbps,
        "min_disk_bw_mbps": min_disk_bw_mbps,
        "min_reliability": min_reliability,
        "verified_only": verified_only,
        "secure_cloud_only": secure_cloud_only,
    }

    async def operation(client):
        return await _search_with_session(
            client,
            api_key.strip(),
            max_price_per_hour,
            min_vram_gb,
            options,
        )

    try:
        return await _run_with_session(operation, session)
    except OfferSearchError:
        raise
    except VastError:
        raise OfferSearchError(
            "Vast offer search is temporarily unavailable."
        ) from None


def _validate_instance_id(instance_id):
    value = _safe_identifier(instance_id)
    if value is None or not value.isdigit():
        raise VastConfigurationError("A valid Vast instance ID is required.")
    return value


def _validate_offer_id(offer_id):
    value = _safe_identifier(offer_id)
    if value is None or not value.isdigit():
        raise VastConfigurationError("A valid Vast offer ID is required.")
    return value


async def get_offer(
    api_key,
    offer_id,
    max_price_per_hour,
    min_vram_gb,
    session=None,
    *,
    disk_gb=DEFAULT_DISK_GB,
    min_inet_down_mbps=0,
    min_disk_bw_mbps=0,
    min_reliability=MIN_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
):
    if not isinstance(api_key, str) or not api_key.strip():
        raise OfferSearchConfigurationError("Vast API key is not configured.")
    identifier = _validate_offer_id(offer_id)
    options = {
        "disk_gb": disk_gb,
        "min_inet_down_mbps": min_inet_down_mbps,
        "min_disk_bw_mbps": min_disk_bw_mbps,
        "min_reliability": min_reliability,
        "verified_only": verified_only,
        "secure_cloud_only": secure_cloud_only,
    }

    async def operation(client):
        offers = await _search_with_session(
            client,
            api_key.strip(),
            max_price_per_hour,
            min_vram_gb,
            options,
            offer_id=identifier,
        )
        return next(
            (
                offer
                for offer in offers
                if str(offer.get("offer_id")) == identifier
            ),
            None,
        )

    try:
        return await _run_with_session(operation, session)
    except OfferSearchError:
        raise
    except VastError:
        raise OfferSearchError(
            "Vast offer search is temporarily unavailable."
        ) from None


def _validate_label(label):
    value = _safe_identifier(label)
    if (
        value is None
        or len(value) > 64
        or not value.startswith("comfy-cloud-run-")
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for character in value)
    ):
        raise VastConfigurationError("A valid managed instance label is required.")
    return value


async def create_instance(
    api_key,
    *,
    offer_id,
    disk_gb,
    label,
    release,
    session=None,
):
    if not isinstance(release, WorkerRelease):
        raise VastConfigurationError(
            "A reviewed worker release is required."
        )
    try:
        validated_release = WorkerRelease.from_payload(
            release.to_record()
        )
    except (AttributeError, WorkerReleaseError):
        raise VastConfigurationError(
            "A reviewed worker release is required."
        ) from None
    disk = _validate_disk_gb(disk_gb)
    key = _require_key(api_key)
    offer = _validate_offer_id(offer_id)
    managed_label = _validate_label(label)

    async def operation(client):
        try:
            async with client.put(
                VAST_API_V0 + "/asks/" + offer + "/",
                headers=_headers(key),
                json={
                    "template_hash_id": (
                        validated_release.template_hash_id
                    ),
                    "label": managed_label,
                    "disk": disk,
                },
            ) as response:
                if response.status != 200:
                    if response.status in (401, 403):
                        message = "Vast API key cannot create instances."
                    elif response.status in (404, 410):
                        message = (
                            "The selected Vast offer or template is no longer "
                            "available."
                        )
                    elif response.status == 400:
                        message = "Vast rejected the instance configuration."
                    else:
                        message = "Vast instance creation failed."
                    raise VastError(
                        message,
                        status=response.status,
                        retryable=response.status in (408, 409, 429)
                        or response.status >= 500,
                    )
                try:
                    payload = await response.json()
                except Exception:
                    raise VastError(
                        "Vast instance creation returned an invalid response."
                    ) from None
        except VastError:
            raise
        except Exception:
            raise VastError(
                "Vast instance creation outcome is unknown.",
                retryable=True,
            ) from None
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or _safe_identifier(payload.get("new_contract")) is None
        ):
            raise VastError("Vast instance creation returned an invalid response.")
        return str(payload["new_contract"])

    return await _run_with_session(operation, session)


def _normalize_instance(raw):
    if not isinstance(raw, dict) or _safe_identifier(raw.get("id")) is None:
        return None
    return {
        "instance_id": str(raw["id"]),
        "actual_status": _safe_identifier(raw.get("actual_status")),
        "public_ipaddr": _safe_identifier(raw.get("public_ipaddr")),
        "ports": raw.get("ports") if isinstance(raw.get("ports"), dict) else {},
        "label": _safe_identifier(raw.get("label")),
        "dph_total": _finite_number(raw.get("dph_total")),
        "status_msg": _safe_identifier(raw.get("status_msg")),
        "jupyter_token": _safe_identifier(raw.get("jupyter_token")),
    }


async def list_instances(api_key, *, session=None):
    key = _require_key(api_key)

    async def operation(client):
        try:
            async with client.get(
                VAST_API_V1 + "/instances/",
                headers=_headers(key),
            ) as response:
                if response.status != 200:
                    raise VastError(
                        "Vast instance inventory is unavailable.",
                        status=response.status,
                        retryable=response.status == 429 or response.status >= 500,
                    )
                try:
                    payload = await response.json()
                except Exception:
                    raise VastError(
                        "Vast instance inventory returned an invalid response."
                    ) from None
        except VastError:
            raise
        except Exception:
            raise VastError(
                "Vast instance inventory is unavailable.",
                retryable=True,
            ) from None
        raw_instances = payload.get("instances") if isinstance(payload, dict) else None
        if not isinstance(raw_instances, list):
            raise VastError("Vast instance inventory returned an invalid response.")
        return [
            normalized
            for normalized in (_normalize_instance(item) for item in raw_instances)
            if normalized is not None
        ]

    return await _run_with_session(operation, session)


async def get_instance(api_key, instance_id, *, session=None):
    key = _require_key(api_key)
    identifier = _validate_instance_id(instance_id)

    async def operation(client):
        try:
            async with client.get(
                VAST_API_V0 + "/instances/" + identifier + "/",
                headers=_headers(key),
            ) as response:
                if response.status == 404:
                    return None
                if response.status != 200:
                    raise VastError(
                        "Vast instance status is unavailable.",
                        status=response.status,
                        retryable=response.status == 429 or response.status >= 500,
                    )
                try:
                    payload = await response.json()
                except Exception:
                    raise VastError(
                        "Vast instance status returned an invalid response."
                    ) from None
        except VastError:
            raise
        except Exception:
            raise VastError(
                "Vast instance status is unavailable.",
                retryable=True,
            ) from None
        instances = payload.get("instances") if isinstance(payload, dict) else None
        if instances is None:
            return None
        if isinstance(instances, list):
            for item in instances:
                if str(item.get("id")) == identifier:
                    return _normalize_instance(item)
            return None
        normalized = _normalize_instance(instances)
        if normalized is None:
            raise VastError("Vast instance status returned an invalid response.")
        return normalized

    return await _run_with_session(operation, session)


async def destroy_instance(api_key, instance_id, *, session=None):
    try:
        key = _require_key(api_key)
        identifier = _validate_instance_id(instance_id)
    except VastConfigurationError:
        return False

    async def operation(client):
        try:
            async with client.delete(
                VAST_API_V0 + "/instances/" + identifier + "/",
                headers=_headers(key),
            ) as response:
                return response.status in (200, 404)
        except Exception:
            return False

    try:
        return await _run_with_session(operation, session)
    except VastError:
        return False


def derive_base_url(instance, container_port):
    if not isinstance(instance, dict):
        return None
    try:
        address = ipaddress.ip_address(instance.get("public_ipaddr"))
        port_key = str(int(container_port)) + "/tcp"
        entries = (instance.get("ports") or {}).get(port_key) or []
        host_port = int((entries[0] or {}).get("HostPort"))
    except (TypeError, ValueError, IndexError):
        return None
    if (
        not address.is_global
        or not 1 <= host_port <= 65535
    ):
        return None
    host = "[" + str(address) + "]" if address.version == 6 else str(address)
    return "http://" + host + ":" + str(host_port)
