"""Журнал HTTP не содержит токенов, параметров URL и содержимого заявок."""

import json
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger("uvicorn.error")


def instrument(app: FastAPI) -> None:
    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        logger.warning(
            "database_error request_id=%s kind=%s", request.state.request_id, type(error).__name__
        )
        return JSONResponse(
            {"detail": "База данных временно недоступна"},
            status_code=503,
            headers={"Retry-After": "1"},
        )

    @app.middleware("http")
    async def observe(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = uuid4().hex
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request.state.request_id,
                    "route": getattr(request.scope.get("route"), "path", "unmatched"),
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
        )
        return response
