"""The reviewed-plan store behind maintenance previews (#249 System S6).

A preview issues a plan: its operation, the fingerprint of exactly what was
reviewed, the payload an apply needs (target ids, the sources it judged) and
the identity of the database it describes. Everything is held here, in
server memory, under a single-use token (``secrets.token_urlsafe(32)``); the
client only ever sees the token and the public plan. Plans expire after
``ttl`` seconds and are gone after a restart.

The store keeps at most ``max_plans`` plans and ``max_bytes`` of payload
(measured as compact JSON). A plan larger than ``max_bytes`` is refused with
``ScopeTooLarge``: it is never cut down to fit, since an apply must do
exactly what was reviewed. A new plan that fits makes room by dropping the
oldest ones, which then read as expired.

A consumed token is remembered (the last ``MAX_USED``) with the job it
started, so a second apply of it is told which job ran it.
"""

import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field


class PlanExpired(Exception):
    """No live plan has the token: unknown, expired, dropped or another operation's."""


class PlanUsed(Exception):
    """The token was already applied; ``job_id`` is the job that applied it."""

    def __init__(self, job_id):
        super().__init__("This plan was already applied")
        self.job_id = job_id


class ScopeTooLarge(Exception):
    """The plan's payload exceeds the store's byte budget."""


@dataclass
class StoredPlan:
    """One reviewed plan, as only the server sees it."""

    operation: str
    fingerprint: str
    payload: dict
    db_identity: tuple
    expires_at: float  # wall-clock seconds since the epoch, for the response
    deadline: float = field(repr=False)  # on the store's monotonic clock
    size: int = field(repr=False)


def _payload_size(payload):
    return len(json.dumps(payload, separators=(",", ":"), default=str))


class PlanStore:
    """Reviewed maintenance plans under single-use tokens."""

    MAX_USED = 64  # consumed tokens remembered for plan-used answers

    def __init__(
        self,
        ttl=900,
        max_plans=8,
        max_bytes=16 * 2**20,
        *,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self.ttl = ttl
        self.max_plans = max_plans
        self.max_bytes = max_bytes
        self._clock = clock
        self._wall_clock = wall_clock
        self._plans = OrderedDict()  # token -> StoredPlan, oldest first
        self._used = OrderedDict()  # token -> job id, oldest first

    def issue(self, operation, fingerprint, payload, db_identity) -> str:
        """Hold a plan and return its token; ScopeTooLarge when it cannot fit."""
        size = _payload_size(payload)
        if size > self.max_bytes:
            raise ScopeTooLarge(f"The plan needs {size} bytes")
        self._drop_expired()
        while self._plans and (
            len(self._plans) >= self.max_plans
            or self._held_bytes() + size > self.max_bytes
        ):
            self._plans.popitem(last=False)
        token = secrets.token_urlsafe(32)
        self._plans[token] = StoredPlan(
            operation=operation,
            fingerprint=fingerprint,
            payload=payload,
            db_identity=tuple(db_identity),
            expires_at=self._wall_clock() + self.ttl,
            deadline=self._clock() + self.ttl,
            size=size,
        )
        return token

    def peek(self, token, operation=None) -> StoredPlan:
        """The live plan of ``token``, left in place; raises PlanUsed or PlanExpired."""
        if not isinstance(token, str):
            raise PlanExpired("No such plan")
        if token in self._used:
            raise PlanUsed(self._used[token])
        plan = self._plans.get(token)
        if plan is None or (operation is not None and plan.operation != operation):
            raise PlanExpired("No such plan")
        if self._clock() > plan.deadline:
            del self._plans[token]
            raise PlanExpired("The plan expired")
        return plan

    def consume(self, token, operation=None, *, job_id=None) -> StoredPlan:
        """Take the plan of ``token`` once; later calls raise PlanUsed(job_id)."""
        plan = self.peek(token, operation)
        del self._plans[token]
        self._used[token] = job_id
        while len(self._used) > self.MAX_USED:
            self._used.popitem(last=False)
        return plan

    def discard(self, token):
        """Forget a plan that can no longer apply (its database was replaced)."""
        self._plans.pop(token, None)

    def _held_bytes(self):
        return sum(plan.size for plan in self._plans.values())

    def _drop_expired(self):
        now = self._clock()
        for token in [t for t, plan in self._plans.items() if now > plan.deadline]:
            del self._plans[token]
