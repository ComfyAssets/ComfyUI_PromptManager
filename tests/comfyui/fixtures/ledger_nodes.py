"""Deterministic nodes and queue controls installed only in an owned test host."""

import threading
import time

from aiohttp import web
import comfy.model_management
from server import PromptServer

_GATE = threading.Event()
_GATE.set()
_ENTERED = threading.Event()


@PromptServer.instance.routes.post("/pm_execution_fixture/gate/{action}")
async def control_gate(request: web.Request) -> web.Response:
    action = request.match_info["action"]
    if action == "hold":
        _ENTERED.clear()
        _GATE.clear()
    elif action == "release":
        _GATE.set()
    else:
        raise web.HTTPBadRequest()
    return web.json_response({"held": not _GATE.is_set()})


@PromptServer.instance.routes.get("/pm_execution_fixture/gate")
async def gate_status(request: web.Request) -> web.Response:
    return web.json_response({"entered": _ENTERED.is_set(), "held": not _GATE.is_set()})


class PMHistoryCached:
    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {"required": {"value": ("STRING", {"default": "deterministic"})}}

    RETURN_TYPES = ("STRING",)
    FUNCTION = "execute"
    CATEGORY = "testing/execution-history"
    OUTPUT_NODE = True

    def execute(self, value: str) -> dict:
        return {"ui": {"text": [value]}, "result": (value,)}


class PMHistoryFailure(PMHistoryCached):
    def execute(self, value: str) -> dict:
        raise ValueError(value)


class PMHistoryGate(PMHistoryCached):
    @classmethod
    def IS_CHANGED(cls, **kwargs: object) -> float:
        return float("nan")

    def execute(self, value: str) -> dict:
        _ENTERED.set()
        deadline = time.monotonic() + 30
        while not _GATE.wait(0.05):
            comfy.model_management.throw_exception_if_processing_interrupted()
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned execution fixture gate timed out")
        return super().execute(value)


NODE_CLASS_MAPPINGS = {
    "PMHistoryCached": PMHistoryCached,
    "PMHistoryFailure": PMHistoryFailure,
    "PMHistoryGate": PMHistoryGate,
}
