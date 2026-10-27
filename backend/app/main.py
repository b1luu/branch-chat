"""FastAPI application entrypoint."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import router
from app.db.errors import NotFoundError
from app.db.session import make_engine, make_session_factory


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    """Build the FastAPI app. A factory keeps tests isolated from module-level state.

    Tests pass a session factory bound to an in-memory DB; otherwise the app
    connects to `DATABASE_URL` (schema is created by Alembic, not here).
    """
    app = FastAPI(title="Branch Chat API", version="0.1.0")
    app.state.session_factory = session_factory or make_session_factory(make_engine())
    app.include_router(router)

    @app.exception_handler(NotFoundError)
    async def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
