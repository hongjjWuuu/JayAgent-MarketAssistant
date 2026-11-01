class ApiError(Exception):
    def __init__(
        self, status_code: int, error_code: str, message: str, details: dict | None = None
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        super().__init__(message)
