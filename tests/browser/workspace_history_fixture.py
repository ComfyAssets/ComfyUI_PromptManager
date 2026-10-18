"""Real disposable ledger commands and physical files for history workflows."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from aiohttp import web
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.browser.workspace_visual_fixture import _write_art, seed_workspace_visual
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


class _HistoryCommands:
    """One disposable fixture lifetime and one bounded publication barrier."""

    def __init__(self, api: PromptManagerAPI, root: Path) -> None:
        self.api = api
        self.root = root
        self.manifest = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.restore_seam = None
        import folder_paths

        self.original_output = folder_paths.get_output_directory

    def reset(self, manifest: dict | None = None) -> None:
        self.disarm()
        import folder_paths

        folder_paths.get_output_directory = self.original_output
        self.manifest = manifest

    def disarm(self) -> None:
        self.release.set()
        if self.restore_seam:
            module, name, original = self.restore_seam
            setattr(module, name, original)
            self.restore_seam = None

    def arm(self, boundary: str) -> None:
        from py.api import history
        from py.api import inspection_service

        self.disarm()
        self.entered = threading.Event()
        self.release = threading.Event()
        name = {
            "window": "_serialize_history",
            "detail": "_serialize_history",
            "candidate": "resolve_current_candidate",
            "inspection": "read_inspection_file",
            "media": "_prepare_current_media",
        }[boundary]
        module = inspection_service if boundary == "inspection" else history
        original = getattr(module, name)
        entered, release = self.entered, self.release

        def held(*args: object, **kwargs: object) -> object:
            value = original(*args, **kwargs)
            setattr(module, name, original)
            entered.set()
            try:
                if not release.wait(10):
                    raise TimeoutError("Owned history publication barrier timed out")
                return value
            except BaseException:
                if boundary == "media":
                    value.close()
                raise

        self.restore_seam = module, name, original
        setattr(module, name, held)


def register_workspace_history_commands(
    routes: web.RouteTableDef, api: PromptManagerAPI, root: Path
) -> _HistoryCommands:
    """Register fixed commands; all files and namespaces belong to this server."""
    state = _HistoryCommands(api, root)
    api._workspace_history_commands = state

    @routes.post("/__test__/history/commands")
    async def command(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else None
        actions = {"release", "replace-file", "reconfigure-root"}
        if (
            not isinstance(data, dict)
            or type(data.get("action")) is not str
            or ("boundary" in data and type(data["boundary"]) is not str)
            or not (
                (set(data) == {"action"} and data["action"] in actions)
                or (
                    set(data) == {"action", "boundary"}
                    and data["action"] == "arm"
                    and data["boundary"]
                    in {"window", "detail", "candidate", "inspection", "media"}
                )
            )
        ):
            raise web.HTTPBadRequest()
        if state.manifest is None:
            raise web.HTTPConflict()
        action = data["action"]
        if action == "arm":
            state.arm(data["boundary"])
        elif action == "release":
            state.disarm()
        elif action == "replace-file":
            replacement = root / "output" / "history" / "replacement.png"
            with Image.new("RGB", (160, 100), (225, 30, 40)) as image:
                image.save(replacement)
            replacement.replace(root / "output" / "history" / "current.png")
        elif action == "reconfigure-root":
            import folder_paths

            moved = root / "owned-reconfigured-output"
            moved.mkdir(exist_ok=True)
            folder_paths.get_output_directory = lambda: str(moved)
        return web.json_response({"success": True})

    @routes.get("/__test__/history/checkpoint")
    async def checkpoint(request: web.Request) -> web.Response:
        if state.manifest is None:
            raise web.HTTPConflict()
        return web.json_response({"entered": state.entered.is_set()})

    @routes.get("/__test__/history/snapshot")
    async def snapshot(request: web.Request) -> web.Response:
        if state.manifest is None or request.query:
            raise web.HTTPConflict()
        path = root / "owned-history-snapshot.db"
        created = await api._run_in_executor(api.db.model.backup_database, str(path))
        if not created:
            raise web.HTTPInternalServerError()
        return web.Response(
            body=path.read_bytes(), content_type="application/octet-stream"
        )

    @routes.get("/__test__/history/reused-snapshot")
    async def reused_snapshot(request: web.Request) -> web.Response:
        if state.manifest is None or request.query:
            raise web.HTTPConflict()
        from database.operations import PromptDatabase
        from utils.hashing import generate_prompt_hash

        path = root / "owned-history-reused.db"

        def create() -> bytes:
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            database = PromptDatabase(str(path))
            try:
                target = state.manifest["detail_id"]
                with database.model.get_connection() as connection:
                    text = "Restored owner with reused numeric ID"
                    connection.execute(
                        "INSERT INTO prompts(id,text,hash) VALUES(?,?,?)",
                        (target, text, generate_prompt_hash(text)),
                    )
                database.save_prompt_pair(text, "Restored saved negative")
            finally:
                database.close_all()
            return path.read_bytes()

        return web.Response(
            body=await api._run_in_executor(create),
            content_type="application/octet-stream",
        )

    async def cleanup(application: web.Application) -> None:
        state.reset()

    api_app = __import__("server").PromptServer.instance.app
    api_app.on_cleanup.append(cleanup)
    return state


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
    api._workspace_history_commands.reset()
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
    canonical_positive = (
        "Layered sandstone arches, warm afternoon light, precise silhouettes."
    )
    canonical_negative = "blurred edges, watermark"
    canonical_pair = api.db.save_prompt_pair(
        canonical_positive, canonical_negative, title="Quality baseline"
    )
    canonical_id = api.db.get_prompt_pair(canonical_pair)["positive_prompt_id"]
    api.db.save_prompt_pair(canonical_positive, "flat light", title="Clean geometry")
    api.db.save_prompt_pair(canonical_positive, "", title="No negative")
    for number, name in enumerate(("canonical.png", "canonical-alt.png")):
        path = folder / name
        _write_art(path, number, canonical_positive, canonical_negative)
        with Image.open(path) as source:
            graph = json.loads(source.info["prompt"])
            graph["3"]["inputs"].update(
                seed=42001 + number,
                steps=28,
                cfg=6.5,
                sampler_name="euler",
                scheduler="normal",
            )
            metadata = PngInfo()
            metadata.add_text("prompt", json.dumps(graph))
            source.save(path, pnginfo=metadata)
        stamp = 1_760_000_000 - number * 60
        os.utime(path, (stamp, stamp))
        image_id = api.db.link_image_to_prompt(canonical_id, str(path))
        api.db.link_image_to_pair(canonical_pair, image_id, {"source": "fixture"})
    canonical_run = run(
        (
            RecipeContext(
                "10",
                _snapshot(canonical_positive, " + frozen composition"),
                _snapshot(canonical_negative),
                "inferred",
            ),
        ),
        OutputBundle(
            (
                reference("10", 0, "canonical.png"),
                reference("10", 1, "canonical-alt.png"),
            ),
            "complete",
            "reported",
        ),
    )
    merge_duplicate_id = api.db.save_prompt(DETAIL_POSITIVE)
    api.invalidate_gallery_cache()
    manifest = {
        "positive_id": positive_id,
        "detail_id": detail_id,
        "aab": aab,
        "runs": detail_runs,
        "saved_pair": saved_pair,
        "detail_image_id": current,
        "canonical_id": canonical_id,
        "canonical_pair": canonical_pair,
        "canonical_run": canonical_run,
        "canonical_positive": canonical_positive,
        "canonical_negative": canonical_negative,
        "merge_duplicate_id": merge_duplicate_id,
    }
    api._workspace_history_commands.reset(manifest)
    return manifest
