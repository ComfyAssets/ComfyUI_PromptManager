"""Hold real requests at this test server, so a page can leave while one is in flight.

A Playwright ``page.route`` hold answers inside the browser's interception layer,
which WebKit cancels differently from a request the server has not answered yet.
These commands hold the request at the server itself:

- ``POST /__test__/hold`` with ``{"path": "/prompt_manager/..."}`` holds every
  later request for that path until it is released. With ``"stage": "body"``
  the server answers first and holds after sending the headers and the first
  half of the body, so the browser has a response whose body is still loading.
- ``GET /__test__/hold`` answers ``{"held": {path: count}}``: how many requests
  for each held path have arrived and are waiting.
- ``POST /__test__/release`` lets every held request go on, and stops holding.
"""

import asyncio

from aiohttp import web

HOLDABLE_PREFIX = "/prompt_manager/"


class RequestHolds:
    def __init__(self) -> None:
        self._gates: dict[str, asyncio.Event] = {}
        self._waiting: dict[str, int] = {}
        self._stages: dict[str, str] = {}

    def hold(self, path: str, stage: str = "request") -> None:
        self._gates.setdefault(path, asyncio.Event())
        self._waiting.setdefault(path, 0)
        self._stages[path] = stage

    def stage(self, path: str) -> str | None:
        return self._stages.get(path) if path in self._gates else None

    def held(self) -> dict[str, int]:
        return dict(self._waiting)

    def release(self) -> None:
        for gate in self._gates.values():
            gate.set()
        self._gates.clear()
        self._waiting.clear()
        self._stages.clear()

    async def wait(self, path: str) -> None:
        gate = self._gates.get(path)
        if gate is None:
            return
        self._waiting[path] = self._waiting.get(path, 0) + 1
        try:
            await gate.wait()
        finally:
            if path in self._waiting:
                self._waiting[path] -= 1


def register_request_holds(app: web.Application, routes) -> RequestHolds:
    holds = RequestHolds()

    @web.middleware
    async def hold_middleware(request: web.Request, handler):
        stage = holds.stage(request.path)
        if stage == "request":
            await holds.wait(request.path)
        response = await handler(request)
        if stage != "body" or not isinstance(response, web.Response):
            return response
        body = response.body if isinstance(response.body, bytes) else b""
        streamed = web.StreamResponse(status=response.status, headers=response.headers)
        streamed.content_length = len(body)
        await streamed.prepare(request)
        await streamed.write(body[: len(body) // 2])
        await holds.wait(request.path)
        await streamed.write(body[len(body) // 2 :])
        await streamed.write_eof()
        return streamed

    app.middlewares.append(hold_middleware)

    @routes.post("/__test__/hold")
    async def hold(request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        path = data.get("path") if isinstance(data, dict) else None
        stage = data.get("stage", "request") if isinstance(data, dict) else None
        if (
            not isinstance(path, str)
            or not path.startswith(HOLDABLE_PREFIX)
            or set(data) - {"path", "stage"}
            or stage not in ("request", "body")
        ):
            raise web.HTTPBadRequest()
        holds.hold(path, stage)
        return web.json_response({"success": True})

    @routes.get("/__test__/hold")
    async def held(request: web.Request) -> web.Response:
        return web.json_response({"success": True, "held": holds.held()})

    @routes.post("/__test__/release")
    async def release(request: web.Request) -> web.Response:
        holds.release()
        return web.json_response({"success": True})

    return holds
