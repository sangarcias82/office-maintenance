import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import Forbidden, LoginRequired, get_lang
from app.csrf import CSRFError, redirect_target
from app.config import get_settings
from app.db import init_db
from app.deps import flash
from app.i18n import t
from app.routers import (
    api,
    auth_routes,
    evidence_routes,
    maintenance_routes,
    reports_routes,
    requests_routes,
    web,
)
from app.security import SecurityMiddleware, resolve_session_secret

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_application: FastAPI):
    Path(get_settings().upload_dir).mkdir(parents=True, exist_ok=True)
    init_db()
    yield


async def _login_required_handler(_: Request, exc: LoginRequired) -> RedirectResponse:
    return RedirectResponse(f"/login?{urlencode({'next': exc.next_path})}", status_code=303)


async def _forbidden_handler(request: Request, exc: Forbidden) -> RedirectResponse:
    flash(request, exc.message, "error")
    return RedirectResponse("/", status_code=303)


async def _csrf_handler(request: Request, _exc: CSRFError) -> RedirectResponse:
    # The route body never ran, so nothing was written. Send the user back to
    # the page they came from (same site only) to resubmit with a fresh form.
    flash(request, t(get_lang(request), "csrf.invalid"), "error")
    return RedirectResponse(redirect_target(request), status_code=303)


def create_app() -> FastAPI:
    settings = get_settings()

    application = FastAPI(title=settings.app_name, lifespan=lifespan)
    application.add_middleware(
        SessionMiddleware,
        # Never sign sessions with the public placeholder (see app/security.py).
        secret_key=resolve_session_secret(settings.secret_key),
        session_cookie="maintenance_session",
        same_site="lax",  # keep Lax: second layer next to the CSRF token (app/csrf.py)
        https_only=settings.session_https_only,
    )
    # Added last = outermost: size limit first, headers on every response.
    application.add_middleware(
        SecurityMiddleware,
        max_body_bytes=settings.max_request_bytes,
        hsts=settings.session_https_only,
    )
    application.add_exception_handler(LoginRequired, _login_required_handler)
    application.add_exception_handler(Forbidden, _forbidden_handler)
    application.add_exception_handler(CSRFError, _csrf_handler)

    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Uploaded evidence is deliberately NOT mounted as static files; it is served by
    # GET /evidence/{photo_id} (routers/evidence_routes.py) after an access check.

    application.include_router(api.router)
    application.include_router(auth_routes.router)
    application.include_router(web.router)
    application.include_router(requests_routes.router)
    application.include_router(maintenance_routes.router)
    application.include_router(reports_routes.router)
    application.include_router(evidence_routes.router)
    return application


app = create_app()
