"""The one error type the core raises on purpose.

The CLI turns it into {"ok": false, "error": {"code", "message", "details"}} and
exit code 1. Codes are stable snake_case strings that callers can branch on.
"""


class JudicataError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_json(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}
