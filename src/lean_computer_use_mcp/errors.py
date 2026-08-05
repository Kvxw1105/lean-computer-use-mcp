from __future__ import annotations


class LeanComputerUseError(Exception):
    code = "INTERNAL_ERROR"


class UpstreamError(LeanComputerUseError):
    code = "UPSTREAM_ERROR"


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


class CommitUncertainError(LeanComputerUseError):
    code = "COMMIT_UNCERTAIN"
