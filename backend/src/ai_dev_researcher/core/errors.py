from __future__ import annotations


class AppError(Exception):
    code: str = "APP_ERROR"
    status_code: int = 500
    retryable: bool = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable

    @property
    def message(self) -> str:
        return str(self)


class ConfigurationError(AppError):
    code = "CONFIGURATION_ERROR"
    status_code = 500


class SessionNotFoundError(AppError):
    code = "SESSION_NOT_FOUND"
    status_code = 404


class RunNotFoundError(AppError):
    code = "RUN_NOT_FOUND"
    status_code = 404


class RunConflictError(AppError):
    code = "RUN_ACTIVE"
    status_code = 409


class InvalidUploadError(AppError):
    code = "INVALID_UPLOAD"
    status_code = 400


class DocumentParseError(AppError):
    code = "DOCUMENT_PARSE_ERROR"
    status_code = 422


class ArtifactAccessError(AppError):
    code = "ARTIFACT_ACCESS_DENIED"
    status_code = 403


class ArtifactNotFoundError(AppError):
    code = "ARTIFACT_NOT_FOUND"
    status_code = 404


class ReportValidationError(AppError):
    code = "REPORT_VALIDATION_ERROR"
    status_code = 422


class KnowledgeBaseError(AppError):
    """Local knowledge base access failed (missing file, escape attempt, etc.)."""

    code = "KNOWLEDGE_BASE_ERROR"
    status_code = 403


class SearchProviderError(AppError):
    """External search provider (e.g. Tavily) call failed."""

    code = "SEARCH_PROVIDER_ERROR"
    status_code = 502
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ):
        super().__init__(message, code=code, retryable=retryable)
        self.provider = provider
