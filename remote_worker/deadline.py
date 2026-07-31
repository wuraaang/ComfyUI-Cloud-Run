"""Instance-scoped deadline enforcement for the Remote Worker."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import os
import re
import time

from .state import WorkerStateError, WorkerStateStore


VAST_API_ORIGIN = "https://console.vast.ai"
MAX_DEADLINE_BODY_BYTES = 4096
MAX_RETRIEVAL_GRACE_SECONDS = 300
_CONTAINER_ID = re.compile(r"[1-9][0-9]{0,19}")
_FINITE_FIELDS = {
    "mode",
    "deadline_at",
    "retrieval_grace_seconds",
}
_NO_LIMIT_FIELDS = {"mode", "acknowledged"}


class DeadlineValidationError(ValueError):
    """A sanitized invalid signed deadline update."""


class DeadlineEnforcementError(RuntimeError):
    """A sanitized own-instance destruction failure."""


@dataclass(frozen=True)
class DeadlineUpdate:
    mode: str
    deadline_at: object
    retrieval_grace_seconds: int
    destroy_intent: bool = False
    destroy_intent_at: object = None
    destroy_requested: bool = False

    def payload(self):
        return {
            "mode": self.mode,
            "deadline_at": self.deadline_at,
            "retrieval_grace_seconds": self.retrieval_grace_seconds,
            "destroy_intent": self.destroy_intent,
            "destroy_requested": self.destroy_requested,
        }


def _deadline_error():
    return DeadlineValidationError("Worker deadline update was rejected.")


def _finite_epoch(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid JSON object.")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("Invalid JSON constant.")


def parse_deadline_update(body):
    if (
        not isinstance(body, bytes)
        or not 0 < len(body) <= MAX_DEADLINE_BODY_BYTES
    ):
        raise _deadline_error()
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        raise _deadline_error() from None
    if not isinstance(payload, dict):
        raise _deadline_error()
    return payload


class AiohttpOwnInstanceProvider:
    """The worker's only provider capability: one fixed DELETE request."""

    async def delete(self, url, api_key):
        if (
            not isinstance(url, str)
            or not re.fullmatch(
                re.escape(VAST_API_ORIGIN)
                + r"/api/v0/instances/[1-9][0-9]{0,19}/",
                url,
            )
            or not isinstance(api_key, str)
            or not api_key
        ):
            raise DeadlineEnforcementError(
                "Worker deadline destruction failed."
            )
        try:
            from aiohttp import ClientSession, ClientTimeout
        except ImportError:
            raise DeadlineEnforcementError(
                "Worker deadline destruction failed."
            ) from None
        try:
            async with ClientSession(
                timeout=ClientTimeout(total=30),
                auto_decompress=False,
            ) as session:
                async with session.delete(
                    url,
                    headers={
                        "Authorization": "Bearer " + api_key,
                        "Accept": "application/json",
                    },
                    allow_redirects=False,
                ) as response:
                    response.release()
                    return int(response.status)
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            raise DeadlineEnforcementError(
                "Worker deadline destruction failed."
            ) from None


class DeadlineWatchdog:
    def __init__(
        self,
        *,
        container_id,
        container_api_key,
        provider,
        state,
        clock=None,
        sleeper=None,
    ):
        if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(
            container_id
        ):
            raise DeadlineValidationError(
                "Worker instance identity is invalid."
            )
        if (
            not isinstance(container_api_key, str)
            or not container_api_key
            or len(container_api_key) > 4096
            or container_api_key != container_api_key.strip()
            or any(ord(character) < 33 for character in container_api_key)
        ):
            raise DeadlineValidationError(
                "Worker instance credentials are invalid."
            )
        if not callable(getattr(provider, "delete", None)):
            raise DeadlineValidationError(
                "Worker deadline provider is invalid."
            )
        if not isinstance(state, WorkerStateStore):
            raise DeadlineValidationError(
                "Worker deadline state is invalid."
            )
        self._container_id = container_id
        self._container_api_key = container_api_key
        self.provider = provider
        self.state = state
        self.clock = clock or time.time
        self.sleeper = sleeper or asyncio.sleep
        self.mode = "finite"
        self.deadline_at = 0.0
        self.retrieval_grace_seconds = 0
        self._task = None
        self._task_loop = None
        self._lock = None
        self._lock_loop = None

    def _now(self):
        value = self.clock()
        if not _finite_epoch(value) or value < 0:
            raise DeadlineEnforcementError(
                "Worker deadline clock is unavailable."
            )
        return float(value)

    @property
    def own_instance_url(self):
        return (
            VAST_API_ORIGIN
            + "/api/v0/instances/"
            + self._container_id
            + "/"
        )

    def _enforcement_lock(self):
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def update(self, payload):
        if not isinstance(payload, dict):
            raise _deadline_error()
        now = self._now()
        current = self.current()
        if (
            current.destroy_intent
            or (
                current.mode == "finite"
                and _finite_epoch(current.deadline_at)
                and float(current.deadline_at) > 0
                and now >= float(current.deadline_at)
            )
        ):
            raise _deadline_error()
        mode = payload.get("mode")
        if mode == "none":
            if (
                set(payload) != _NO_LIMIT_FIELDS
                or payload.get("acknowledged") is not True
            ):
                raise _deadline_error()
            deadline_at = None
            grace = 0
        elif mode == "finite":
            deadline_at = payload.get("deadline_at")
            grace = payload.get("retrieval_grace_seconds")
            if (
                set(payload) != _FINITE_FIELDS
                or not _finite_epoch(deadline_at)
                or float(deadline_at) <= now
                or isinstance(grace, bool)
                or not isinstance(grace, int)
                or not 0 <= grace <= MAX_RETRIEVAL_GRACE_SECONDS
            ):
                raise _deadline_error()
            deadline_at = float(deadline_at)
        else:
            raise _deadline_error()
        try:
            self.state.record_deadline(
                mode=mode,
                deadline_at=deadline_at,
                retrieval_grace_seconds=grace,
                destroy_intent=False,
                destroy_intent_at=None,
                destroy_requested=False,
                updated_at=now,
            )
        except WorkerStateError:
            raise DeadlineEnforcementError(
                "Worker deadline state is unavailable."
            ) from None
        self.mode = mode
        self.deadline_at = deadline_at
        self.retrieval_grace_seconds = grace
        return DeadlineUpdate(
            mode=mode,
            deadline_at=deadline_at,
            retrieval_grace_seconds=grace,
        )

    def current(self):
        try:
            state = self.state.load()
        except WorkerStateError:
            raise DeadlineEnforcementError(
                "Worker deadline state is unavailable."
            ) from None
        record = state["transactions"].get("deadline")
        if not isinstance(record, dict):
            return DeadlineUpdate(
                mode=state["deadline_mode"],
                deadline_at=state["deadline_at"],
                retrieval_grace_seconds=0,
            )
        try:
            return DeadlineUpdate(
                mode=record["mode"],
                deadline_at=record["deadline_at"],
                retrieval_grace_seconds=record[
                    "retrieval_grace_seconds"
                ],
                destroy_intent=record["destroy_intent"],
                destroy_intent_at=record["destroy_intent_at"],
                destroy_requested=record["destroy_requested"],
            )
        except (KeyError, TypeError, ValueError):
            raise DeadlineEnforcementError(
                "Worker deadline state is unavailable."
            ) from None

    async def enforce(
        self,
        *,
        deadline_at,
        mode,
        retrieval_grace_seconds,
    ):
        if mode == "none":
            if deadline_at is not None:
                raise _deadline_error()
            return DeadlineUpdate(
                mode="none",
                deadline_at=None,
                retrieval_grace_seconds=0,
            )
        if (
            mode != "finite"
            or not _finite_epoch(deadline_at)
            or float(deadline_at) <= 0
            or isinstance(retrieval_grace_seconds, bool)
            or not isinstance(retrieval_grace_seconds, int)
            or not 0
            <= retrieval_grace_seconds
            <= MAX_RETRIEVAL_GRACE_SECONDS
        ):
            raise _deadline_error()
        async with self._enforcement_lock():
            remaining = float(deadline_at) - self._now()
            if remaining > 0:
                await self.sleeper(remaining)
            now = self._now()
            current = self.current()
            try:
                has_stored_policy = isinstance(
                    self.state.load()["transactions"].get("deadline"),
                    dict,
                )
            except WorkerStateError:
                raise DeadlineEnforcementError(
                    "Worker deadline state is unavailable."
                ) from None
            if has_stored_policy and (
                current.mode != "finite"
                or current.deadline_at != float(deadline_at)
                or current.retrieval_grace_seconds
                != retrieval_grace_seconds
            ):
                return current
            same_intent = (
                current.mode == "finite"
                and current.deadline_at == float(deadline_at)
                and current.retrieval_grace_seconds
                == retrieval_grace_seconds
                and current.destroy_intent
                and _finite_epoch(current.destroy_intent_at)
            )
            intent_at = (
                float(current.destroy_intent_at)
                if same_intent
                else now
            )
            try:
                if not same_intent:
                    self.state.record_deadline(
                        mode="finite",
                        deadline_at=float(deadline_at),
                        retrieval_grace_seconds=(
                            retrieval_grace_seconds
                        ),
                        destroy_intent=True,
                        destroy_intent_at=intent_at,
                        destroy_requested=False,
                        updated_at=now,
                    )
            except WorkerStateError:
                raise DeadlineEnforcementError(
                    "Worker deadline state is unavailable."
                ) from None
            grace_remaining = max(
                0.0,
                intent_at + retrieval_grace_seconds - self._now(),
            )
            if grace_remaining:
                await self.sleeper(grace_remaining)
            try:
                status = await self.provider.delete(
                    self.own_instance_url,
                    self._container_api_key,
                )
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception:
                raise DeadlineEnforcementError(
                    "Worker deadline destruction failed."
                ) from None
            if (
                isinstance(status, bool)
                or not isinstance(status, int)
                or status not in {200, 202, 204, 404}
            ):
                raise DeadlineEnforcementError(
                    "Worker deadline destruction failed."
                )
            now = self._now()
            try:
                self.state.record_deadline(
                    mode="finite",
                    deadline_at=float(deadline_at),
                    retrieval_grace_seconds=retrieval_grace_seconds,
                    destroy_intent=True,
                    destroy_intent_at=intent_at,
                    destroy_requested=True,
                    updated_at=now,
                )
            except WorkerStateError:
                raise DeadlineEnforcementError(
                    "Worker deadline state is unavailable."
                ) from None
            return DeadlineUpdate(
                mode="finite",
                deadline_at=float(deadline_at),
                retrieval_grace_seconds=retrieval_grace_seconds,
                destroy_intent=True,
                destroy_intent_at=intent_at,
                destroy_requested=True,
            )

    async def watch(self):
        while True:
            update = self.current()
            if update.mode == "none" or not update.deadline_at:
                await self.sleeper(1)
                continue
            if update.destroy_requested:
                return update
            remaining = float(update.deadline_at) - self._now()
            if remaining > 0:
                await self.sleeper(min(1.0, remaining))
                continue
            try:
                return await self.enforce(
                    deadline_at=update.deadline_at,
                    mode=update.mode,
                    retrieval_grace_seconds=(
                        update.retrieval_grace_seconds
                    ),
                )
            except DeadlineEnforcementError:
                await self.sleeper(5)

    def start(self):
        loop = asyncio.get_running_loop()
        if (
            self._task is not None
            and self._task_loop is loop
            and not self._task.done()
        ):
            return self._task
        self._task_loop = loop
        self._task = loop.create_task(self.watch())
        return self._task

    async def close(self):
        task = self._task
        self._task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def deadline_watchdog(
    *,
    state,
    provider=None,
    clock=None,
    sleeper=None,
):
    """Build from Vast's instance-scoped environment only."""

    return DeadlineWatchdog(
        container_id=os.environ.get("CONTAINER_ID", ""),
        container_api_key=os.environ.get("CONTAINER_API_KEY", ""),
        provider=provider or AiohttpOwnInstanceProvider(),
        state=state,
        clock=clock,
        sleeper=sleeper,
    )
