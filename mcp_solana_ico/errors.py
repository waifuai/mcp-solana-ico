class InactiveICOError(Exception):
    """Raised when the ICO is not active."""


class InsufficientFundsError(Exception):
    """Raised when the payment is insufficient."""


class InvalidTransactionError(Exception):
    """Raised for invalid transaction signatures or structures."""


class TransactionFailedError(Exception):
    """Raised if the token transfer transaction fails."""


class TokenBalanceError(Exception):
    """Raised if there's an issue fetching the token balance."""