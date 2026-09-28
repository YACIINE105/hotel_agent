from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, detail: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(detail)
        self.detail = detail
        if status_code:
            self.status_code = status_code
        if code:
            self.code = code


class NotFound(AppError):
    status_code, code = 404, "not_found"


class Forbidden(AppError):
    status_code, code = 403, "forbidden"


class Conflict(AppError):
    status_code, code = 409, "conflict"


class Unavailable(AppError):
    """External provider failure. Never carries provider secrets or raw errors."""

    status_code, code = 503, "provider_unavailable"

    def __init__(self, detail: str, *, upstream_status: int | None = None):
        super().__init__(detail)
        self.upstream_status = upstream_status


async def handle_app_error(_: Request, exc: AppError):
    return JSONResponse({"code": exc.code, "detail": exc.detail}, status_code=exc.status_code)
