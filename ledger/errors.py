class LedgerError(Exception):
    """A user-facing failure: what went wrong and what to do next."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint
