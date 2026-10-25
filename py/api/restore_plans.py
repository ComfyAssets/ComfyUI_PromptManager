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

The store is only read and changed on the event loop (it has no lock). Its
entries own stage folders, whose removal is blocking: every method that
drops an entry returns it (or keeps it in ``take_doomed``), and the caller
removes the folder in the executor (``HeldRestore.staged.discard``).
"""

import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

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
        clock: Optional[Callable[[], float]] = None,
        ttl: float = STAGE_TTL_SECONDS,
        max_stages: int = MAX_STAGES,
    ):
        self._ttl = ttl
        self._max = max_stages
        self._clock = clock or time.time
        self._held: Dict[str, HeldRestore] = {}
        self._used: Dict[str, str] = {}
        self._doomed: List[HeldRestore] = []

    @property
    def ttl(self) -> float:
        return self._ttl

    def now(self) -> float:
        return self._clock()

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
            self._doomed.append(self._held.pop(token))
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

    def give_back(self, token, held) -> bool:
        """Hold a taken stage again under its token (its job did not apply it).

        False, holding nothing, when the review expired meanwhile or its slot
        was reused (the store is full): the caller then discards the stage.
        """
        if held.expires_at <= self._clock() or self.full():
            return False
        self._used.pop(token, None)
        self._held[token] = held
        return True

    def entry(self, token) -> Optional[HeldRestore]:
        """The held entry, expired or not (for the response that issued it)."""
        return self._held.get(token)

    def pop(self, token) -> Optional[HeldRestore]:
        """Drop the token; its entry (to discard) or None when it was not held."""
        return self._held.pop(token, None)

    def pop_expired(self) -> List[HeldRestore]:
        """Drop every expired entry; them, with any ``peek`` found expired."""
        now = self._clock()
        expired = [t for t, held in self._held.items() if held.expires_at <= now]
        return [self._held.pop(token) for token in expired] + self.take_doomed()

    def pop_all(self) -> List[HeldRestore]:
        """Drop every entry (the library was replaced); them, to discard."""
        held = list(self._held.values()) + self.take_doomed()
        self._held.clear()
        return held

    def take_doomed(self) -> List[HeldRestore]:
        doomed, self._doomed = self._doomed, []
        return doomed

    def next_expiry(self) -> Optional[float]:
        """When the soonest held entry expires (clock time), or None."""
        return min((held.expires_at for held in self._held.values()), default=None)
