"""System › Integrations test hooks for the browser tests (#249 System S10).

A fake LoraManager install lives under this server's temp root; nothing ever
reaches a real LoraManager, ComfyUI install or the network:

- ``POST /__test__/integrations/reset`` turns the integration off (no path,
  no key, no trigger words), removes the fake install, its LoRA folder and
  the image cache, forgets every LoRA import and injected failure, and opens
  the download gate;
- ``POST /__test__/integrations/lora-manager``
  ``{loras, civitai, preview, previous, settings}`` writes
  ``custom_nodes/ComfyUI-Lora-Manager`` (detected through the
  ``folder_paths`` stub's base path) and ``loras`` LoRAs under
  ``models/loras``: each a ``<stem>.safetensors`` model beside its
  ``<stem>.metadata.json`` sidecar (LoraManager's alongside layout, #355)
  and, unless ``preview`` is false, a local preview PNG; ``civitai`` adds one
  CivitAI example image per LoRA (served by the stub opener below; an import
  downloads it only for a LoRA without a local preview); ``previous`` saves
  that many earlier ``lora-manager`` imports with tags and image links;
  ``settings: "invalid"`` makes LoraManager's settings file unreadable JSON
  (the server's own isolated settings folder, ``LORA_MANAGER_SETTINGS_DIR``);
- ``POST /__test__/integrations/gate`` ``{hold}`` holds every CivitAI
  download until it is released (``{"hold": false}``), so a test sees the job
  while it downloads;
- ``POST /__test__/integrations/fail-import`` makes the next collection
  replacement fail (a database error after staging);
- ``POST /__test__/integrations/pair`` saves a pair whose positive is the first
  imported prompt (a saved pair that blocks the replacement);
- ``GET /__test__/integrations/state`` answers the imported prompt texts,
  whether a CivitAI key is saved and the downloads served.

CivitAI downloads are always answered by a stub opener (a small PNG), so no
test ever opens a network connection.
"""

import io
import json
import os
import shutil
import threading
from pathlib import Path

from aiohttp import web
from PIL import Image

MAX_LORAS = 20
CATEGORY = "lora-manager"


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "#3a6ea5").save(buffer, "PNG")
    return buffer.getvalue()


class _Response:
    def __init__(self, body):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))}

    def read(self, amount=-1):
        return self._body.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def register_integrations_commands(app, routes, api, root: Path):
    from py import lora_utils
    from py.config import IntegrationConfig
    from utils.hashing import generate_prompt_hash

    lora_manager = root / "custom_nodes" / "ComfyUI-Lora-Manager"
    loras = root / "models" / "loras"
    gate = threading.Event()
    gate.set()
    served = []
    failures = {"replace": 0}
    pairs = []  # the blocking pairs this fixture saved
    real_replace = type(api.db).replace_category_prompts

    def stub_open(request, timeout=10):
        served.append(request.full_url)
        gate.wait(30)
        return _Response(_png())

    lora_utils._open_url = stub_open

    def replace(db, *args, **kwargs):
        if failures["replace"] > 0:
            failures["replace"] -= 1
            raise RuntimeError("Injected replacement failure")
        return real_replace(db, *args, **kwargs)

    type(api.db).replace_category_prompts = replace

    def settings_file():
        return Path(os.environ["LORA_MANAGER_SETTINGS_DIR"]) / "settings.json"

    def reset():
        settings_file().unlink(missing_ok=True)
        IntegrationConfig.LORA_MANAGER_ENABLED = False
        IntegrationConfig.LORA_MANAGER_PATH = ""
        IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED = False
        IntegrationConfig.CIVITAI_API_KEY = ""
        lora_utils.get_trigger_cache().clear()
        gate.set()
        served.clear()
        failures["replace"] = 0
        cache = Path(lora_utils.get_lora_image_cache_dir(create=False))
        for folder in (lora_manager, loras, cache):
            shutil.rmtree(folder, ignore_errors=True)
        for pair_id in pairs:
            api.db.delete_prompt_pair(pair_id)
        pairs.clear()
        real_replace(api.db, CATEGORY, [], allow_empty=True)

    @routes.post("/__test__/integrations/reset")
    async def integrations_reset(request):
        reset()
        return web.json_response({"success": True})

    @routes.post("/__test__/integrations/lora-manager")
    async def make_lora_manager(request):
        data = await request.json() if request.can_read_body else {}
        count = data.get("loras", 2)
        previous = data.get("previous", 0)
        preview = data.get("preview", True)
        settings = data.get("settings")
        for value in (count, previous):
            if isinstance(value, bool) or not isinstance(value, int):
                raise web.HTTPBadRequest()
        if not 0 <= count <= MAX_LORAS or not 0 <= previous <= MAX_LORAS:
            raise web.HTTPBadRequest()
        if not isinstance(preview, bool) or settings not in (None, "invalid"):
            raise web.HTTPBadRequest()
        if settings == "invalid":
            settings_file().write_text("{not json", encoding="utf-8")
        (lora_manager / "py").mkdir(parents=True, exist_ok=True)
        (lora_manager / "__init__.py").write_text("")
        loras.mkdir(parents=True, exist_ok=True)
        for number in range(count):
            stem = f"style_{number + 1:02}"
            civitai = {"trainedWords": [f"trigger{number + 1}"]}
            if data.get("civitai") is True:
                civitai["images"] = [
                    {
                        "url": f"https://image.civitai.com/x/{stem}.jpeg",
                        "meta": {"prompt": f"{stem} example prompt"},
                    }
                ]
            meta = {
                "file_name": f"{stem}.safetensors",
                "model_name": f"Style {number + 1:02}",
                "civitai": civitai,
            }
            (loras / f"{stem}.safetensors").write_bytes(b"model")
            (loras / f"{stem}.metadata.json").write_text(json.dumps(meta))
            if preview:
                (loras / f"{stem}.png").write_bytes(_png())
        for number in range(previous):
            text = f"previous lora import {number + 1}"
            prompt_id = api.db.save_prompt(
                text=text,
                category=CATEGORY,
                tags=[CATEGORY, f"lora:previous-{number + 1}"],
                prompt_hash=generate_prompt_hash(text),
            )
            api.db.link_image_to_prompt(str(prompt_id), str(loras / "old.png"))
        return web.json_response({"success": True})

    @routes.post("/__test__/integrations/gate")
    async def download_gate(request):
        data = await request.json()
        if not isinstance(data.get("hold"), bool):
            raise web.HTTPBadRequest()
        if data["hold"]:
            gate.clear()
        else:
            gate.set()
        return web.json_response({"success": True})

    @routes.post("/__test__/integrations/fail-import")
    async def fail_import(request):
        failures["replace"] = 1
        return web.json_response({"success": True})

    @routes.post("/__test__/integrations/pair")
    async def pair(request):
        with api.db.model.get_connection() as conn:
            row = conn.execute(
                "SELECT text FROM prompts WHERE category = ? ORDER BY id LIMIT 1",
                (CATEGORY,),
            ).fetchone()
        if row is None:
            raise web.HTTPConflict()
        pairs.append(api.db.save_prompt_pair(row[0], "blurry"))
        return web.json_response({"success": True})

    @routes.get("/__test__/integrations/state")
    async def state(request):
        imports = api.db.search_prompts(category=CATEGORY, limit=MAX_LORAS * 2)
        return web.json_response(
            {
                "imports": sorted(prompt["text"] for prompt in imports),
                "key_saved": bool(IntegrationConfig.CIVITAI_API_KEY),
                "enabled": IntegrationConfig.LORA_MANAGER_ENABLED,
                "trigger_words": IntegrationConfig.LORA_TRIGGER_WORDS_ENABLED,
                "downloads": len(served),
            }
        )

    return reset
