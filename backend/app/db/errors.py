"""Domain errors raised by repositories; the API layer maps them to HTTP status codes."""


class NotFoundError(Exception):
    """A requested row does not exist. Mapped to HTTP 404 in app.main."""

    def __init__(self, kind: str, id_: str) -> None:
        super().__init__(f"{kind} {id_} not found")
        self.kind = kind
        self.id = id_


class ConflictError(Exception):
    """The request is valid but doesn't fit the current state (e.g. merging the main thread).

    Mapped to HTTP 409 in app.main.
    """
