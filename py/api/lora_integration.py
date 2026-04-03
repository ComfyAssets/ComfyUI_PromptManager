"""LoraManager integration API routes for PromptManager."""

import json
import os
from pathlib import Path

from aiohttp import web


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
                    "path": path or "",
                }
            )
        except Exception as e:
            self.logger.error(f"LoraManager detection failed: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

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
                    "path": config["path"],
                    "trigger_words_enabled": config["trigger_words_enabled"],
                    "detected": detected_path is not None,
                    "detected_path": detected_path or "",
                    "trigger_cache_loaded": cache.is_loaded,
                }
            )
        except Exception as e:
            self.logger.error(f"LoraManager status check failed: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    # ── Enable / Disable ─────────────────────────────────────────────

    async def lora_enable(self, request):
        """Enable or disable LoraManager integration and save to config.json."""
        try:
            data = await request.json()
            enabled = data.get("enabled", False)
            path = data.get("path", "")
            trigger_words = data.get("trigger_words_enabled", False)

            from ..config import IntegrationConfig, PromptManagerConfig
            from ..lora_utils import detect_lora_manager, get_trigger_cache

            # If enabling, validate the path
            if enabled:
                resolved = await self._run_in_executor(detect_lora_manager, path)
                if not resolved:
                    return web.json_response(
                        {
                            "success": False,
                            "error": "LoraManager not found at the specified path",
                        },
                        status=400,
                    )
                path = resolved

            # Update in-memory config
            IntegrationConfig.LORA_MANAGER_ENABLED = enabled
            IntegrationConfig.LORA_MANAGER_PATH = path
            IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED = trigger_words

            # Persist to config.json
            config_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            config_file = os.path.join(config_dir, "config.json")
            PromptManagerConfig.save_to_file(config_file)

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
                    "path": path,
                    "trigger_words_enabled": trigger_words,
                }
            )
        except Exception as e:
            self.logger.error(f"LoraManager enable/disable failed: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    # ── Scan LoRA example images ─────────────────────────────────────

    async def lora_scan(self, request):
        """Scan LoraManager metadata and import LoRA info + preview images.

        Streams progress as SSE, matching the existing scan pattern.
        """
        try:
            from ..config import IntegrationConfig
            from ..lora_utils import (
                find_lora_directories,
                get_example_prompt_from_metadata,
                get_preview_image_from_metadata,
                get_trigger_words_from_metadata,
                get_model_name_from_metadata,
                read_lora_metadata,
            )

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return web.json_response(
                    {
                        "success": False,
                        "error": "LoraManager integration is not enabled",
                    },
                    status=400,
                )

            lm_path = IntegrationConfig.LORA_MANAGER_PATH
            if not lm_path:
                return web.json_response(
                    {"success": False, "error": "LoraManager path not configured"},
                    status=400,
                )

            response = web.StreamResponse(
                status=200,
                reason="OK",
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)

            async def send_progress(data):
                line = f"data: {json.dumps(data)}\n\n"
                await response.write(line.encode("utf-8"))

            await send_progress(
                {
                    "type": "progress",
                    "status": "Finding LoRA directories...",
                    "progress": 0,
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

            for i, meta_file in enumerate(meta_files):
                metadata = await self._run_in_executor(read_lora_metadata, meta_file)
                if not metadata:
                    skipped += 1
                    continue

                model_name = get_model_name_from_metadata(metadata)
                trigger_words = get_trigger_words_from_metadata(metadata)
                preview_path = await self._run_in_executor(
                    get_preview_image_from_metadata, metadata, meta_file
                )

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
                        # Link preview image if we have one and it's not already linked
                        if preview_path:
                            await self._run_in_executor(
                                self.db.link_image_to_prompt,
                                existing["id"],
                                preview_path,
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

                        if prompt_id and preview_path:
                            await self._run_in_executor(
                                self.db.link_image_to_prompt,
                                prompt_id,
                                preview_path,
                            )

                        if prompt_id:
                            imported += 1
                        else:
                            skipped += 1

                except Exception as e:
                    self.logger.warning(f"Failed to import LoRA {model_name}: {e}")
                    skipped += 1

                # Progress update every 5 items or at the end
                if (i + 1) % 5 == 0 or i == total - 1:
                    progress = int(5 + (90 * (i + 1) / max(total, 1)))
                    await send_progress(
                        {
                            "type": "progress",
                            "status": f"Processing: {model_name}",
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

            await response.write_eof()
            return response

        except Exception as e:
            self.logger.error(f"LoRA scan failed: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)

    # ── Trigger word endpoints ───────────────────────────────────────

    async def lora_trigger_words(self, request):
        """Look up trigger words for a specific LoRA name."""
        try:
            from ..config import IntegrationConfig
            from ..lora_utils import get_trigger_cache

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return web.json_response(
                    {"success": False, "error": "LoraManager integration not enabled"},
                    status=400,
                )

            lora_name = request.query.get("name", "")
            if not lora_name:
                return web.json_response(
                    {"success": False, "error": "Missing 'name' query parameter"},
                    status=400,
                )

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
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def lora_refresh_cache(self, request):
        """Force-refresh the trigger word cache from disk."""
        try:
            from ..config import IntegrationConfig
            from ..lora_utils import get_trigger_cache

            if not IntegrationConfig.LORA_MANAGER_ENABLED:
                return web.json_response(
                    {"success": False, "error": "LoraManager integration not enabled"},
                    status=400,
                )

            lm_path = IntegrationConfig.LORA_MANAGER_PATH
            if not lm_path:
                return web.json_response(
                    {"success": False, "error": "LoraManager path not configured"},
                    status=400,
                )

            cache = get_trigger_cache()
            count = await self._run_in_executor(cache.load, lm_path)
            return web.json_response(
                {"success": True, "loras_with_trigger_words": count}
            )
        except Exception as e:
            self.logger.error(f"Trigger cache refresh failed: {e}")
            return web.json_response({"success": False, "error": str(e)}, status=500)
