import asyncio
import json
import time
import httpx
from typing import Optional

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


# --- MCP Resources ---

@mcp.tool() # Changed from resource to tool
async def get_ico_info(context: Context, ico_id: str = Field(..., description="The ICO ID.")) -> str:
    """Get information about a specific ICO."""
    ico = ico_manager.get_ico(ico_id)
    if not ico:
        return f"ICO with id {ico_id} not found."
    # Return JSON representation using Pydantic's model_dump_json
    return ico.model_dump_json(indent=2)

@mcp.tool() # Changed from resource to tool
async def create_ico(context: Context, config_json: str = Field(..., description="The ICO configuration as a JSON string.")) -> str:
    """Creates a new ICO from a JSON configuration string."""
    try:
        config_data = json.loads(config_json)
        # Validate the input data against the schema
        ico_config = IcoConfigModel.model_validate(config_data)

        # Use the ico_manager to add/update and save the ICO
        success = ico_manager.add_or_update_ico(ico_config)

        if success:
            return f"ICO '{ico_config.ico.ico_id}' created/updated successfully."
        else:
            return f"Error saving ICO configuration for '{ico_config.ico.ico_id}'."

    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON for ico://create request.")
        return "Error: Invalid JSON format provided."
    except ValidationError as e:
        logger.error(f"Invalid ICO configuration provided to ico://create: {e}")
        return f"Error: Invalid ICO configuration: {e}"
    except Exception as e:
        logger.exception(f"Unexpected error creating ICO via ico://create: {e}")
        return f"An unexpected error occurred: {e}"


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
    """Buys or sells tokens for a specific ICO."""
    # Use an async HTTP client session for multiple requests if needed
    async with httpx.AsyncClient() as client:
        try:
            # 1. Rate Limiting Check
            if not rate_limiter.check_rate_limit(client_ip):
                # Logged within check_rate_limit
                raise RateLimitExceededError(f"Rate limit exceeded for IP: {client_ip}")

            # 2. Get ICO Configuration
            ico = ico_manager.get_ico(ico_id)
            if not ico:
                return f"ICO with id {ico_id} not found."

            # 3. Basic Validations
            # Check if ICO is active (using start/end times from loaded config)
            current_time = time.time()
            if not (ico.ico.start_time <= current_time <= ico.ico.end_time):
                 # Use default timestamps from config if not present in ico config? Or make mandatory?
                 # Assuming start/end times are mandatory in IcoConfig schema now.
                 raise InactiveICOError(f"ICO '{ico_id}' is not active. Current time: {current_time}, Start: {ico.ico.start_time}, End: {ico.ico.end_time}")

            if amount <= 0:
                raise InvalidTransactionError("Invalid amount. Must be greater than 0.")

            # 4. Calculate Required SOL / Expected SOL from Sale
            # Use the dedicated pricing module
            required_or_expected_sol = pricing.calculate_token_price(amount, ico, is_sell=sell)

            # 5. Validate Payment Transaction Signature Format
            try:
                tx_signature = Signature.from_string(payment_transaction)
            except ValueError as e:
                raise InvalidTransactionError(f"Invalid transaction signature format: {e}")

            # --- Buy Logic ---
            if not sell:
                # 6. Validate the SOL payment transaction using solana_utils
                payer = await solana_utils.validate_payment_transaction(client, tx_signature, required_or_expected_sol)
                logger.info(f"Payment validated for {ico_id} buy from {payer}. Required SOL: {required_or_expected_sol:.9f}")

                # 7. Create and send the token transfer using solana_utils
                token_transfer_tx_hash = await solana_utils.create_and_send_token_transfer(client, payer, amount, ico_id)
                logger.info(f"Token transfer successful for {ico_id} buy. Tx: {token_transfer_tx_hash}")

                # 8. Update total minted tokens using ico_manager
                ico_manager.increment_tokens_minted(ico_id, amount)

                # 9. Format success response
                token_amount_ui = amount / (10 ** ico.token.decimals)
                return (f"Successfully purchased {token_amount_ui:.{ico.token.decimals}f} {ico.token.symbol} "
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
                logger.warning(f"Sell functionality requested for {ico_id} but not fully implemented.")
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
            logger.warning(f"Token operation attempt on inactive ICO '{ico_id}': {e}, client_ip: {client_ip}")
            return str(e)
        except InsufficientFundsError as e:
            logger.warning(f"Insufficient funds for token operation on '{ico_id}': {e}, client_ip: {client_ip}")
            return str(e)
        except InvalidTransactionError as e:
            logger.error(f"Invalid transaction provided for token operation on '{ico_id}': {e}, client_ip: {client_ip}")
            return str(e)
        except TransactionFailedError as e:
            logger.error(f"Blockchain transaction failed for token operation on '{ico_id}': {e}, client_ip: {client_ip}")
            return str(e)
        except ValueError as e: # Catch config/calculation errors
             logger.error(f"Configuration or calculation error for '{ico_id}': {e}, client_ip: {client_ip}")
             return f"Error processing request: {e}"
        except Exception as e:
            logger.exception(f"Unexpected error processing token operation for '{ico_id}': {e}, client_ip: {client_ip}")
            return f"An unexpected server error occurred: {e}"


@mcp.tool()
async def get_discount(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    amount: int = Field(..., description="The amount of tokens held (in base units)."),
) -> str:
    """Gets a discount based on the amount of tokens held (example utility)."""
    try:
        ico = ico_manager.get_ico(ico_id)
        if not ico:
            return f"ICO with id {ico_id} not found."

        # Example logic (remains simple for now)
        # Convert amount from base units for calculation if logic depends on token amount
        token_amount = amount / (10**ico.token.decimals)
        discount_percentage = token_amount / 1000 * 0.01 # 1% per 1000 tokens
        discount_percentage = min(discount_percentage, 0.1) # Cap at 10%

        return f"Discount based on {token_amount} {ico.token.symbol}: {discount_percentage*100:.2f}%"
    except Exception as e:
        logger.exception(f"Error getting discount for ICO {ico_id}: {e}")
        return f"Error getting discount: {e}"


# --- Main Execution ---
if __name__ == "__main__":
    logger.info("Starting Solana ICO MCP Server...")
    # The ico_manager loads data on import
    logger.info(f"Loaded {len(ico_manager.ico_data)} ICO(s).")
    asyncio.run(mcp.run(transport="stdio"))
    logger.info("Solana ICO MCP Server stopped.")