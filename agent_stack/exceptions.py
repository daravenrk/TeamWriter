class AgentStackError(Exception):
    """Base typed exception for agent runtime failures with stable error codes."""

    def __init__(self, message, code="AGENT_STACK_ERROR", details=None):
        super().__init__(message)
        self.code = str(code)
        self.details = details or {}


class AgentProfileError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="AGENT_PROFILE_ERROR", details=details)


class AgentRouteConfigError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="AGENT_ROUTE_CONFIG_ERROR", details=details)


class AgentQuarantinedError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="AGENT_QUARANTINED", details=details)


class AgentHungError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="AGENT_HUNG", details=details)


class AgentUnexpectedError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="AGENT_UNEXPECTED_ERROR", details=details)


class OllamaRequestError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="OLLAMA_REQUEST_ERROR", details=details)


class OllamaResponseDecodeError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="OLLAMA_RESPONSE_DECODE_ERROR", details=details)


class OllamaEndpointError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="OLLAMA_ENDPOINT_ERROR", details=details)


class OllamaEmptyResponseError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="OLLAMA_EMPTY_RESPONSE", details=details)


class OpenClawProfileConfigError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="OPENCLAW_PROFILE_CONFIG_ERROR", details=details)


class StageQualityGateError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="STAGE_QUALITY_GATE_ERROR", details=details)


class ChapterSpecValidationError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="CHAPTER_SPEC_VALIDATION_ERROR", details=details)


class BookExportError(AgentStackError):
    def __init__(self, message, details=None):
        super().__init__(message, code="BOOK_EXPORT_ERROR", details=details)
