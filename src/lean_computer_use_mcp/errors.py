class LeanComputerUseError(Exception):
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        # Structured machine-readable cause for the model: one of
        # window_not_found / timeout / out_of_bounds / win32_error / ...
        # Never contains screen text or keys.
        self.reason = reason


class UpstreamError(LeanComputerUseError):
    code = "UPSTREAM_ERROR"


class UpstreamTimeoutError(UpstreamError):
    """An upstream call exceeded its timeout; reason is always ``timeout``."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason="timeout")


class AppNotFoundError(LeanComputerUseError):
    code = "APP_NOT_FOUND"


class StaleStateError(LeanComputerUseError):
    code = "STALE_STATE"

    def __init__(self, message: str, current_state_id: str | None = None) -> None:
        super().__init__(message)
        self.current_state_id = current_state_id


class AmbiguousTargetError(LeanComputerUseError):
    code = "AMBIGUOUS_TARGET"


class UnsupportedActionError(LeanComputerUseError):
    code = "UNSUPPORTED_ACTION"


class RealInputUnavailableError(LeanComputerUseError):
    code = "REAL_INPUT_UNAVAILABLE"


class RealInputFailedError(LeanComputerUseError):
    """A real-input click reached Win32 but could not execute.

    ``reason`` is one of ``out_of_bounds`` / ``win32_error``.
    """

    code = "REAL_INPUT_FAILED"


class CommitUncertainError(LeanComputerUseError):
    code = "COMMIT_UNCERTAIN"
