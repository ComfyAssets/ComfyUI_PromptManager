"""Reviewed restores: staged uploads held under single-use tokens (#249 S8).

A restore preview stages the upload (``database/restore_stage.py``) and
keeps it here under a ``secrets.token_urlsafe(32)`` token with the live
library's fingerprint and the database identity it was reviewed against.
``take`` hands the stage to the restore job exactly once; a second apply of
the token names that job (``PlanUsed``). An expired, discarded or unknown
token raises ``PlanExpired``; its stage folder is removed. A restart forgets
every token (the startup sweep removes the folders).

At most ``max_stages`` stages are held. Bytes are bounded by free disk space
(the upload is refused when the disk has no room, and held stages use that
disk), not by a fixed budget. A stage being applied belongs to its job and is
never evicted or counted.
"""

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .maintenance_plans import PlanExpired, PlanUsed

STAGE_TTL_SECONDS = 900
MAX_STAGES = 2
_USED_KEPT = 32  # applied tokens remembered for plan-used answers


class TooManyStages(Exception):
    """Every stage slot is held by a reviewed restore."""


@dataclass
class HeldRestore:
    staged: Any  # database.restore_stage.StagedRestore
    fingerprint: str
    db_identity: Any
    live: Dict[str, int]
    expires_at: float


class RestorePlanStore:
    def __init__(
        self,
        ttl: float = STAGE_TTL_SECONDS,
        max_stages: int = MAX_STAGES,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._ttl = ttl
        self._max = max_stages
        self._clock = clock or time.time
        self._held: Dict[str, HeldRestore] = {}
        self._used: Dict[str, str] = {}

    def __len__(self):
        return len(self._held)

    def full(self) -> bool:
        return len(self._held) >= self._max

    def issue(self, staged, fingerprint, db_identity, live) -> str:
        """Hold ``staged`` and return its token; TooManyStages when full."""
        if self.full():
            raise TooManyStages()
        token = secrets.token_urlsafe(32)
        self._held[token] = HeldRestore(
            staged=staged,
            fingerprint=fingerprint,
            db_identity=db_identity,
            live=dict(live),
            expires_at=self._clock() + self._ttl,
        )
        return token

    def peek(self, token) -> HeldRestore:
        if token in self._used:
            raise PlanUsed(self._used[token])
        held = self._held.get(token)
        if held is None:
            raise PlanExpired()
        if held.expires_at <= self._clock():
            self.discard(token)
            raise PlanExpired()
        return held

    def take(self, token, job_id) -> HeldRestore:
        """Hand the held stage to the job applying it (single use)."""
        held = self.peek(token)
        del self._held[token]
        self._used[token] = job_id
        while len(self._used) > _USED_KEPT:
            self._used.pop(next(iter(self._used)))
        return held

    def discard(self, token) -> bool:
        """Drop the token and remove its stage; False when it was not held."""
        held = self._held.pop(token, None)
        if held is None:
            return False
        held.staged.discard()
        return True

    def drop_expired(self) -> int:
        """Remove every expired stage (blocking file removal); the count."""
        now = self._clock()
        expired = [t for t, held in self._held.items() if held.expires_at <= now]
        for token in expired:
            self.discard(token)
        return len(expired)

    def discard_all(self) -> None:
        """Remove every held stage (the database was replaced or shutdown)."""
        for token in list(self._held):
            self.discard(token)
