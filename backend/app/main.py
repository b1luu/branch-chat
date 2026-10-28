"""FastAPI application entrypoint."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.ratelimit import RateLimitConfig, RateLimitMiddleware, TokenBucketLimiter
from app.api.routes import router
from app.db.errors import ConflictError, NotFoundError
from app.db.session import make_engine, make_session_factory
from app.llm import LLMProvider, get_provider


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    llm_provider: LLMProvider | None = None,
    rate_limit: RateLimitConfig | None = None,
    limiter: TokenBucketLimiter | None = None,
) -> FastAPI:
    """Build the FastAPI app. A factory keeps tests isolated from module-level state.

    Tests pass a session factory bound to an in-memory DB and/or a fake LLM;
    otherwise the app connects to `DATABASE_URL` (schema is created by Alembic,
    not here) and picks the provider from `LLM_PROVIDER`. Rate limiting (D3)
    comes from `RATE_LIMIT_*` env vars unless `rate_limit` is given; tests can
    also pass a `limiter` with a fake clock.
    """
    app = FastAPI(title="Branch Chat API", version="0.1.0")
    app.state.session_factory = session_factory or make_session_factory(make_engine())
    app.state.llm_provider = llm_provider or get_provider()
    app.include_router(router)

    config = rate_limit or RateLimitConfig.from_env()
    app.state.rate_limiter = None
    if config.enabled:
        app.state.rate_limiter = (
            limiter if limiter is not None else TokenBucketLimiter(config.per_minute, config.burst)
        )
        app.add_middleware(
            RateLimitMiddleware, limiter=app.state.rate_limiter, trust_proxy=config.trust_proxy
        )

    @app.exception_handler(NotFoundError)
    async def not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(_request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
