"""
Custom Exception Classes for Solana ICO System

This module defines custom exception classes specific to the Solana ICO system operations.
These exceptions provide clear categorization of different types of errors that can occur
during ICO operations, token transactions, and system interactions.

Exception Categories:
- ICO State Errors: Related to ICO availability and timing
- Transaction Errors: Related to blockchain transaction processing
- Rate Limiting Errors: Related to API usage limits
- Configuration Errors: Related to system configuration issues
- Validation Errors: Related to input data validation

Each exception class includes descriptive docstrings to help developers understand
when and why each exception might be raised, making debugging and error handling
more effective throughout the application.

Usage:
    These exceptions are used throughout the ICO system and should be caught
    and handled appropriately by calling code to provide user-friendly error messages
    while maintaining detailed logging for debugging purposes.
"""
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
