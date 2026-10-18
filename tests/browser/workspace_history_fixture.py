"""Real disposable ledger commands and physical files for history workflows."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from PIL import Image

from tests.browser.workspace_visual_fixture import seed_workspace_visual
from utils.execution_contexts import (
    ContextBundle,
    PromptSnapshot,
    RecipeContext,
    TextEvidence,
)
from utils.execution_history_events import make_finish, make_start
from utils.execution_outputs import OutputBundle, OutputReference, snapshot_output_roots

if TYPE_CHECKING:
    from py.api import PromptManagerAPI


HISTORY_POSITIVE = (
    "Chronology A,A,B\nExact positive 猫 😀 " + "fine light " * 35
).rstrip()
DETAIL_POSITIVE = "Frozen evidence\n" + "unbroken-" * 90
LONG_NEGATIVE = "same visible negative\n" + "exact detail 猫 😀 " * 70
SESSION = "69f04366-6384-4182-b84b-117597479127"


def _known(value: str, source: str = "runtime_return") -> TextEvidence:
    provenance = "inferred" if source.startswith("graph_") else "recorded"
    return TextEvidence(value, provenance, source, None)


def _snapshot(raw: str, suffix: str = "") -> PromptSnapshot:
    return PromptSnapshot(_known(raw), _known(raw + suffix), _known(""), _known(suffix))


def capture_workspace_history_zero_outputs(api: PromptManagerAPI, run_id: str) -> bool:
    """Apply one late host capture through the normal immutable storage command."""
    run = api.db.get_execution_run(run_id)
    finish = make_finish(
        run["session_id"],
        run["queue_item_id"],
        run["storage_epoch"],
        SimpleNamespace(
            messages=[
                (
                    "execution_success",
                    {"prompt_id": run["job_id"], "timestamp": run["terminal_event_at"]},
                )
            ]
        ),
        run["observed_callback_at"],
    )
    return api.db.write_execution_outputs_for_finish(
        finish, OutputBundle((), "complete", "none_reported")
    )


def seed_workspace_history(
    api: PromptManagerAPI, root: Path, *, history_runs: int = 0
) -> dict:
    """Write actual captures; reset removes only this server's disposable rows."""
    seed_workspace_visual(api, root)
    with api.db.model.get_connection() as conn:
        for table in (
            "execution_run_outputs",
            "execution_output_capture",
            "execution_run_contexts",
            "execution_context_capture",
            "execution_runs",
            "execution_contexts",
            "execution_prompt_snapshots",
            "execution_context_texts",
        ):
            conn.execute(f"DELETE FROM {table}")
    epoch = api.db.get_execution_epoch()
    roots = snapshot_output_roots("posix", str(root / "output"), str(root / "temp"))
    folder = root / "output" / "history"
    folder.mkdir(exist_ok=True)
    for index, name in enumerate(("current.png", "other.png")):
        with Image.new("RGB", (160, 100), (50, 80 + index * 50, 110)) as image:
            image.save(folder / name)

    def reference(
        node: str, ordinal: int, name: str, kind: str = "output"
    ) -> OutputReference:
        return OutputReference(
            node,
            ordinal,
            kind,
            "history",
            name,
            "posix",
            roots.output_key if kind == "output" else roots.temp_key,
        )

    item = 0

    def run(
        contexts: tuple[RecipeContext, ...],
        outputs: OutputBundle | None = None,
        *,
        observed: int | None = None,
        conflicting: bool = False,
    ) -> str:
        nonlocal item
        job = str(uuid5(UUID(SESSION), f"job-{item}"))
        start = make_start(
            SESSION,
            ((item, job, {}, {}), item),
            epoch,
            1_780_000_000_000 + item if observed is None else observed,
        )
        api.db.write_execution_start(start)
        messages = [("execution_success", {"prompt_id": job, "timestamp": item + 1})]
        finish = make_finish(
            SESSION, item, epoch, SimpleNamespace(messages=messages), item + 10
        )
        api.db.write_execution_finish(finish)
        partial = any(
            getattr(side, field).value is None
            for context in contexts
            for side in (context.positive, context.negative)
            if side is not None
            for field in ("raw", "effective", "prepend", "append")
        )
        api.db.write_execution_contexts(
            start.run_id,
            epoch,
            ContextBundle(
                contexts,
                "partial" if partial else "complete",
                "partial" if partial else "captured",
            ),
        )
        if outputs is not None:
            api.db.write_execution_outputs_for_finish(finish, outputs)
        if conflicting:
            api.db.write_execution_contexts(
                start.run_id,
                epoch,
                ContextBundle(
                    (replace(contexts[0], negative=_snapshot("later")),),
                    "complete",
                    "captured",
                ),
            )
            api.db.write_execution_outputs_for_finish(
                finish, OutputBundle((), "complete", "none_reported")
            )
        item += 1
        return start.run_id

    pair_a = api.db.save_prompt_pair(
        HISTORY_POSITIVE, "A negative", title="Saved A", category="History"
    )
    api.db.save_prompt_pair(HISTORY_POSITIVE, "B negative", title="Saved B")
    positive_id = api.db.get_prompt_pair(pair_a)["positive_prompt_id"]
    image = api.db.link_image_to_prompt(positive_id, str(folder / "current.png"))
    api.db.link_image_to_pair(pair_a, image, {"source": "history browser fixture"})
    a = RecipeContext(
        "10", _snapshot(HISTORY_POSITIVE), _snapshot("A negative"), "inferred"
    )
    b = replace(a, negative=_snapshot("B negative"))
    aab = [run((context,)) for context in (a, a, b)]
    for number in range(history_runs):
        context = (
            a
            if number % 3 == 0
            else (
                b
                if number % 3 == 1
                else replace(b, negative=_snapshot(f"Distinct context {number:04}"))
            )
        )
        run((context,))

    saved_pair = api.db.save_prompt_pair(
        DETAIL_POSITIVE, LONG_NEGATIVE, title="Saved exact negative"
    )
    detail_id = api.db.get_prompt_pair(saved_pair)["positive_prompt_id"]
    api.db.save_prompt_pair(DETAIL_POSITIVE, "", title="Saved empty")
    current = api.db.link_image_to_prompt(detail_id, str(folder / "current.png"))
    api.db.link_image_to_pair(saved_pair, current, {"source": "fixture"})
    x = RecipeContext(
        "10",
        _snapshot(DETAIL_POSITIVE, " + composition A"),
        _snapshot(LONG_NEGATIVE, " A"),
        "inferred",
    )
    y = replace(x, negative=_snapshot(LONG_NEGATIVE, " B"))
    receipts = OutputBundle(
        (
            reference("10", 0, "current.png"),
            reference("10", 1, "current.png"),
            reference("2", 0, "other.png"),
            reference("2", 1, "missing.png"),
            reference("z", 0, "transient.png", "temp"),
        ),
        "complete",
        "reported",
    )
    detail_runs = {
        "shared": run(
            (x, replace(x, anchor_id="2"), replace(y, anchor_id="z")), receipts
        ),
        "equal_looking": run((y,)),
        "empty": run(
            (replace(x, negative=_snapshot("")),),
            OutputBundle((), "complete", "none_reported"),
        ),
        "unknown": run(
            (
                replace(
                    x,
                    negative=PromptSnapshot(
                        TextEvidence(None, "unknown", "unknown", "missing"),
                        _known("  \n\t ", "host_cache_output"),
                        _known("", "graph_literal"),
                        _known("", "graph_semantics"),
                    ),
                ),
            )
        ),
        "whitespace": run(
            (
                replace(
                    x,
                    negative=PromptSnapshot(
                        _known("  \n\t ", "graph_literal"),
                        _known("  \n\t ", "host_cache_output"),
                        _known("", "graph_literal"),
                        _known("", "graph_semantics"),
                    ),
                ),
            ),
            OutputBundle(
                (reference("1", 0, "current.png"),), "partial", "limit_reached"
            ),
        ),
        "conflicting": run(
            (x,), receipts, conflicting=True, observed=9_223_372_036_854_775_807
        ),
        "paged": run(
            (x,),
            OutputBundle(
                tuple(
                    reference("paged", ordinal, "current.png") for ordinal in range(41)
                ),
                "complete",
                "reported",
            ),
        ),
    }
    api.invalidate_gallery_cache()
    return {
        "positive_id": positive_id,
        "detail_id": detail_id,
        "aab": aab,
        "runs": detail_runs,
        "saved_pair": saved_pair,
        "detail_image_id": current,
    }
