class InactiveICOError(Exception):
    """Raised when the ICO is not active (outside start/end time window)."""


class InsufficientFundsError(Exception):
    """Raised when the payment is insufficient for the requested token amount."""


class InvalidTransactionError(Exception):
    """Raised for invalid transaction signatures or structures."""


class TransactionFailedError(Exception):
    """Raised if the token transfer transaction fails on-chain."""


class TokenBalanceError(Exception):
    """Raised when there are issues fetching token balance from the blockchain."""


class RateLimitExceededError(Exception):
    """Raised when the rate limit is exceeded for API requests."""


class ConfigurationError(Exception):
    """Raised when there are configuration-related errors."""


class ValidationError(Exception):
    """Raised when input validation fails."""
