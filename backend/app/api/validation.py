"""422 responses that can always be encoded.

FastAPI's default `RequestValidationError` handler echoes each error's `input`
back to the client. If the body held an unpaired UTF-16 surrogate (JSON
`"\\ud83d"` with no low half, e.g. a client that cut a string mid-emoji),
pydantic correctly rejects it, but then the echoed `input` can't be encoded
as UTF-8 and the 422 itself crashes into a 500. This handler returns the same
body with lone surrogates replaced by U+FFFD.
"""

from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def scrub_surrogates(value: Any) -> Any:
    """Recursively replace unpaired surrogates in strings with U+FFFD."""
    if isinstance(value, str):
        # Round-trip through UTF-16: valid pairs survive, lone halves can't be
        # decoded and become the replacement character.
        return value.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    if isinstance(value, dict):
        return {scrub_surrogates(k): scrub_surrogates(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub_surrogates(v) for v in value]
    return value


async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Same shape as FastAPI's default 422 (`{"detail": [...]}`), but always encodable."""
    return JSONResponse(
        status_code=422, content={"detail": scrub_surrogates(jsonable_encoder(exc.errors()))}
    )
