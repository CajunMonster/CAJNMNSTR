class CAJNMNSTRError(RuntimeError):
    """Base error for explicit, operator-visible failures."""


class ConfigurationError(CAJNMNSTRError):
    """Raised when configuration violates a safety invariant."""


class CredentialsMissingError(ConfigurationError):
    """Raised when an authenticated operation is requested without keys."""


class ExecutionDisabledError(CAJNMNSTRError):
    """Raised when any order path is reached while the paper gate is closed."""


class AuthorityDeniedError(ExecutionDisabledError):
    """Raised when evidence or Referee authority does not permit execution."""


class DuplicateOrderIdentityError(AuthorityDeniedError):
    """Raised when a durable client-order identity has already been used."""


class InvalidRefereeResultError(AuthorityDeniedError):
    """Raised when a Referee result is missing required deterministic structure."""


class EvidenceStoreError(CAJNMNSTRError):
    """Raised when durable evidence cannot be written or read."""
