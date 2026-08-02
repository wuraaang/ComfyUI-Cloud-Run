"""Thin, sanitized Vast.ai HTTP contracts used only by the backend."""

from __future__ import annotations

import asyncio
import ipaddress
import math
import ssl

from .constants import (
    DEFAULT_DISK_GB,
    MAX_SESSION_DISK_GB,
    MIN_SESSION_DISK_GB,
    MIN_VAST_INET_DOWN_MBPS,
    MIN_VAST_RELIABILITY,
    PREFERRED_VAST_INET_DOWN_MBPS,
    VAST_CREATE_FAILURE_CODES,
)
from .offers import offer_quality_key
from .worker_release import WorkerRelease, WorkerReleaseError
from .worker_protocol import (
    BOUNDARY_TOKEN_ENVIRONMENT,
    SESSION_ID_ENVIRONMENT,
    is_boundary_token,
    is_worker_session_id,
)


VAST_API_V0 = "https://console.vast.ai/api/v0"
VAST_API_V1 = "https://console.vast.ai/api/v1"
OFFER_SEARCH_URL = VAST_API_V0 + "/bundles/"
OFFER_SEARCH_LIMIT = 20
OFFER_TIMEOUT_SECONDS = 30


class VastError(RuntimeError):
    code: str | None
    status: int | None
    retryable: bool

    def __init__(self, message, *, code=None, status=None, retryable=False):
        if code is not None and code not in VAST_CREATE_FAILURE_CODES:
            raise ValueError("Invalid safe Vast error code.")
        super().__init__(message)
        self.code = code
        self.status = (
            status
            if isinstance(status, int) and not isinstance(status, bool)
            else None
        )
        self.retryable = bool(retryable)

    def __repr__(self):
        return (
            type(self).__name__
            + "(message="
            + repr(str(self))
            + ", code="
            + repr(self.code)
            + ", status="
            + repr(self.status)
            + ", retryable="
            + repr(self.retryable)
            + ")"
        )


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


def _validated_minimum(value, fixed_floor, message, *, maximum=None):
    normalized = _finite_number(value)
    if (
        normalized is None
        or normalized < fixed_floor
        or (maximum is not None and normalized > maximum)
    ):
        raise VastConfigurationError(message)
    return int(normalized) if normalized.is_integer() else normalized


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
    min_inet_down_mbps=MIN_VAST_INET_DOWN_MBPS,
    max_inet_down_mbps=None,
    min_disk_bw_mbps=0,
    min_reliability=MIN_VAST_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
    order=None,
):
    """Build the on-demand, one-GPU search query."""
    disk = _validate_disk_gb(disk_gb)
    reliability_floor = _validated_minimum(
        min_reliability,
        MIN_VAST_RELIABILITY,
        "Vast reliability cannot be below the fixed safety floor.",
        maximum=1,
    )
    download_floor = _validated_minimum(
        min_inet_down_mbps,
        MIN_VAST_INET_DOWN_MBPS,
        "Vast download speed cannot be below the fixed safety floor.",
    )
    if max_inet_down_mbps is not None:
        download_ceiling = _finite_number(max_inet_down_mbps)
        if download_ceiling is None or download_ceiling <= download_floor:
            raise VastConfigurationError(
                "Vast download speed interval is invalid."
            )
        download_ceiling = (
            int(download_ceiling)
            if download_ceiling.is_integer()
            else download_ceiling
        )
    else:
        download_ceiling = None
    payload = {
        "gpu_ram": {"gte": int(min_vram_gb) * 1024},
        "reliability": {"gte": reliability_floor},
        "rentable": {"eq": True},
        "dph_total": {"lte": float(max_price_per_hour)},
        "disk_space": {"gte": disk},
        "allocated_storage": disk,
        "gpu_arch": {"eq": "nvidia"},
        "cpu_arch": {"eq": "amd64"},
        "cuda_max_good": {"gte": 12.9},
        "compute_cap": {"gte": 750},
        "num_gpus": {"eq": 1},
        "type": "ondemand",
        "limit": OFFER_SEARCH_LIMIT,
        "inet_down": {"gte": download_floor},
        "order": order or [["dph_total", "asc"]],
    }
    if verified_only:
        payload["verified"] = {"eq": True}
    if secure_cloud_only:
        payload["datacenter"] = {"eq": True}
    if download_ceiling is not None:
        payload["inet_down"]["lt"] = download_ceiling
    if min_disk_bw_mbps:
        payload["disk_bw"] = {"gte": int(min_disk_bw_mbps)}
    return payload


def normalize_offers(
    payload,
    max_price_per_hour,
    min_vram_gb,
    *,
    disk_gb=DEFAULT_DISK_GB,
    min_inet_down_mbps=MIN_VAST_INET_DOWN_MBPS,
    min_disk_bw_mbps=0,
    min_reliability=MIN_VAST_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
    requested_rental_type=None,
):
    disk_required = _validate_disk_gb(disk_gb)
    reliability_floor = _validated_minimum(
        min_reliability,
        MIN_VAST_RELIABILITY,
        "Vast reliability cannot be below the fixed safety floor.",
        maximum=1,
    )
    download_floor = _validated_minimum(
        min_inet_down_mbps,
        MIN_VAST_INET_DOWN_MBPS,
        "Vast download speed cannot be below the fixed safety floor.",
    )
    if not isinstance(payload, dict):
        raise OfferSearchError("Vast returned an invalid offer response.")
    raw_offers = payload.get("offers")
    if raw_offers is None:
        return []
    if not isinstance(raw_offers, list):
        raise OfferSearchError("Vast returned an invalid offer response.")

    request_guarantees_ondemand = (
        isinstance(requested_rental_type, str)
        and requested_rental_type.casefold() == "ondemand"
    )
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
        dlperf = _optional_nonnegative_number(raw.get("dlperf"))
        disk_space = _finite_number(raw.get("disk_space"))
        gpu_arch = raw.get("gpu_arch")
        cpu_arch = raw.get("cpu_arch")
        cuda_max_good = _finite_number(raw.get("cuda_max_good"))
        compute_cap = _finite_number(raw.get("compute_cap"))
        rental_type = raw.get("type")
        has_rental_type = "type" in raw
        rental_type_is_valid = (
            isinstance(rental_type, str)
            and rental_type.casefold() == "ondemand"
        ) or (not has_rental_type and request_guarantees_ondemand)
        num_gpus = raw.get("num_gpus")
        rentable = raw.get("rentable")
        verified = raw.get("verified")
        verification = raw.get("verification")
        datacenter = raw.get("datacenter")
        has_verified = "verified" in raw and verified is not None
        has_verification = "verification" in raw and verification is not None
        verification_is_valid = (
            (has_verified or has_verification)
            and (not has_verified or verified is True)
            and (
                not has_verification
                or (
                    isinstance(verification, str)
                    and verification.casefold() == "verified"
                )
            )
        )
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
            or not reliability_floor <= reliability <= 1
            or not rental_type_is_valid
            or isinstance(num_gpus, bool)
            or not isinstance(num_gpus, (int, float))
            or float(num_gpus) != 1
            or rentable is not True
            or (verified_only and not verification_is_valid)
            or (secure_cloud_only and datacenter is not True)
            or inet_down is None
            or inet_down < download_floor
            or (
                min_disk_bw_mbps
                and (disk_bw is None or disk_bw < float(min_disk_bw_mbps))
            )
            or disk_space is None
            or disk_space < disk_required
            or gpu_arch != "nvidia"
            or cpu_arch != "amd64"
            or cuda_max_good is None
            or cuda_max_good < 12.9
            or compute_cap is None
            or compute_cap < 750
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
                "dlperf": dlperf,
            }
        )
    offers.sort(key=offer_quality_key)
    return offers[:OFFER_SEARCH_LIMIT]


async def _search_with_session(
    session,
    api_key,
    max_price_per_hour,
    min_vram_gb,
    search_options,
    *,
    offer_id=None,
    max_inet_down_mbps=None,
    order=None,
):
    try:
        request_payload = build_search_payload(
            max_price_per_hour,
            min_vram_gb,
            **search_options,
            max_inet_down_mbps=max_inet_down_mbps,
            order=order,
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
            requested_rental_type=request_payload.get("type"),
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
        raise VastError(
            "Vast is temporarily unavailable.",
            code="transport_unknown",
            retryable=True,
        ) from None
    try:
        timeout = aiohttp.ClientTimeout(total=OFFER_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            return await operation(client)
    except (asyncio.CancelledError, KeyboardInterrupt):
        raise
    except VastError:
        raise
    except Exception as error:
        raise VastError(
            "Vast is temporarily unavailable.",
            code=_create_transport_code(error),
            retryable=True,
        ) from None


async def search_offers(
    api_key,
    max_price_per_hour,
    min_vram_gb,
    session=None,
    *,
    disk_gb=DEFAULT_DISK_GB,
    min_inet_down_mbps=MIN_VAST_INET_DOWN_MBPS,
    min_disk_bw_mbps=0,
    min_reliability=MIN_VAST_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
):
    if not isinstance(api_key, str) or not api_key.strip():
        raise OfferSearchConfigurationError("Vast API key is not configured.")
    try:
        reliability_floor = _validated_minimum(
            min_reliability,
            MIN_VAST_RELIABILITY,
            "Vast reliability cannot be below the fixed safety floor.",
            maximum=1,
        )
        download_floor = _validated_minimum(
            min_inet_down_mbps,
            MIN_VAST_INET_DOWN_MBPS,
            "Vast download speed cannot be below the fixed safety floor.",
        )
    except VastConfigurationError as error:
        raise OfferSearchConfigurationError(str(error)) from None
    options = {
        "disk_gb": disk_gb,
        "min_inet_down_mbps": download_floor,
        "min_disk_bw_mbps": min_disk_bw_mbps,
        "min_reliability": reliability_floor,
        "verified_only": verified_only,
        "secure_cloud_only": secure_cloud_only,
    }

    async def operation(client):
        target_options = {
            **options,
            "min_inet_down_mbps": max(
                PREFERRED_VAST_INET_DOWN_MBPS,
                download_floor,
            ),
        }
        target = await _search_with_session(
            client,
            api_key.strip(),
            max_price_per_hour,
            min_vram_gb,
            target_options,
            order=[
                ["dlperf", "desc"],
                ["reliability", "desc"],
                ["disk_bw", "desc"],
                ["dph_total", "asc"],
                ["id", "asc"],
            ],
        )
        result_sets = [target]
        if download_floor < PREFERRED_VAST_INET_DOWN_MBPS:
            fallback = await _search_with_session(
                client,
                api_key.strip(),
                max_price_per_hour,
                min_vram_gb,
                options,
                max_inet_down_mbps=PREFERRED_VAST_INET_DOWN_MBPS,
                order=[
                    ["dlperf", "desc"],
                    ["inet_down", "desc"],
                    ["reliability", "desc"],
                    ["disk_bw", "desc"],
                    ["dph_total", "asc"],
                    ["id", "asc"],
                ],
            )
            result_sets.append(fallback)
        unique = {}
        for result_set in result_sets:
            for offer in result_set:
                unique.setdefault(offer["offer_id"], offer)
        return sorted(unique.values(), key=offer_quality_key)[
            : 2 * OFFER_SEARCH_LIMIT
        ]

    try:
        return await _run_with_session(operation, session)
    except OfferSearchError:
        raise
    except VastError:
        raise OfferSearchError(
            "Vast offer search is temporarily unavailable."
        ) from None


def _numeric_identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if (
        not text
        or len(text) > 160
        or not text.isdigit()
        or (isinstance(value, str) and value != value.strip())
    ):
        return None
    return str(int(text, 10))


def _validate_instance_id(instance_id):
    value = _numeric_identifier(instance_id)
    if value is None:
        raise VastConfigurationError("A valid Vast instance ID is required.")
    return value


def _validate_offer_id(offer_id):
    value = _numeric_identifier(offer_id)
    if value is None:
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
    min_inet_down_mbps=MIN_VAST_INET_DOWN_MBPS,
    min_disk_bw_mbps=0,
    min_reliability=MIN_VAST_RELIABILITY,
    verified_only=True,
    secure_cloud_only=False,
):
    if not isinstance(api_key, str) or not api_key.strip():
        raise OfferSearchConfigurationError("Vast API key is not configured.")
    identifier = _validate_offer_id(offer_id)
    try:
        reliability_floor = _validated_minimum(
            min_reliability,
            MIN_VAST_RELIABILITY,
            "Vast reliability cannot be below the fixed safety floor.",
            maximum=1,
        )
        download_floor = _validated_minimum(
            min_inet_down_mbps,
            MIN_VAST_INET_DOWN_MBPS,
            "Vast download speed cannot be below the fixed safety floor.",
        )
    except VastConfigurationError as error:
        raise OfferSearchConfigurationError(str(error)) from None
    options = {
        "disk_gb": disk_gb,
        "min_inet_down_mbps": download_floor,
        "min_disk_bw_mbps": min_disk_bw_mbps,
        "min_reliability": reliability_floor,
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
    value = label if isinstance(label, str) else None
    if (
        value is None
        or value != value.strip()
        or len(value) > 64
        or not value.startswith("comfy-cloud-run-")
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for character in value)
    ):
        raise VastConfigurationError("A valid managed instance label is required.")
    return value


def _worker_environment(boundary_token, session_id):
    if (
        not is_boundary_token(boundary_token)
        or not is_worker_session_id(session_id)
    ):
        raise VastConfigurationError(
            "A valid worker boundary context is required."
        )
    return {
        BOUNDARY_TOKEN_ENVIRONMENT: boundary_token,
        SESSION_ID_ENVIRONMENT: session_id,
    }


_CREATE_ERROR_MESSAGES = {
    "configuration_rejected": "Vast rejected the instance configuration.",
    "api_key_rejected": "Vast API key cannot create instances.",
    "offer_unavailable": (
        "The selected Vast offer or template is no longer available."
    ),
    "rate_limited": "Vast instance creation failed.",
    "retryable_http": "Vast instance creation failed.",
    "timeout": "Vast instance creation outcome is unknown.",
    "connection": "Vast instance creation outcome is unknown.",
    "tls": "Vast instance creation outcome is unknown.",
    "server_disconnected": "Vast instance creation outcome is unknown.",
    "invalid_response": (
        "Vast instance creation returned an invalid response."
    ),
    "transport_unknown": "Vast instance creation outcome is unknown.",
}


def _create_http_error(status):
    if status == 400:
        code = "configuration_rejected"
        retryable = False
    elif status in (401, 403):
        code = "api_key_rejected"
        retryable = False
    elif status in (404, 410):
        code = "offer_unavailable"
        retryable = False
    elif status == 429:
        code = "rate_limited"
        retryable = True
    elif status in (408, 409) or (
        isinstance(status, int)
        and not isinstance(status, bool)
        and status >= 500
    ):
        code = "retryable_http"
        retryable = True
    else:
        code = "transport_unknown"
        retryable = True
    return VastError(
        _CREATE_ERROR_MESSAGES[code],
        code=code,
        status=status,
        retryable=retryable,
    )


def _invalid_create_response():
    return VastError(
        _CREATE_ERROR_MESSAGES["invalid_response"],
        code="invalid_response",
        status=200,
        retryable=True,
    )


def _optional_aiohttp_exception_types(*names):
    try:
        import aiohttp
    except ImportError:
        return ()
    classes = []
    for name in names:
        candidate = getattr(aiohttp, name, None)
        if isinstance(candidate, type) and candidate not in classes:
            classes.append(candidate)
    return tuple(classes)


def _create_transport_code(error):
    if isinstance(error, TimeoutError):
        return "timeout"
    tls_types = _optional_aiohttp_exception_types(
        "ClientSSLError",
        "ClientConnectorCertificateError",
        "ClientConnectorSSLError",
        "ServerFingerprintMismatch",
        "FingerprintMismatch",
    )
    if isinstance(error, (ssl.SSLError, *tls_types)):
        return "tls"
    disconnect_types = _optional_aiohttp_exception_types(
        "ServerDisconnectedError",
        "ClientPayloadError",
    )
    if isinstance(
        error,
        (ConnectionResetError, BrokenPipeError, EOFError, *disconnect_types),
    ):
        return "server_disconnected"
    connection_types = _optional_aiohttp_exception_types(
        "ClientConnectionError",
        "ClientConnectorError",
        "ClientOSError",
    )
    if isinstance(error, (ConnectionError, *connection_types)):
        return "connection"
    return "transport_unknown"


def _create_transport_error(error):
    code = _create_transport_code(error)
    return VastError(
        _CREATE_ERROR_MESSAGES[code],
        code=code,
        retryable=True,
    )


async def create_instance(
    api_key,
    *,
    offer_id,
    disk_gb,
    label,
    release,
    boundary_token,
    session_id,
    session=None,
):
    worker_environment = _worker_environment(boundary_token, session_id)
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
                    "env": worker_environment,
                },
            ) as response:
                if response.status != 200:
                    raise _create_http_error(response.status)
                try:
                    payload = await response.json()
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception:
                    raise _invalid_create_response() from None
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except VastError:
            raise
        except Exception as error:
            raise _create_transport_error(error) from None
        contract_id = (
            _numeric_identifier(payload.get("new_contract"))
            if isinstance(payload, dict)
            and payload.get("success") is True
            else None
        )
        if (
            contract_id is None
            or not contract_id.isdigit()
        ):
            raise _invalid_create_response()
        return contract_id

    return await _run_with_session(operation, session)


def _normalize_instance(raw):
    identifier = (
        _numeric_identifier(raw.get("id"))
        if isinstance(raw, dict)
        else None
    )
    if identifier is None:
        return None
    return {
        "instance_id": identifier,
        "actual_status": _safe_identifier(raw.get("actual_status")),
        "public_ipaddr": _safe_identifier(raw.get("public_ipaddr")),
        "ports": raw.get("ports") if isinstance(raw.get("ports"), dict) else {},
        "label": _safe_identifier(raw.get("label")),
        "dph_total": _finite_number(raw.get("dph_total")),
        "status_msg": _safe_identifier(raw.get("status_msg")),
    }


async def list_instances(api_key, *, session=None):
    key = _require_key(api_key)

    async def operation(client):
        instances = []
        instance_ids = set()
        requested_tokens = set()
        expected_total = None
        instances_found = 0
        after_token = None

        while True:
            params = {"limit": 25}
            if after_token is not None:
                params["after_token"] = after_token
            try:
                async with client.get(
                    VAST_API_V1 + "/instances/",
                    headers=_headers(key),
                    params=params,
                ) as response:
                    if response.status != 200:
                        raise VastError(
                            "Vast instance inventory is unavailable.",
                            status=response.status,
                            retryable=response.status == 429
                            or response.status >= 500,
                        )
                    try:
                        payload = await response.json()
                    except (asyncio.CancelledError, KeyboardInterrupt):
                        raise
                    except Exception:
                        raise VastError(
                            "Vast instance inventory returned an invalid response."
                        ) from None
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except VastError:
                raise
            except Exception:
                raise VastError(
                    "Vast instance inventory is unavailable.",
                    retryable=True,
                ) from None

            if (
                not isinstance(payload, dict)
                or payload.get("success") is not True
                or "next_token" not in payload
            ):
                raise VastError(
                    "Vast instance inventory returned an invalid response."
                )
            raw_instances = payload.get("instances")
            page_count = payload.get("instances_found")
            total = payload.get("total_instances")
            next_token = payload["next_token"]
            if (
                not isinstance(raw_instances, list)
                or isinstance(page_count, bool)
                or not isinstance(page_count, int)
                or page_count < 0
                or page_count > 25
                or page_count != len(raw_instances)
                or isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or (next_token is not None and page_count == 0)
            ):
                raise VastError(
                    "Vast instance inventory returned an invalid response."
                )
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise VastError(
                    "Vast instance inventory returned an invalid response."
                )

            page_instances = []
            for raw_instance in raw_instances:
                normalized = _normalize_instance(raw_instance)
                if normalized is None:
                    raise VastError(
                        "Vast instance inventory returned an invalid response."
                    )
                identifier = normalized["instance_id"]
                if identifier in instance_ids:
                    raise VastError(
                        "Vast instance inventory returned an invalid response."
                    )
                instance_ids.add(identifier)
                page_instances.append(normalized)
            instances.extend(page_instances)
            instances_found += page_count
            if (
                instances_found > expected_total
                or len(instance_ids) > expected_total
            ):
                raise VastError(
                    "Vast instance inventory returned an invalid response."
                )

            if next_token is None:
                if (
                    instances_found != expected_total
                    or len(instance_ids) != expected_total
                ):
                    raise VastError(
                        "Vast instance inventory returned an invalid response."
                    )
                return instances
            if (
                not isinstance(next_token, str)
                or not next_token
                or next_token != next_token.strip()
                or len(next_token) > 4096
                or next_token in requested_tokens
                or instances_found >= expected_total
            ):
                raise VastError(
                    "Vast instance inventory returned an invalid response."
                )
            requested_tokens.add(next_token)
            after_token = next_token

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
