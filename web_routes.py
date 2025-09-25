"""aiohttp route helpers for serving the PromptManager UI."""

from pathlib import Path
from aiohttp import web

CURRENT_DIR = Path(__file__).parent.resolve()
WEB_DIR = CURRENT_DIR / 'web'

PAGES = {
  'index': 'index.html',
  'dashboard': 'dashboard.html',
  'gallery': 'gallery.html',
  'collections': 'collections.html',
  'stats': 'stats.html',
  'metadata': 'metadata.html',
  'logs': 'logs.html',
  'settings': 'settings.html',
}


def _safe_path(relative_path: str) -> Path:
  candidate = (WEB_DIR / relative_path).resolve()
  if not str(candidate).startswith(str(WEB_DIR)):
    raise ValueError('Invalid path')
  return candidate


async def serve_page(request: web.Request, page: str) -> web.Response:
  try:
    file_name = PAGES.get(page)
    if not file_name:
      raise web.HTTPNotFound()
    file_path = _safe_path(file_name)
    if not file_path.exists():
      raise web.HTTPNotFound()
    return web.FileResponse(file_path)
  except web.HTTPException:
    raise
  except Exception as exc:  # pragma: no cover - defensive
    print(f"Error serving page {page}: {exc}")
    return web.Response(text=str(exc), status=500)


def make_static_handler(subdir: str):
  async def handler(request: web.Request) -> web.StreamResponse:
    path = request.match_info.get('path', '')
    relative = Path(subdir) / path if subdir else Path(path)
    try:
      file_path = _safe_path(relative)
    except ValueError:
      return web.Response(text='Invalid path', status=400)

    if not file_path.exists() or not file_path.is_file():
      raise web.HTTPNotFound()

    return web.FileResponse(file_path)

  return handler


async def redirect_admin(_request: web.Request) -> web.Response:
  raise web.HTTPFound(location='/prompt_manager/dashboard')


def page_handler(page: str):
  async def handler(request: web.Request) -> web.Response:
    return await serve_page(request, page)

  return handler


def setup_routes(routes):
  """Register PromptManager UI routes with the ComfyUI server."""

  index_handler = page_handler('index')
  routes.get('/prompt_manager')(index_handler)
  routes.get('/prompt_manager/')(index_handler)
  routes.get('/prompt_manager/index')(index_handler)

  for page in PAGES:
    if page == 'index':
      continue
    routes.get(f'/prompt_manager/{page}')(page_handler(page))
    routes.get(f'/prompt_manager/{page}/')(page_handler(page))

  routes.get('/prompt_manager/admin')(redirect_admin)
  routes.get('/prompt_manager/admin/')(redirect_admin)

  # Static assets
  routes.get('/prompt_manager/css/{path:.*}')(make_static_handler('css'))
  routes.get('/prompt_manager/js/{path:.*}')(make_static_handler('js'))
  routes.get('/prompt_manager/vendor/{path:.*}')(make_static_handler('vendor'))

  print('PromptManager web routes registered successfully')
  print('Access the interface at: http://localhost:8188/prompt_manager/')

  async def favicon(_request: web.Request) -> web.Response:
    return web.FileResponse(_safe_path('favicon.ico'))

  routes.get('/favicon.ico')(favicon)
