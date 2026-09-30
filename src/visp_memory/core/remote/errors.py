class RemoteStorageError(RuntimeError):
    """Raised when the remote server cannot complete an operation.

    The message stays the human sentence callers already match on; the fields let
    an interface tell "the server is not running" from "the server said no"
    without parsing it.
    """

    def __init__(
        self,
        message: str,
        *,
        server_url: str | None = None,
        status_code: int | None = None,
        detail: str | None = None,
        unreachable: bool = False,
    ):
        super().__init__(message)
        self.server_url = server_url
        self.status_code = status_code
        self.detail = detail
        self.unreachable = unreachable
