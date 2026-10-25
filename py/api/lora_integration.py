"""LoraManager integration API routes for PromptManager."""

import asyncio
import json

from aiohttp import web

from ..config_store import ConfigUnreadable, RevisionConflict, get_config_store
from .jobs import LORA_IMPORT_JOB_KIND

# Flags of POST /lora/enable; each must be a JSON boolean when present.
_LORA_FLAGS = ("enabled", "trigger_words_enabled", "clear_civitai_api_key")

_LORA_PATH_ERROR = (
    "LoraManager path must be a directory under ComfyUI's custom_nodes "
    "whose name contains 'lora'"
)


def _lora_settings_mutation(values):
    """The config commit for a LoRA save: only ``integrations.lora_manager``.

    The submitted keys replace their values; other LoRA keys, other
    integrations and every other section stay as they are on disk.
    """

    def mutate(config):
        integrations = config.get("integrations")
        integrations = dict(integrations) if isinstance(integrations, dict) else {}
        lora = integrations.get("lora_manager")
        lora = dict(lora) if isinstance(lora, dict) else {}
        lora.update(values)
        integrations["lora_manager"] = lora
        config["integrations"] = integrations
        return config

    return mutate


def _lora_error_frame(message):
    """An import stream's error event, as the admin page's modal reads it.

    The modal shows ``status`` and ``progress`` from every frame and closes
    on ``type: error``, naming ``message``.
    """
    return {"type": "error", "status": message, "progress": 0, "message": message}


class LoraIntegrationMixin:
    """Mixin providing LoraManager detection, scanning, and trigger word endpoints."""

    def _register_lora_routes(self, routes):
        @routes.get("/prompt_manager/lora/detect")
        async def lora_detect_route(request):
            return await self.lora_detect(request)

        @routes.get("/prompt_manager/lora/status")
        async def lora_status_route(request):
            return await self.lora_status(request)

        @routes.post("/prompt_manager/lora/enable")
        async def lora_enable_route(request):
            return await self.lora_enable(request)

        @routes.post("/prompt_manager/lora/scan")
        async def lora_scan_route(request):
            return await self.lora_scan(request)

        @routes.get("/prompt_manager/lora/trigger-words")
        async def lora_trigger_words_route(request):
            return await self.lora_trigger_words(request)

        @routes.post("/prompt_manager/lora/refresh-cache")
        async def lora_refresh_cache_route(request):
            return await self.lora_refresh_cache(request)

    def _error_response(self, exc: Exception, status: int = 500):
        return web.json_response(
            {"success": False, "error": self._public_error(exc)}, status=status
        )

    @staticmethod
    def _bad_lora_request(message: str):
        return web.json_response({"success": False, "error": message}, status=400)

    # ── Detection ────────────────────────────────────────────────────

    async def lora_detect(self, request):
        """Auto-detect LoraManager installation."""
        try:
            from ..lora_utils import detect_lora_manager

            path = await self._run_in_executor(detect_lora_manager)
            return web.json_response(
                {
                    "success": True,
                    "detected": path is not None,
                    "path": self._public_path(path) if path else "",
                }
            )
        except Exception as e:
            self.logger.error(f"LoraManager detection failed: {e}")
            return self._error_response(e)

    # ── Status ───────────────────────────────────────────────────────

    async def lora_status(self, request):
        """Get current LoraManager integration status."""
        try:
            from ..config import IntegrationConfig
            from ..lora_utils import detect_lora_manager, get_trigger_cache

            config = IntegrationConfig.get_config()["lora_manager"]
            cache = get_trigger_cache()

            # Check if the configured path is still valid
            detected_path = await self._run_in_executor(
                detect_lora_manager, config.get("path", "")
            )

            return web.json_response(
                {
                    "success": True,
                    "enabled": config["enabled"],
                    "path": self._public_path(config["path"]),
                    "trigger_words_enabled": config["trigger_words_enabled"],
                    "has_civitai_api_key": bool(config.get("civitai_api_key")),
                    "detected": detected_path is not None,
                    "detected_path": (
                        self._public_path(detected_path) if detected_path else ""
                    ),
                    "trigger_cache_loaded": cache.is_loaded,
                }
            )
        except Exception as e:
            self.logger.error(f"LoraManager status check failed: {e}")
            return self._error_response(e)

    # ── Enable / Disable ─────────────────────────────────────────────

    async def _validated_lora_manager_path(self, path):
        """Canonical LoraManager path for ``path`` or ``(None, error_response)``.

        The user's path (or, when empty, the auto-detected install) must be a
        LoraManager-looking directory that is a direct child of ComfyUI's
        ``custom_nodes`` with ``lora`` in its name; the trees under it are
        later served as images, so nothing outside custom_nodes may be used.
        """
        from ..lora_utils import detect_lora_manager, resolve_lora_manager_path

        candidate = ""
        if path:
            candidate = await self._run_in_executor(resolve_lora_manager_path, path)
            if candidate is None:
                return None, self._bad_lora_request(_LORA_PATH_ERROR)

        resolved = await self._run_in_executor(detect_lora_manager, candidate)
        if not resolved:
            return None, self._bad_lora_request(
                "LoraManager not found at the specified path"
            )

        allowed = await self._run_in_executor(resolve_lora_manager_path, resolved)
        if allowed is None:
            return None, self._bad_lora_request(_LORA_PATH_ERROR)
        return allowed, None

    async def lora_enable(self, request):
        """Enable or disable LoraManager integration and save it to config.json.

        The values are committed through the config store first (#220) and
        published at runtime only once config.json holds them; a save that
        cannot be written reports ``persisted: false`` and changes nothing.
        The CivitAI key is never echoed back.
        """
        try:
            data, error = await self._read_json_body(request)
            if error is not None:
                return error
            for flag in _LORA_FLAGS:
                if flag in data and not isinstance(data[flag], bool):
                    return self._bad_lora_request(f"{flag} must be true or false")
            enabled = data.get("enabled", False)
            path = data.get("path", "")
            trigger_words = data.get("trigger_words_enabled", False)

            from ..config import IntegrationConfig
            from ..lora_utils import get_trigger_cache

            # The key is never echoed back to the UI, so a blank or absent
            # field means "unchanged"; clearing is an explicit request.
            civitai_key = IntegrationConfig.CIVITAI_API_KEY
            submitted_key = data.get("civitai_api_key")
            if submitted_key is not None and not isinstance(submitted_key, str):
                return self._bad_lora_request("civitai_api_key must be a string")
            if data.get("clear_civitai_api_key"):
                civitai_key = ""
            elif submitted_key and submitted_key.strip():
                civitai_key = submitted_key.strip()

            if not isinstance(path, str):
                return self._bad_lora_request("path must be a string")

            # If enabling, validate the path
            if enabled:
                path, error = await self._validated_lora_manager_path(path)
                if error is not None:
                    return error

            values = {
                "enabled": enabled,
                "path": path,
                "trigger_words_enabled": trigger_words,
                "civitai_api_key": civitai_key,
            }

            def publish(_config):
                # Under the commit lock, so the last save written is the one
                # the runtime keeps.
                IntegrationConfig.LORA_MANAGER_ENABLED = enabled
                IntegrationConfig.LORA_MANAGER_PATH = path
                IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED = trigger_words
                IntegrationConfig.CIVITAI_API_KEY = civitai_key

            try:
                _saved, revision = await self._run_in_executor(
                    get_config_store().commit,
                    _lora_settings_mutation(values),
                    expected_revision=None,
                    publish=publish,
                )
            except (ConfigUnreadable, RevisionConflict, OSError) as exc:
                return self._config_commit_failure(exc)

            cache = get_trigger_cache()
            if enabled and trigger_words and path:
                try:
                    count = await self._run_in_executor(cache.load, path)
                    self.logger.info(f"Trigger word cache loaded: {count} LoRAs")
                except Exception as e:
                    # Saved and enabled; only the trigger words are missing.
                    self.logger.warning(f"Trigger word cache could not be loaded: {e}")
                    cache.clear()
            elif not enabled:
                cache.clear()

            return web.json_response(
                {
                    "success": True,
                    "enabled": enabled,
                    "path": self._public_path(path),
                    "trigger_words_enabled": trigger_words,
                    "persisted": True,
                    "revision": revision,
                    "runtime_applied": True,
                    "cache_loaded": cache.is_loaded,
                }
            )
        except Exception as e:
            self.logger.error(f"LoraManager enable/disable failed: {e}")
            return self._error_response(e)

    # ── Scan LoRA example images ─────────────────────────────────────

    async def lora_scan(self, request):
        """Scan LoraManager metadata and import LoRA info + preview images.

        Streams progress as SSE, matching the existing scan pattern. Errors
        raised once the stream is open are reported as an ``error`` event.
        """
        try:
            from ..config import IntegrationConfig
            from ..lora_utils import resolve_lora_manager_path

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return self._bad_lora_request("LoraManager integration is not enabled")

            lm_path = IntegrationConfig.LORA_MANAGER_PATH
            if not lm_path:
                return self._bad_lora_request("LoraManager path not configured")

            allowed = await self._run_in_executor(resolve_lora_manager_path, lm_path)
            if allowed is None:
                return self._bad_lora_request(_LORA_PATH_ERROR)

            response = web.StreamResponse(
                status=200,
                reason="OK",
                headers={"Content-Type": "text/event-stream"},
            )

            async def send_progress(data):
                line = f"data: {json.dumps(data)}\n\n"
                await response.write(line.encode("utf-8"))

            # The import replaces prompts and images: a records hold, so it
            # never runs beside a scan, restore or tagging job.
            busy = self._jobs.blocker(LORA_IMPORT_JOB_KIND, admission="records")
            if busy is not None:
                await response.prepare(request)
                message = f"{busy.title} already running"
                await send_progress(_lora_error_frame(message))
                await response.write_eof()
                return response

            async with self._jobs.hold(
                LORA_IMPORT_JOB_KIND, "LoRA import (admin page)", admission="records"
            ) as job:
                await response.prepare(request)
                try:
                    await self._lora_scan_stream(allowed, send_progress)
                except Exception as e:
                    self.logger.exception("LoRA scan failed")
                    job.finish("failed")
                    await send_progress(_lora_error_frame(self._public_error(e)))

                await response.write_eof()
            return response

        except Exception as e:
            self.logger.error(f"LoRA scan failed: {e}")
            return self._error_response(e)

    async def _lora_scan_stream(self, lm_path, send_progress):
        """Import every LoRA under ``lm_path``, reporting through ``send_progress``.

        The staged path of the System import (#221, ``lora_import_routes``):
        discover, stage every record (downloads into the cache first), then
        replace the collection in one transaction. A failure at any stage
        raises with the previous imports untouched; an empty replacement of a
        non-empty collection is refused (it needs the reviewed import).
        """
        from .. import lora_import

        await send_progress(
            {
                "type": "progress",
                "status": "Finding LoRA directories...",
                "progress": 2,
            }
        )
        files = await self._run_in_executor(lora_import.discover, lm_path)
        total = len(files)
        await send_progress(
            {
                "type": "progress",
                "status": f"Found {total} LoRA metadata files",
                "progress": 5,
                "total": total,
            }
        )

        events = asyncio.Queue()
        state = await self._run_in_executor(
            self.db.category_import_state, lora_import.LORA_CATEGORY
        )
        # Records whose prompt already exists outside the collection are
        # linked, not imported (the running counts; the end ones are exact).
        known = set()

        def on_record(index, record, _staged):
            events.put_nowait((index, record))

        counts = {"imported": 0}

        async def report(index, record):
            if record is not None and record["hash"] not in known:
                known.add(record["hash"])
                existing = await self._run_in_executor(
                    self.db.existing_prompt_hashes,
                    [record["hash"]],
                    lora_import.LORA_CATEGORY,
                )
                if not existing:
                    counts["imported"] += 1
            status = record["name"] if record else files[index].name
            if record and record["images"]:
                status += f" ({len(record['images'])} images)"
            await send_progress(
                {
                    "type": "progress",
                    "status": status,
                    "progress": int(5 + (90 * (index + 1) / max(total, 1))),
                    "processed": index + 1,
                    "imported": counts["imported"],
                    "skipped": index + 1 - counts["imported"],
                }
            )

        # Each record's event is queued (through the loop) before staging
        # ends, so reporting until staging is done and then draining the
        # queue reports every record in order.
        staging = asyncio.ensure_future(
            self.stage_lora_import(files, on_record=on_record)
        )
        while not staging.done():
            getter = asyncio.ensure_future(events.get())
            await asyncio.wait({getter, staging}, return_when=asyncio.FIRST_COMPLETED)
            if getter.done():
                await report(*getter.result())
            else:
                getter.cancel()
        while not events.empty():
            await report(*events.get_nowait())
        staged = await staging

        await send_progress(
            {
                "type": "progress",
                "status": "Replacing previous lora-manager imports...",
                "progress": 96,
            }
        )
        result = await self.replace_lora_imports(
            self.db, staged, expected_ids=state["ids"], allow_empty=False
        )
        await send_progress(
            {
                "type": "complete",
                "progress": 100,
                "total": total,
                "imported": result["imported"],
                "skipped": total - result["imported"],
            }
        )

    # ── Trigger word endpoints ───────────────────────────────────────

    async def lora_trigger_words(self, request):
        """Look up trigger words for a specific LoRA name."""
        try:
            from ..config import IntegrationConfig
            from ..lora_utils import get_trigger_cache

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return self._bad_lora_request("LoraManager integration not enabled")

            lora_name = request.query.get("name", "")
            if not lora_name:
                return self._bad_lora_request("Missing 'name' query parameter")

            cache = get_trigger_cache()
            if not cache.is_loaded:
                lm_path = IntegrationConfig.LORA_MANAGER_PATH
                if lm_path:
                    await self._run_in_executor(cache.load, lm_path)

            words = cache.get_trigger_words(lora_name)
            return web.json_response(
                {"success": True, "lora": lora_name, "trigger_words": words}
            )
        except Exception as e:
            self.logger.error(f"Trigger word lookup failed: {e}")
            return self._error_response(e)

    async def lora_refresh_cache(self, request):
        """Force-refresh the trigger word cache from disk."""
        try:
            from ..config import IntegrationConfig
            from ..lora_utils import get_trigger_cache

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return self._bad_lora_request("LoraManager integration not enabled")

            lm_path = IntegrationConfig.LORA_MANAGER_PATH
            if not lm_path:
                return self._bad_lora_request("LoraManager path not configured")

            cache = get_trigger_cache()
            count = await self._run_in_executor(cache.load, lm_path)
            return web.json_response(
                {"success": True, "loras_with_trigger_words": count}
            )
        except Exception as e:
            self.logger.error(f"Trigger cache refresh failed: {e}")
            return self._error_response(e)
