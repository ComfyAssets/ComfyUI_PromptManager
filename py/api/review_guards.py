"""Body validation of Needs review decisions and manual links (#249 E3, #536)."""

from .annotations import _HEX64, _TOKEN, _UUID
from .prompts import bad_request

UNRESOLVED = "unresolved"
_GUARD_FIELDS = frozenset(
    {
        "expected_epoch",
        "expected_revision",
        "capture_token",
        "sha256",
        "dependency_fingerprint",
    }
)
_DECISION_FIELDS = _GUARD_FIELDS | {"choice"}
_LINK_FIELDS = _GUARD_FIELDS | {"positive_id", "pair_id"}


def _guard_error(data: dict, fields: frozenset):
    """The first problem with a decision's shared fields, as a response."""
    if set(data) != fields:
        return bad_request("Request must contain exactly " + ", ".join(sorted(fields)))
    epoch, revision = data["expected_epoch"], data["expected_revision"]
    if not isinstance(epoch, str) or _UUID.fullmatch(epoch) is None:
        return bad_request("expected_epoch must be a canonical UUID")
    if type(revision) is not int or revision < 0:
        return bad_request("expected_revision must be a non-negative integer")
    token = data["capture_token"]
    if token is not None and (
        not isinstance(token, str) or _TOKEN.fullmatch(token) is None
    ):
        return bad_request("Invalid capture token")
    for name in ("sha256", "dependency_fingerprint"):
        value = data[name]
        if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
            return bad_request(f"{name} must be 64 lowercase hexadecimal characters")
    return None


def _decision_guards(data: dict):
    """The validated decision fields, or ``(None, error)``."""
    error = _guard_error(data, _DECISION_FIELDS)
    if error is not None:
        return None, error
    choice = data["choice"]
    if not isinstance(choice, str) or (
        choice != UNRESOLVED and _HEX64.fullmatch(choice) is None
    ):
        return None, bad_request("choice must be a candidate hash or unresolved")
    return {**data}, None


def _link_guards(data: dict):
    """The validated manual link fields, or ``(None, error)``."""
    error = _guard_error(data, _LINK_FIELDS)
    if error is not None:
        return None, error
    if data["capture_token"] is None:
        return None, bad_request("A link needs a capture of the file")
    positive, pair = data["positive_id"], data["pair_id"]
    if type(positive) is not int or positive < 1:
        return None, bad_request("positive_id must be a positive integer")
    if pair is not None and (type(pair) is not int or pair < 1):
        return None, bad_request("pair_id must be a positive integer or null")
    return {**data}, None
