"""FastAPI application entrypoint."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db.errors import NotFoundError


def create_app() -> FastAPI:
    """Build the FastAPI app. A factory keeps tests isolated from module-level state."""
    app = FastAPI(title="Branch Chat API", version="0.1.0")

    @app.exception_handler(NotFoundError)
    async def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
