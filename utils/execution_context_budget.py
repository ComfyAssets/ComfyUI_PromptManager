"""Shared payload reservations with explicit, single-owner transport leases."""

from dataclasses import dataclass
import json
import threading

from .execution_contexts import BUNDLE_STATES, CAPTURE_REASONS, ContextBundle


def _byte_count(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("Byte count must be a nonnegative integer")


class ContextBudget:
    def __init__(self, limit_bytes: int = 8388608) -> None:
        _byte_count(limit_bytes)
        self._limit = limit_bytes
        self._charged = 0
        self._lock = threading.Lock()

    @property
    def charged_bytes(self) -> int:
        with self._lock:
            return self._charged

    def reserve(self, byte_count: int) -> "PayloadLease | None":
        _byte_count(byte_count)
        with self._lock:
            if byte_count > self._limit - self._charged:
                return None
            self._charged += byte_count
            return PayloadLease._new(self, byte_count)


class PayloadLease:
    def __init__(self, pool: ContextBudget, byte_count: int) -> None:
        raise ValueError("Reserve payload leases through ContextBudget")

    @classmethod
    def _new(cls, pool: ContextBudget, byte_count: int) -> "PayloadLease":
        owner = object.__new__(cls)
        owner._pool = pool
        owner._bytes = byte_count
        owner._active = True
        return owner

    @property
    def byte_count(self) -> int:
        with self._pool._lock:
            self._require_owner()
            return self._bytes

    def _require_owner(self) -> None:
        if not self._active:
            raise ValueError("Inactive payload lease")

    def transfer(self) -> "PayloadLease":
        with self._pool._lock:
            self._require_owner()
            owner = PayloadLease._new(self._pool, self._bytes)
            self._active = False
            return owner

    def shrink_to(self, byte_count: int) -> None:
        _byte_count(byte_count)
        with self._pool._lock:
            self._require_owner()
            if byte_count > self._bytes:
                raise ValueError("Cannot grow payload lease")
            self._pool._charged -= self._bytes - byte_count
            self._bytes = byte_count

    def release(self) -> None:
        with self._pool._lock:
            if self._active:
                self._pool._charged -= self._bytes
                self._active = False


SHARED_CONTEXT_BUDGET = ContextBudget()


@dataclass(frozen=True)
class SealedContext:
    bundle: ContextBundle | None
    lease: PayloadLease | None
    state: str
    reason: str
    capture_generation: int | None = None

    def validate_header(self) -> None:
        if (
            type(self.state) is not str
            or self.state not in BUNDLE_STATES
            or type(self.reason) is not str
            or self.reason not in CAPTURE_REASONS
            or (
                self.capture_generation is not None
                and (
                    type(self.capture_generation) is not int
                    or not 0 <= self.capture_generation <= 2**63 - 1
                )
            )
        ):
            raise ValueError("Invalid capture status")
        header = json.dumps(
            [self.state, self.reason, self.capture_generation], separators=(",", ":")
        ).encode("utf-8")
        if len(header) > 256:
            raise ValueError("Oversized capture status")
