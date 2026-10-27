"""FastAPI application entrypoint."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the FastAPI app. A factory keeps tests isolated from module-level state."""
    app = FastAPI(title="Branch Chat API", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
