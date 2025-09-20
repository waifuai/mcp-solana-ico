"""
Solana ICO Server - MCP Server Implementation

This module provides the main MCP server implementation for a Solana-based ICO (Initial Coin Offering) system.
It handles token purchases, ICO management, bonding curves, and integration with Solana blockchain.

Key Features:
- Multi-ICO support with individual configurations
- Multiple bonding curve types (fixed, linear, exponential, sigmoid, custom)
- Rate limiting and security measures
- Comprehensive error handling and logging
- Action API for Solana Blinks integration
- Performance monitoring and caching

Security Features:
- Input validation and sanitization
- Rate limiting by IP address
- CORS origin validation
- Secure error message handling (no internal details exposed)
- Configurable wallet and RPC endpoint management

Performance Optimizations:
- HTTP client connection pooling
- ICO data caching with file modification detection
- Efficient rate limiting with automatic cleanup
- Performance monitoring and structured logging

Author: AI Assistant
License: MIT-0
"""

import asyncio
import json
import time
import httpx
from typing import Optional, Dict, Any

# Add performance monitoring
_start_time = time.time()

# Constants
MAX_ICO_ID_LENGTH = 100
MAX_TOKEN_AMOUNT = 10**18  # 1 quintillion (reasonable upper limit)
MAX_TRANSACTION_SIG_LENGTH = 200
DISCOUNT_BASE_TOKENS = 1000  # Base number of tokens for discount calculation
DISCOUNT_RATE = 0.01  # 1% discount per base tokens
MAX_DISCOUNT_PERCENTAGE = 0.1  # Maximum 10% discount

from pydantic import Field, ValidationError
from solders.pubkey import Pubkey
from solders.signature import Signature

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.utilities.logging import get_logger

# Import refactored modules
from mcp_solana_ico import config
from mcp_solana_ico import ico_manager
from mcp_solana_ico import actions # Keep actions import for now if Flask app is still relevant
from mcp_solana_ico import pricing
from mcp_solana_ico import rate_limiter
from mcp_solana_ico import solana_utils
from mcp_solana_ico.schemas import IcoConfigModel, CurveType # Import necessary schemas
from mcp_solana_ico.errors import (
    InactiveICOError,
    InsufficientFundsError,
    InvalidTransactionError,
    TransactionFailedError,
    RateLimitExceededError,
    # TokenBalanceError might be used elsewhere or can be removed if only used in solana_utils
)

logger = get_logger(__name__)

# --- Server Setup ---
mcp = FastMCP(name="Solana ICO Server")

# ICO data is now managed by ico_manager and loaded on import.
# No need for ico_data global here or loading from env vars directly.

# --- Helper Functions (Currency Conversion - can stay or move to a general utils if needed) ---
# These are simple enough to potentially keep here or move later if a general utils module emerges.
def lamports_to_sol(lamports: int) -> float:
    """Convert lamports to SOL."""
    return lamports / config.LAMPORTS_PER_SOL

def sol_to_lamports(sol: float) -> int:
    """Convert SOL to lamports."""
    return int(sol * config.LAMPORTS_PER_SOL)


def validate_token_operation_params(ico_id: str, amount: int, payment_transaction: str, client_ip: str) -> None:
    """
    Validate common parameters for token operations.

    Args:
        ico_id: ICO identifier to validate
        amount: Token amount to validate
        payment_transaction: Transaction signature to validate
        client_ip: Client IP to validate

    Raises:
        ValueError: If any parameter is invalid
    """
    if not ico_id or not isinstance(ico_id, str):
        raise ValueError("ICO ID must be a non-empty string")
    if len(ico_id) > MAX_ICO_ID_LENGTH:
        raise ValueError("ICO ID is too long")

    if not isinstance(amount, int) or amount <= 0:
        raise ValueError("Amount must be a positive integer")
    if amount > MAX_TOKEN_AMOUNT:
        raise ValueError("Amount is too large")

    if not payment_transaction or not isinstance(payment_transaction, str):
        raise ValueError("Payment transaction must be a non-empty string")
    if len(payment_transaction) > MAX_TRANSACTION_SIG_LENGTH:
        raise ValueError("Payment transaction signature is too long")

    if not client_ip or not isinstance(client_ip, str):
        raise ValueError("Client IP must be a non-empty string")


def format_token_amount(amount: int, decimals: int, symbol: str) -> str:
    """Format token amount with proper decimal places and symbol."""
    token_amount_ui = amount / (10 ** decimals)
    return f"{token_amount_ui:.{decimals}f} {symbol}"


def log_token_operation_success(
    operation: str,
    ico_id: str,
    token_amount: int,
    token_decimals: int,
    token_symbol: str,
    sol_amount: float,
    payment_tx: str,
    transfer_tx: str,
    duration: float,
    client_ip: str
) -> None:
    """Log successful token operation with structured information."""
    token_display = format_token_amount(token_amount, token_decimals, token_symbol)
    logger.info(f"Token {operation} completed for ICO '{ico_id}': "
               f"amount={token_display}, "
               f"cost={sol_amount:.9f} SOL, "
               f"payment_tx={payment_tx[:8]}..., "
               f"transfer_tx={transfer_tx[:8]}..., "
               f"duration={duration:.3f}s, client_ip={client_ip}")


def log_operation_error(
    operation: str,
    ico_id: str,
    error: Exception,
    client_ip: str,
    duration: float
) -> None:
    """Log operation error with structured information."""
    logger.error(f"{operation} failed for ICO '{ico_id}': {error}, "
                f"client_ip: {client_ip}, duration: {duration:.3f}s")


# --- MCP Resources ---

@mcp.tool() # Changed from resource to tool
async def get_ico_info(context: Context, ico_id: str = Field(..., description="The ICO ID.")) -> str:
    """Get information about a specific ICO."""
    try:
        # Input validation
        if not ico_id or not isinstance(ico_id, str):
            raise ValueError("ICO ID must be a non-empty string")
        if len(ico_id) > 100:  # Reasonable limit
            raise ValueError("ICO ID is too long")

        ico = ico_manager.get_ico(ico_id)
        if not ico:
            logger.warning(f"ICO not found: {ico_id}")
            return f"ICO with id {ico_id} not found."

        # Return JSON representation using Pydantic's model_dump_json
        return ico.model_dump_json(indent=2)
    except ValueError as e:
        logger.error(f"Invalid ICO ID provided: {e}")
        return f"Invalid ICO ID: {e}"
    except Exception as e:
        logger.exception(f"Unexpected error getting ICO info for {ico_id}: {e}")
        return f"An unexpected error occurred while retrieving ICO information."

@mcp.tool() # Changed from resource to tool
async def create_ico(context: Context, config_json: str = Field(..., description="The ICO configuration as a JSON string.")) -> str:
    """Creates a new ICO from a JSON configuration string."""
    try:
        # Input validation
        if not config_json or not isinstance(config_json, str):
            raise ValueError("Configuration JSON must be a non-empty string")
        if len(config_json) > 10000:  # Reasonable size limit
            raise ValueError("Configuration JSON is too large (max 10KB)")

        config_data = json.loads(config_json)

        # Validate the input data against the schema
        ico_config = IcoConfigModel.model_validate(config_data)

        # Additional business logic validation
        if ico_config.ico.start_time >= ico_config.ico.end_time:
            raise ValueError("ICO start time must be before end time")
        if ico_config.token.decimals < 0 or ico_config.token.decimals > 18:
            raise ValueError("Token decimals must be between 0 and 18")
        if ico_config.token.total_supply <= 0:
            raise ValueError("Token total supply must be positive")

        # Use the ico_manager to add/update and save the ICO
        success = ico_manager.add_or_update_ico(ico_config)

        if success:
            logger.info(f"ICO '{ico_config.ico.ico_id}' created/updated successfully")
            return f"ICO '{ico_config.ico.ico_id}' created/updated successfully."
        else:
            logger.error(f"Failed to save ICO configuration for '{ico_config.ico.ico_id}'")
            return f"Error saving ICO configuration for '{ico_config.ico.ico_id}'."

    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON for create_ico request: {e}")
        return "Error: Invalid JSON format provided. Please check your JSON syntax."
    except ValidationError as e:
        logger.error(f"Invalid ICO configuration provided to create_ico: {e}")
        return f"Error: Invalid ICO configuration - {e}"
    except ValueError as e:
        logger.error(f"Validation error in create_ico: {e}")
        return f"Error: {e}"
    except Exception as e:
        logger.exception(f"Unexpected error creating ICO via create_ico: {e}")
        return f"An unexpected server error occurred while creating the ICO."


# --- MCP Tools ---

@mcp.tool()
async def buy_tokens(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    amount: int = Field(
        ..., description="The number of tokens to purchase/sell (in base units)."
    ),
    payment_transaction: str = Field(
        ...,
        description=(
            "The transaction signature of the SOL payment (for buying) or "
            "the pre-signed token transfer (for selling - if implemented this way)."
            " Ensure the transaction is already signed."
        ),
    ),
    client_ip: str = Field(..., description="The client's IP address."),
    # affiliate_id: Optional[str] = Field(None, description="The affiliate ID (optional)."), # Removed as per plan 8
    sell: bool = Field(False, description="Set to True to sell tokens, False to buy.")
) -> str:
    """
    Buys or sells tokens for a specific ICO.

    This function handles the complete token purchase/sell workflow including:
    - Input validation and sanitization
    - Rate limiting checks
    - ICO availability verification
    - Price calculation using bonding curves
    - Payment transaction validation
    - Token transfer execution
    - State updates and logging

    Args:
        context: MCP context object (provided by framework)
        ico_id: Unique identifier of the ICO
        amount: Number of tokens to buy/sell in base units (integer)
        payment_transaction: Transaction signature for payment verification
        client_ip: Client's IP address for rate limiting
        sell: True for selling tokens, False for buying

    Returns:
        str: Success message with transaction details, or error message

    Raises:
        Various custom exceptions are caught and converted to user-friendly messages:
        - RateLimitExceededError: When rate limit is exceeded
        - InactiveICOError: When ICO is not currently active
        - InsufficientFundsError: When payment is insufficient
        - InvalidTransactionError: When transaction is invalid
        - TransactionFailedError: When blockchain transaction fails
        - ValueError: When input validation fails

    Security Notes:
        - All error messages are sanitized to prevent information leakage
        - Rate limiting is enforced per IP address
        - Input validation prevents injection attacks
        - Transaction signatures are validated for format

    Performance:
        - Uses connection pooling for HTTP requests
        - Includes performance timing for monitoring
        - Caches ICO data to reduce file I/O

    Example:
        >>> result = await buy_tokens(
        ...     context=ctx,
        ...     ico_id="main_ico",
        ...     amount=1000,
        ...     payment_transaction="abc123...",
        ...     client_ip="192.168.1.1",
        ...     sell=False
        ... )
        "Successfully purchased 1.000 MMT at a price of 0.001000 SOL..."
    """
    start_time = time.time()

    # Use an async HTTP client session for multiple requests if needed
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        limits=httpx.Limits(max_keepalive=20, max_connections=100)
    ) as client:
        try:
            # 1. Input Validation
            validate_token_operation_params(ico_id, amount, payment_transaction, client_ip)

            # 2. Rate Limiting Check
            if not rate_limiter.check_rate_limit(client_ip):
                # Logged within check_rate_limit
                logger.warning(f"Rate limit exceeded for IP: {client_ip} attempting to buy tokens for ICO: {ico_id}")
                raise RateLimitExceededError(f"Rate limit exceeded for IP: {client_ip}")

            # 3. Get ICO Configuration
            ico = ico_manager.get_ico(ico_id)
            if not ico:
                logger.warning(f"ICO not found: {ico_id}, client_ip: {client_ip}")
                return f"ICO with id {ico_id} not found."

            # 4. Basic Validations
            # Check if ICO is active (using start/end times from loaded config)
            current_time = time.time()
            if not (ico.ico.start_time <= current_time <= ico.ico.end_time):
                 # Use default timestamps from config if not present in ico config? Or make mandatory?
                 # Assuming start/end times are mandatory in IcoConfig schema now.
                 raise InactiveICOError(f"ICO '{ico_id}' is not active. Current time: {current_time}, Start: {ico.ico.start_time}, End: {ico.ico.end_time}")

            # 5. Calculate Required SOL / Expected SOL from Sale
            # Use the dedicated pricing module
            try:
                required_or_expected_sol = pricing.calculate_token_price(amount, ico, is_sell=sell)
                if required_or_expected_sol < 0:
                    raise ValueError("Calculated price cannot be negative")
            except Exception as e:
                logger.error(f"Error calculating token price for {ico_id}: {e}")
                raise ValueError(f"Error calculating token price: {e}")

            # 6. Validate Payment Transaction Signature Format
            try:
                tx_signature = Signature.from_string(payment_transaction)
            except ValueError as e:
                raise InvalidTransactionError(f"Invalid transaction signature format: {e}")

            # --- Buy Logic ---
            if not sell:
                # 7. Validate the SOL payment transaction using solana_utils
                try:
                    payer = await solana_utils.validate_payment_transaction(client, tx_signature, required_or_expected_sol)
                    logger.info(f"Payment validated for {ico_id} buy from {payer}. Required SOL: {required_or_expected_sol:.9f}")
                except Exception as e:
                    logger.error(f"Payment validation failed for {ico_id}: {e}")
                    raise

                # 8. Create and send the token transfer using solana_utils
                try:
                    token_transfer_tx_hash = await solana_utils.create_and_send_token_transfer(client, payer, amount, ico_id)
                    logger.info(f"Token transfer successful for {ico_id} buy. Tx: {token_transfer_tx_hash}")
                except Exception as e:
                    logger.error(f"Token transfer failed for {ico_id}: {e}")
                    raise

                # 9. Update total minted tokens using ico_manager
                try:
                    ico_manager.increment_tokens_minted(ico_id, amount)
                    logger.debug(f"Updated token count for {ico_id}: +{amount}")
                except Exception as e:
                    logger.error(f"Failed to update token count for {ico_id}: {e}")
                    # Don't raise here as the transaction was successful
                    # Log the error but continue

                # 10. Format success response and log
                end_time = time.time()
                duration = end_time - start_time
                log_token_operation_success(
                    "purchase", ico_id, amount, ico.token.decimals, ico.token.symbol,
                    required_or_expected_sol, payment_transaction, token_transfer_tx_hash,
                    duration, client_ip
                )

                token_display = format_token_amount(amount, ico.token.decimals, ico.token.symbol)
                return (f"Successfully purchased {token_display} "
                        f"at a price of {required_or_expected_sol:.9f} SOL. "
                        f"Payment received (txid: {payment_transaction}). "
                        f"Token transfer txid: {token_transfer_tx_hash}")

            # --- Sell Logic ---
            else:
                # Selling requires different validation:
                # 1. Validate the *token transfer* transaction from the user to the ICO wallet.
                # 2. If valid, send the calculated SOL (required_or_expected_sol) back to the user.
                # This part needs careful implementation and likely a different signature validation approach.
                # For now, returning NotImplemented.
                logger.warning(f"Sell functionality requested for {ico_id} but not fully implemented, client_ip: {client_ip}")
                # Example steps if implemented:
                # user_pubkey = await solana_utils.validate_token_transfer_to_ico(client, tx_signature, amount, ico)
                # sol_transfer_tx_hash = await solana_utils.create_and_send_sol_transfer(client, user_pubkey, required_or_expected_sol)
                # ico_manager.decrement_tokens_minted(ico_id, amount) # Need decrement function
                # return f"Successfully sold {amount / (10 ** ico.token.decimals)} {ico.token.symbol} for {required_or_expected_sol:.6f} SOL..."
                return "Sell functionality is not yet fully implemented."


        # --- Error Handling ---
        except RateLimitExceededError as e:
            # Already logged in rate_limiter
            return str(e)
        except InactiveICOError as e:
            log_operation_error("Token purchase", ico_id, e, client_ip, time.time() - start_time)
            return str(e)
        except InsufficientFundsError as e:
            log_operation_error("Token purchase", ico_id, e, client_ip, time.time() - start_time)
            return str(e)
        except InvalidTransactionError as e:
            log_operation_error("Token purchase", ico_id, e, client_ip, time.time() - start_time)
            return str(e)
        except TransactionFailedError as e:
            log_operation_error("Token purchase", ico_id, e, client_ip, time.time() - start_time)
            return str(e)
        except ValueError as e:
             log_operation_error("Token purchase", ico_id, e, client_ip, time.time() - start_time)
             return f"Error processing request: Invalid input parameters"
        except Exception as e:
            log_operation_error("Token purchase", ico_id, e, client_ip, time.time() - start_time)
            return f"An unexpected server error occurred"


@mcp.tool()
async def get_discount(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    amount: int = Field(..., description="The amount of tokens held (in base units)."),
) -> str:
    """Gets a discount based on the amount of tokens held (example utility)."""
    try:
        # Input validation
        if not ico_id or not isinstance(ico_id, str):
            raise ValueError("ICO ID must be a non-empty string")
        if len(ico_id) > MAX_ICO_ID_LENGTH:
            raise ValueError("ICO ID is too long")

        if not isinstance(amount, int) or amount < 0:
            raise ValueError("Amount must be a non-negative integer")
        if amount > MAX_TOKEN_AMOUNT:
            raise ValueError("Amount is too large")

        ico = ico_manager.get_ico(ico_id)
        if not ico:
            logger.warning(f"ICO not found for discount calculation: {ico_id}")
            return f"ICO with id {ico_id} not found."

        # Example logic (remains simple for now)
        # Convert amount from base units for calculation if logic depends on token amount
        try:
            token_amount = amount / (10**ico.token.decimals)
            discount_percentage = token_amount / DISCOUNT_BASE_TOKENS * DISCOUNT_RATE
            discount_percentage = min(discount_percentage, MAX_DISCOUNT_PERCENTAGE)

            logger.debug(f"Calculated discount for {ico_id}: {discount_percentage*100:.2f}% based on {token_amount} tokens")
            return f"Discount based on {token_amount} {ico.token.symbol}: {discount_percentage*100:.2f}%"
        except ZeroDivisionError:
            logger.error(f"Zero decimals for ICO {ico_id}")
            return "Error: Invalid token configuration (decimals cannot be zero)"
    except ValueError as e:
        logger.error(f"Validation error in get_discount for {ico_id}: {e}")
        return f"Error: {e}"
    except Exception as e:
        logger.exception(f"Unexpected error getting discount for ICO {ico_id}: {e}")
        return f"An unexpected error occurred while calculating discount."


# --- Main Execution ---
if __name__ == "__main__":
    startup_start = time.time()
    logger.info("Starting Solana ICO MCP Server...")

    # The ico_manager loads data on import
    ico_count = len(ico_manager.ico_data)
    startup_duration = time.time() - startup_start
    logger.info(f"Server startup completed in {startup_duration:.3f}s, loaded {ico_count} ICO(s).")

    try:
        asyncio.run(mcp.run(transport="stdio"))
    except KeyboardInterrupt:
        logger.info("Server shutdown requested by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
    finally:
        logger.info("Solana ICO MCP Server stopped.")