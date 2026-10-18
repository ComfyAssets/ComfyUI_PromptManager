"""Deterministic nodes and queue controls installed only in an owned test host."""

import threading
import time

import nodes
import execution
import torch

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

# Only external inference is replaced; PM classes and host execution remain real.


class _Clip:
    def tokenize(self, text: str) -> str:
        return text

    def encode_from_tokens_scheduled(self, tokens: str) -> list:
        return [[tokens, {}]]


class PMHistoryInputs:
    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {"required": {}}

    RETURN_TYPES = ("CLIP", "MODEL", "LATENT")
    FUNCTION = "execute"
    CATEGORY = "testing/execution-history"

    def execute(self) -> tuple:
        return (_Clip(), object(), {"samples": "owned deterministic latent"})


class PMHistoryOutput:
    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {
            "required": {"value": ("LATENT",)},
            "optional": {"mode": (["normal", "failure", "gate"],)},
        }

    RETURN_TYPES = ()
    FUNCTION = "execute"
    CATEGORY = "testing/execution-history"
    OUTPUT_NODE = True

    def execute(self, value: dict, mode: str = "normal") -> tuple:
        if mode == "failure":
            raise ValueError("owned downstream failure")
        if mode == "gate":
            PMHistoryGate().execute("owned downstream gate")
        return ()


def _sample(self: object, **kwargs: object) -> tuple:
    return (kwargs["latent_image"],)


nodes.KSampler.sample = _sample
NODE_CLASS_MAPPINGS.update(
    {"PMHistoryInputs": PMHistoryInputs, "PMHistoryOutput": PMHistoryOutput}
)


class PMHistoryList(PMHistoryCached):
    OUTPUT_IS_LIST = (True,)

    def execute(self, value: str) -> tuple:
        return ([value, value],)


class PMHistoryTransform:
    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {"required": {"conditioning": ("CONDITIONING",)}}

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "execute"
    CATEGORY = "testing/execution-history"

    def execute(self, conditioning: list) -> tuple:
        return (conditioning,)


NODE_CLASS_MAPPINGS.update(
    {"PMHistoryList": PMHistoryList, "PMHistoryTransform": PMHistoryTransform}
)


class PMHistoryImages:
    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {
            "required": {
                "value": ("INT", {"default": 0, "min": 0, "max": 255}),
                "batch": ("INT", {"default": 1, "min": 1, "max": 2}),
            },
            "optional": {"latent": ("LATENT",)},
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "execute"
    CATEGORY = "testing/execution-history"

    def execute(self, value: int, batch: int, latent: dict | None = None) -> tuple:
        return (torch.full((batch, 8, 8, 3), value / 255.0, device="cpu"),)


class PMHistoryImageOutput:
    @classmethod
    def INPUT_TYPES(cls) -> dict:
        return {
            "required": {
                "images": ("IMAGE",),
                "mode": (["failure", "gate"],),
            }
        }

    RETURN_TYPES = ()
    FUNCTION = "execute"
    CATEGORY = "testing/execution-history"
    OUTPUT_NODE = True

    def execute(self, images: torch.Tensor, mode: str) -> tuple:
        if mode == "failure":
            raise ValueError("owned failure after real image output")
        PMHistoryGate().execute("owned gate after real image output")
        return ()


NODE_CLASS_MAPPINGS.update(
    {"PMHistoryImages": PMHistoryImages, "PMHistoryImageOutput": PMHistoryImageOutput}
)


@PromptServer.instance.routes.post("/pm_execution_fixture/metadata")
async def metadata(request: web.Request) -> web.Response:
    disabled = (await request.json())["disabled"]
    if type(disabled) is not bool:
        raise web.HTTPBadRequest()
    nodes.args.disable_metadata = disabled
    return web.json_response({"disabled": disabled})


_INJECTION_ORIGINALS = {}


@PromptServer.instance.routes.post("/pm_execution_fixture/injection")
async def injection(request: web.Request) -> web.Response:
    suffix = (await request.json())["suffix"]
    if not isinstance(suffix, str) or len(suffix) > 128:
        raise web.HTTPBadRequest()
    for name in ("PromptManager", "PromptManagerText"):
        cls = nodes.NODE_CLASS_MAPPINGS[name]
        if name not in _INJECTION_ORIGINALS:
            _INJECTION_ORIGINALS[name] = cls._inject_lora_trigger_words
        cls._inject_lora_trigger_words = (
            (lambda self, text: text + suffix) if suffix else _INJECTION_ORIGINALS[name]
        )
    return web.json_response({"configured": True})


# Apply a requested host cache configuration on its own worker before execution.
# Retain only the mode scalar; never retain the worker/executor/cache objects.

_CACHE_MODE = None
_EXECUTE = execution.PromptExecutor.execute


def _execute_with_cache_mode(self: object, *args: object, **kwargs: object) -> object:
    global _CACHE_MODE
    if _CACHE_MODE is not None:
        self.cache_type = execution.CacheType[_CACHE_MODE]
        self.cache_args = {**self.cache_args, "lru": 100, "ram": 0}
        self.reset()
        _CACHE_MODE = None
    return _EXECUTE(self, *args, **kwargs)


execution.PromptExecutor.execute = _execute_with_cache_mode


@PromptServer.instance.routes.post("/pm_execution_fixture/cache")
async def cache_mode(request: web.Request) -> web.Response:
    global _CACHE_MODE
    mode = (await request.json())["mode"]
    if mode not in ("CLASSIC", "LRU", "RAM_PRESSURE", "NONE"):
        raise web.HTTPBadRequest()
    _CACHE_MODE = mode
    return web.json_response({"configured": True})
