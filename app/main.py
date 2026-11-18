import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import Forbidden, LoginRequired
from app.config import get_settings
from app.db import init_db
from app.deps import flash
from app.routers import api, auth_routes, maintenance_routes, reports_routes, requests_routes, web

logger = logging.getLogger(__name__)

PLACEHOLDER_SECRET = "YOUR_SECRET_KEY_HERE"
STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_MOUNT = Path(get_settings().upload_dir)


@asynccontextmanager
async def lifespan(_application: FastAPI):
    UPLOAD_MOUNT.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


async def _login_required_handler(_: Request, exc: LoginRequired) -> RedirectResponse:
    return RedirectResponse(f"/login?{urlencode({'next': exc.next_path})}", status_code=303)


async def _forbidden_handler(request: Request, exc: Forbidden) -> RedirectResponse:
    flash(request, exc.message, "error")
    return RedirectResponse("/", status_code=303)


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.secret_key == PLACEHOLDER_SECRET:
        logger.warning("SECRET_KEY is the placeholder value; set a real one in .env before deploying.")

    application = FastAPI(title=settings.app_name, lifespan=lifespan)
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="maintenance_session",
        same_site="lax",
        https_only=settings.session_https_only,
    )
    application.add_exception_handler(LoginRequired, _login_required_handler)
    application.add_exception_handler(Forbidden, _forbidden_handler)

    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    UPLOAD_MOUNT.mkdir(parents=True, exist_ok=True)
    application.mount("/uploads", StaticFiles(directory=str(UPLOAD_MOUNT)), name="uploads")

    application.include_router(api.router)
    application.include_router(auth_routes.router)
    application.include_router(web.router)
    application.include_router(requests_routes.router)
    application.include_router(maintenance_routes.router)
    application.include_router(reports_routes.router)
    return application


app = create_app()
