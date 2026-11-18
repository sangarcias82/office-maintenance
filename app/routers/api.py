from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})
