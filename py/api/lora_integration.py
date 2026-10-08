"""LoraManager integration API routes for PromptManager."""

import json
from pathlib import Path

from aiohttp import web

_LORA_PATH_ERROR = (
    "LoraManager path must be a directory under ComfyUI's custom_nodes "
    "whose name contains 'lora'"
)


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
        """Enable or disable LoraManager integration and save to config.json."""
        try:
            data = await request.json()
            enabled = data.get("enabled", False)
            path = data.get("path", "")
            trigger_words = data.get("trigger_words_enabled", False)

            from ..config import IntegrationConfig, PromptManagerConfig
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

            # Update in-memory config
            IntegrationConfig.LORA_MANAGER_ENABLED = enabled
            IntegrationConfig.LORA_MANAGER_PATH = path
            IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED = trigger_words
            IntegrationConfig.CIVITAI_API_KEY = civitai_key

            # Persist to the configured config.json
            PromptManagerConfig.save_to_file()

            # Load trigger word cache if enabling
            cache = get_trigger_cache()
            if enabled and trigger_words and path:
                count = await self._run_in_executor(cache.load, path)
                self.logger.info(f"Trigger word cache loaded: {count} LoRAs")
            elif not enabled:
                cache.clear()

            return web.json_response(
                {
                    "success": True,
                    "enabled": enabled,
                    "path": self._public_path(path),
                    "trigger_words_enabled": trigger_words,
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
            await response.prepare(request)

            async def send_progress(data):
                line = f"data: {json.dumps(data)}\n\n"
                await response.write(line.encode("utf-8"))

            try:
                await self._lora_scan_stream(allowed, send_progress)
            except Exception as e:
                self.logger.exception("LoRA scan failed")
                await send_progress({"type": "error", "message": self._public_error(e)})

            await response.write_eof()
            return response

        except Exception as e:
            self.logger.error(f"LoRA scan failed: {e}")
            return self._error_response(e)

    async def _lora_scan_stream(self, lm_path, send_progress):
        """Import every LoRA under ``lm_path``, reporting through ``send_progress``."""
        from ..config import IntegrationConfig
        from ..lora_utils import (
            download_civitai_images,
            find_lora_directories,
            get_example_prompt_from_metadata,
            get_lora_image_cache_dir,
            get_preview_images_from_metadata,
            get_trigger_words_from_metadata,
            get_model_name_from_metadata,
            read_lora_metadata,
        )

        await send_progress(
            {
                "type": "progress",
                "status": "Clearing previous lora-manager imports...",
                "progress": 0,
            }
        )

        # Clear previous imports so reimport is always clean
        await self._run_in_executor(self.db.delete_prompts_by_category, "lora-manager")

        await send_progress(
            {
                "type": "progress",
                "status": "Finding LoRA directories...",
                "progress": 2,
            }
        )

        lora_dirs = await self._run_in_executor(find_lora_directories, lm_path)

        # Collect all metadata files
        meta_files = []
        for d in lora_dirs:
            dir_path = Path(d)
            meta_files.extend(dir_path.rglob("*.metadata.json"))

        total = len(meta_files)
        imported = 0
        skipped = 0

        await send_progress(
            {
                "type": "progress",
                "status": f"Found {total} LoRA metadata files",
                "progress": 5,
                "total": total,
            }
        )

        cache_dir = get_lora_image_cache_dir()

        for i, meta_file in enumerate(meta_files):
            metadata = await self._run_in_executor(read_lora_metadata, meta_file)
            if not metadata:
                skipped += 1
                continue

            model_name = get_model_name_from_metadata(metadata)
            trigger_words = get_trigger_words_from_metadata(metadata)

            # Collect all images: local previews + downloaded civitai examples
            preview_paths = await self._run_in_executor(
                get_preview_images_from_metadata, metadata, meta_file
            )
            civitai_paths = await self._run_in_executor(
                download_civitai_images,
                metadata,
                meta_file,
                cache_dir,
                IntegrationConfig.CIVITAI_API_KEY,
            )

            # Merge, local first, dedup
            seen = set(preview_paths)
            all_images = list(preview_paths)
            for cp in civitai_paths:
                if cp not in seen:
                    all_images.append(cp)
                    seen.add(cp)

            # Build prompt text: prefer example prompt, then model name
            example_prompt = get_example_prompt_from_metadata(metadata)
            prompt_text = example_prompt or model_name

            # Build tags
            tags = ["lora-manager", f"lora:{model_name}"]
            tags.extend(trigger_words)

            # Save to database via existing mechanism
            try:
                import hashlib

                prompt_hash = hashlib.sha256(
                    prompt_text.strip().lower().encode("utf-8")
                ).hexdigest()

                existing = await self._run_in_executor(
                    self.db.get_prompt_by_hash, prompt_hash
                )

                if existing:
                    # Link all images
                    for pp in all_images:
                        await self._run_in_executor(
                            self.db.link_image_to_prompt,
                            existing["id"],
                            pp,
                        )
                    skipped += 1
                else:
                    prompt_id = await self._run_in_executor(
                        self.db.save_prompt,
                        prompt_text,
                        "lora-manager",  # category
                        tags,
                        None,  # rating
                        None,  # notes
                        prompt_hash,
                    )

                    if prompt_id:
                        for pp in all_images:
                            await self._run_in_executor(
                                self.db.link_image_to_prompt,
                                prompt_id,
                                pp,
                            )
                        imported += 1
                    else:
                        skipped += 1

            except Exception as e:
                self.logger.warning(f"Failed to import LoRA {model_name}: {e}")
                skipped += 1

            # Progress update for every LoRA
            progress = int(5 + (90 * (i + 1) / max(total, 1)))
            img_count = len(all_images)
            status = f"{model_name}"
            if img_count:
                status += f" ({img_count} images)"
            await send_progress(
                {
                    "type": "progress",
                    "status": status,
                    "progress": progress,
                    "processed": i + 1,
                    "imported": imported,
                    "skipped": skipped,
                }
            )

        await send_progress(
            {
                "type": "complete",
                "progress": 100,
                "total": total,
                "imported": imported,
                "skipped": skipped,
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
