from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.db.migrations import schema_is_current

router = APIRouter()


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request) -> JSONResponse:
    is_ready = request.app.state.ready
    if is_ready:
        try:
            is_ready = schema_is_current(request.app.state.db_engine)
        except SQLAlchemyError:
            is_ready = False
    return JSONResponse(
        {"status": "ready" if is_ready else "not_ready"},
        status_code=200 if is_ready else 503,
    )
