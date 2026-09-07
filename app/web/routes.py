from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/")
async def index() -> dict[str, str]:
    return {"name": "HappyDay", "message": "Семейные дни рождения — каркас приложения готов"}


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    is_ready = request.app.state.ready
    return JSONResponse(
        {"status": "ready" if is_ready else "not_ready"},
        status_code=200 if is_ready else 503,
    )
