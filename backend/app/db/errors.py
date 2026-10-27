"""Domain errors raised by repositories; the API layer maps them to HTTP status codes."""


class NotFoundError(Exception):
    """A requested row does not exist. Mapped to HTTP 404 in app.main."""

    def __init__(self, kind: str, id_: str) -> None:
        super().__init__(f"{kind} {id_} not found")
        self.kind = kind
        self.id = id_
