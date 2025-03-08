import asyncio
import json
import os
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import uuid
import httpx
from pydantic import BaseModel, Field
from solders.hash import Hash as Blockhash
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.rpc.config import RpcTransactionConfig
from solders.rpc.responses import SendTransactionResp, SimulateTransactionResp
from solders.signature import Signature
from solders.transaction import Transaction
from solders.system_program import transfer, TransferParams
# Use spl token constants instead of hardcoding addresses.
from spl.token.constants import (
    ASSOCIATED_TOKEN_PROGRAM_ID,
    TOKEN_PROGRAM_ID,
    WRAPPED_SOL_MINT,
)  # Fixed import
from spl.token.instructions import (
    TokenInstruction,
    TransferCheckedParams,
    create_associated_token_account,
    get_associated_token_address,
    transfer_checked,
)
from spl.token._layouts import ACCOUNT_LAYOUT
from spl.token.types import TokenAccountOpts

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.utilities.logging import get_logger
from mcp_solana_ico.errors import (
    InactiveICOError,
    InsufficientFundsError,
    InvalidTransactionError,
    TransactionFailedError,
    TokenBalanceError,
    RateLimitExceededError
)
from mcp_solana_ico.utils import get_token_account
from mcp_solana_ico import dex
from mcp_solana_ico import affiliates
from mcp_solana_ico import actions
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

# Replace these with your actual values and program IDs
RPC_ENDPOINT = os.getenv("RPC_ENDPOINT", "http://localhost:8899")
# Replace with your token mint address (must be a Pubkey)
TOKEN_MINT_ADDRESS = Pubkey.from_string(
    os.getenv("TOKEN_MINT_ADDRESS", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
)  # Placeholder: use *your* mint
ICO_START_TIMESTAMP = int(os.getenv("ICO_START_TIMESTAMP", "0"))
ICO_END_TIMESTAMP = int(os.getenv("ICO_END_TIMESTAMP", "0"))
# Replace with your desired price (e.g., 0.000001 SOL per token)
TOKEN_PRICE_PER_LAMPORTS = float(os.getenv("TOKEN_PRICE_PER_LAMPORTS", "0.000001"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# Load these from a secure configuration (e.g., environment variables, secrets manager)
# DO NOT hardcode your private key in a real application. This is just for the example.
# Here, we use a single keypair for both the ICO wallet *and* the fee payer.  In a
# real application, these would be separate, and the ICO wallet would be secured
# extremely carefully.
ICO_WALLET = Keypair.from_seed(
    bytes(
        [int(x) for x in os.getenv("ICO_WALLET_SEED", "1" * 32).split(",")]
    )
)


# SOL in lamports
LAMPORTS_PER_SOL = 10**9

# Token decimals
TOKEN_DECIMALS = 9

logger = get_logger(__name__)

# --- Rate Limiting ---
rate_limit_cache: Dict[str, Tuple[int, int]] = {}  # {ip: (count, timestamp)}


def check_rate_limit(ip: str) -> bool:
    """Checks if the given IP address has exceeded the rate limit."""
    now = int(time.time())
    if ip in rate_limit_cache:
        count, timestamp = rate_limit_cache[ip]
        if now - timestamp < 60:  # Within the last minute
            if count >= RATE_LIMIT_PER_MINUTE:
                return False  # Rate limit exceeded
            else:
                rate_limit_cache[ip] = (count + 1, timestamp)
            return True  # Rate limit not exceeded
        else:  # Reset after a minute
            rate_limit_cache[ip] = (1, now)
        return True
    else:
        rate_limit_cache[ip] = (1, now)
    return True

# --- Pydantic Models ---


class Token(BaseModel):
    name: str = Field(description="Name of the token")
    symbol: str = Field(description="Symbol of the token")
    decimals: int = Field(description="Number of decimal places for the token")
    total_supply: int = Field(description="Total supply of tokens in base units")
    token_address: str = Field(description="The on-chain address of the token")

    @classmethod
    def from_config(cls) -> "Token":
        """Loads token details from (in this example) hardcoded values."""
        return cls(
            name="My Token",
            symbol="MTK",
            decimals=9,
            total_supply=1_000_000_000 * (10**9),  # 1 million tokens * 10^9 (adjust for decimals)
            token_address=str(TOKEN_MINT_ADDRESS),
        )


class CurveType(str, Enum):
    fixed = "fixed"
    linear = "linear"
    exponential = "exponential"
    sigmoid = "sigmoid"
    custom = "custom"


class ICO(BaseModel):
    ico_id: str = Field(description="Unique identifier for the ICO")
    token: Token
    curve_type: CurveType = Field(
        CurveType.fixed, description="The type of bonding curve to use."
    )
    fixed_price: Optional[float] = Field(
        None, description="The fixed price of the token (in SOL per token). Only used if curve_type is 'fixed'."
    )
    initial_price: Optional[float] = Field(
        None, description="The initial price of the token (in SOL per token). Used for bonding curves."
    )
    slope: Optional[float] = Field(
        None, description="The slope of the linear bonding curve. Only used if curve_type is 'linear'."
    )
    growth_rate: Optional[float] = Field(
        None, description="The growth rate of the exponential bonding curve. Only used if curve_type is 'exponential'."
    )
    custom_formula: Optional[str] = Field(
        None, description="The custom formula for the bonding curve. Only used if curve_type is 'custom'."
    )
    start_time: int = Field(description="Start time of the ICO (Unix timestamp)")
    end_time: int = Field(description="End time of the ICO (Unix timestamp)")

    @classmethod
    def from_config(cls, ico_id: str) -> "ICO":
        """Loads ICO details from configuration."""
        # In a real application, load this from a config file, database, or environment variables

        curve_type_str = os.getenv("CURVE_TYPE", "fixed")
        try:
            curve_type = CurveType(curve_type_str)
        except ValueError:
            curve_type = CurveType.fixed  # Default to fixed if invalid

        return cls(
            ico_id=ico_id,
            token=Token.from_config(),
            curve_type=curve_type,
            fixed_price=float(os.getenv("FIXED_PRICE", str(TOKEN_PRICE_PER_LAMPORTS)))
            if curve_type == CurveType.fixed
            else None,
            initial_price=float(os.getenv("INITIAL_PRICE", "0.0000001"))
            if curve_type != CurveType.fixed
            else None,
            slope=float(os.getenv("SLOPE", "0.000000001"))
            if curve_type == CurveType.linear
            else None,
            growth_rate=float(os.getenv("GROWTH_RATE", "0.0000000001"))
            if curve_type == CurveType.exponential
            else None,
            custom_formula=os.getenv("CUSTOM_FORMULA") if curve_type == CurveType.custom else None,
            start_time=ICO_START_TIMESTAMP,
            end_time=ICO_END_TIMESTAMP,
        )


# --- Server Setup ---
mcp = FastMCP(name="Solana ICO Server")
#ico_data = ICO.from_config() #Removed this line
#ico_data: Dict[str, ICO] = {} #Added this line
ico_data: Dict[str, ICO] = {}

# Load ICO configurations from environment variables
ico_ids = os.getenv("ICO_IDS", "main_ico").split(",")
for ico_id in ico_ids:
    ico_data[ico_id] = ICO.from_config(ico_id)


# --- Helper Functions ---


def lamports_to_sol(lamports: int) -> float:
    """Convert lamports to SOL."""
    return lamports / LAMPORTS_PER_SOL


def sol_to_lamports(sol: float) -> int:
    """Convert SOL to lamports."""
    return int(sol * LAMPORTS_PER_SOL)

async def _validate_payment_transaction(client: httpx.AsyncClient, tx_signature: Signature, required_sol: float) -> Pubkey:
    """Validates the payment transaction and returns the payer's public key."""
    transaction_response = await client.post(
        RPC_ENDPOINT,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                str(tx_signature),
                {
                    "encoding": "jsonParsed",
                    "commitment": "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        },
    )
    transaction_response.raise_for_status()
    transaction_data = transaction_response.json()

    if "error" in transaction_data:
        raise InvalidTransactionError(f"Error fetching transaction: {transaction_data['error']}")

    tx = transaction_data["result"]["transaction"]

    # **CRUCIAL VALIDATION:**  Thoroughly check *ALL* aspects of this transaction:
    instructions = tx["message"]["instructions"]
    if (
        not instructions
        or instructions[0]["programId"] != "11111111111111111111111111111111"
    ):  # System Program ID
        raise InvalidTransactionError("Invalid transaction. Not a system program transfer.")

    transfer_info = instructions[0]["parsed"]["info"]
    transfer_amount_lamports = transfer_info["lamports"]
    transfer_amount_sol = transfer_amount_lamports / LAMPORTS_PER_SOL
    
    if transfer_amount_sol < required_sol:
        raise InsufficientFundsError(
            f"Insufficient payment. Required: {required_sol} SOL. Received: {transfer_amount_sol} SOL"
        )

    payer = Pubkey.from_string(transfer_info["source"])
    return payer

async def _create_and_send_token_transfer(client: httpx.AsyncClient, payer: Pubkey, amount: int, ico_id: str) -> str:
    """Creates and sends the token transfer instruction."""
    token_account = get_token_account(payer)

    transfer_ix = transfer_checked(
        TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=get_associated_token_address(
                ICO_WALLET.pubkey(), Pubkey.from_string(ico_data[ico_id].token.token_address)
            ),
            mint=Pubkey.from_string(ico_data[ico_id].token.token_address),
            dest=token_account,
            owner=ICO_WALLET.pubkey(),
            amount=amount,
            decimals=ico_data[ico_id].token.decimals,
            signers=[],
        )
    )

    blockhash_resp = await client.get(
        RPC_ENDPOINT,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "finalized"}],
        },
    )
    blockhash_resp.raise_for_status()
    blockhash_str = blockhash_resp.json()["result"]["value"]["blockhash"]
    blockhash = Blockhash.from_string(blockhash_str)
    message = Message([transfer_ix])
    txn = Transaction.new_signed_with_payer(
        [transfer_ix],
        payer=ICO_WALLET.pubkey(),
        signers=[ICO_WALLET],
        recent_blockhash=blockhash,
    )
    resp = await client.post(
        RPC_ENDPOINT,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                txn.serialize().hex(),
                {"encoding": "hex"},
            ],
        },
    )
    resp.raise_for_status()
    tx_hash = resp.json()["result"]

    # Wait for transaction confirmation
    try:
        await client.confirm_transaction(tx_hash)
    except Exception as e:
        logger.error(f"Transaction confirmation failed: {e}")
        raise TransactionFailedError(f"Transaction confirmation failed: {e}")

    return tx_hash

# --- MCP Resources ---


@mcp.resource("ico://info")
async def get_ico_info(context: Context, ico_id: str = Field(..., description="The ICO ID.")) -> str:
    """Get information about the current ICO."""
    if ico_id not in ico_data:
        return f"ICO with id {ico_id} not found."
    return ico_data[ico_id].model_dump_json(indent=2)

@mcp.resource("affiliate://register")
async def register_affiliate(context: Context) -> str:
    """Registers a user as an affiliate and returns a Solana Blink URL."""
    affiliate_id = affiliates.generate_affiliate_id()
    affiliates.store_affiliate_data(affiliate_id, {})  # Store basic affiliate data

    # Generate Action API URL
    action_api_url = f"/buy_tokens_action?affiliate_id={affiliate_id}"

    # Generate Solana Blink URL
    blink_url = "solana-action:" + quote(action_api_url)

    return f"Affiliate registered successfully! Your Solana Blink URL is: {blink_url}"


# --- MCP Tools ---

total_tokens_minted: Dict[str, int] = {}  # Initialize total tokens minted for each ICO

def calculate_token_price(amount: int, ico: ICO) -> float:
    """Calculates the token price based on the bonding curve."""
    ico_id = ico.ico_id
    if ico_id not in total_tokens_minted:
        total_tokens_minted[ico_id] = 0

    if ico.curve_type == CurveType.fixed:
        if ico.fixed_price is None:
            raise ValueError("Fixed price is not set.")
        return amount / (10**ico.token.decimals) * ico.fixed_price
    elif ico.curve_type == CurveType.linear:
        if ico.initial_price is None or ico.slope is None:
            raise ValueError("Initial price or slope is not set for linear curve.")
        return amount / (10**ico.token.decimals) * (ico.initial_price + ico.slope * total_tokens_minted[ico_id])
    elif ico.curve_type == CurveType.exponential:
        if ico.initial_price is None or ico.growth_rate is None:
            raise ValueError("Initial price or growth rate is not set for exponential curve.")
        return amount / (10**ico.token.decimals) * (ico.initial_price * (1 + ico.growth_rate)**total_tokens_minted[ico_id])
    elif ico.curve_type == CurveType.custom:
        if ico.custom_formula is None:
            raise ValueError("Custom formula is not set.")
        try:
            # WARNING: Using eval() can be dangerous.  Sanitize inputs carefully!
            price = eval(ico.custom_formula, {"initial_price": ico.initial_price, "total_tokens_minted": total_tokens_minted[ico_id]})
            return amount / (10**ico.token.decimals) * price
        except Exception as e:
            raise ValueError(f"Error evaluating custom formula: {e}")
    else:
        raise ValueError(f"Invalid curve type: {ico.curve_type}")


@mcp.tool()
async def buy_tokens(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    amount: int = Field(
        ..., description="The number of tokens to purchase (in base units)."
    ),
    payment_transaction: str = Field(
        ...,
        description=(
            "The transaction signature of the SOL payment.  Provide a transaction "
            "that is already signed and contains all the required fields."
        ),
    ),
    client_ip: str = Field(..., description="The client's IP address."),
    affiliate_id: Optional[str] = Field(None, description="The affiliate ID (optional).")
) -> str:
    """Buys tokens from the ICO.

    Args:
        context: The MCP context.
        ico_id: The ICO ID.
        amount: The number of tokens to purchase (in base units).
        payment_transaction: The transaction signature of the SOL payment.
        client_ip: The client's IP address.
        affiliate_id (optional): The affiliate ID.

    Returns:
        A string indicating the success or failure of the purchase.
    """
    async with httpx.AsyncClient() as client:
        try:
            # Rate Limiting Check
            if not check_rate_limit(client_ip):
                raise RateLimitExceededError(f"Rate limit exceeded for IP: {client_ip}")

            # 1.  Basic Validations
            if ico_id not in ico_data:
                return f"ICO with id {ico_id} not found."
            ico = ico_data[ico_id]

            if not ICO_START_TIMESTAMP <= time.time() <= ICO_END_TIMESTAMP:
                raise InactiveICOError("ICO is not active.")

            if amount <= 0:
                raise InvalidTransactionError("Invalid amount. Must be greater than 0.")

            # Calculate required SOL based on the bonding curve
            required_sol = calculate_token_price(amount, ico)

            try:
                tx_signature = Signature.from_string(payment_transaction)
            except ValueError as e:
                raise InvalidTransactionError(f"Invalid transaction signature: {e}")

            payer = await _validate_payment_transaction(client, tx_signature, required_sol)

            # 2. Affiliate Handling
            if affiliate_id:
                affiliate_data = affiliates.get_affiliate_data(affiliate_id)
                if not affiliate_data:
                    logger.warning(f"Invalid affiliate ID: {affiliate_id}, client_ip: {client_ip}")
                    return "Invalid affiliate ID."
                
                # Calculate commission (10%)
                commission = required_sol * 0.1
                # TODO: Store commission details (affiliate_id, ico_id, amount, commission, timestamp)
                logger.info(f"Affiliate commission recorded: affiliate_id={affiliate_id}, ico_id={ico_id}, amount={amount}, commission={commission}, client_ip={client_ip}")

            tx_hash = await _create_and_send_token_transfer(client, payer, amount, ico_id)

            if ico_id not in total_tokens_minted:
                total_tokens_minted[ico_id] = 0
            total_tokens_minted[ico_id] += amount
            logger.info(f"Successfully processed purchase of {amount} tokens. tx_hash: {tx_hash}, client_ip: {client_ip}")
            return f"Successfully purchased {amount / (10 ** ico.token.decimals)} {ico.token.symbol} at a price of {required_sol:.6f} SOL. Payment received (txid: {payment_transaction}). Token transfer txid: {tx_hash}"

        except InactiveICOError as e:
            logger.warning(f"Attempted to purchase tokens during inactive ICO: {e}, client_ip: {client_ip}")
            return str(e)
        except InsufficientFundsError as e:
            logger.warning(f"Insufficient funds for token purchase: {e}, client_ip: {client_ip}")
            return str(e)
        except InvalidTransactionError as e:
            logger.error(f"Invalid transaction provided for token purchase: {e}, client_ip: {client_ip}")
            return str(e)
        except TransactionFailedError as e:
            logger.error(f"Token transfer transaction failed: {e}, client_ip: {client_ip}")
            return str(e)
        except RateLimitExceededError as e:
            logger.warning(f"Rate limit exceeded: {e}, client_ip: {client_ip}")
            return str(e)
        except Exception as e:  # pylint: disable=broad-except-clause
            logger.exception(f"Error processing purchase: {e}, client_ip: {client_ip}")
            return f"An error occurred: {e}"

@mcp.tool()
async def create_order(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    amount: int = Field(..., description="The amount of tokens to sell."),
    price: float = Field(..., description="The price per token."),
    owner: str = Field(..., description="The public key of the order owner."),
) -> str:
    """Creates a new order in the DEX."""
    try:
        owner_pubkey = Pubkey.from_string(owner)
        return await dex.create_order(context, ico_id, amount, price, owner_pubkey)
    except Exception as e:
        return f"Error creating order: {e}"

@mcp.tool()
async def cancel_order(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    order_id: int = Field(..., description="The ID of the order to cancel."),
    owner: str = Field(..., description="The public key of the order owner."),
) -> str:
    """Cancels an existing order in the DEX."""
    try:
        owner_pubkey = Pubkey.from_string(owner)
        return await dex.cancel_order(context, ico_id, order_id, owner_pubkey)
    except Exception as e:
        return f"Error cancelling order: {e}"

@mcp.tool()
async def execute_order(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    order_id: int = Field(..., description="The ID of the order to execute."),
    buyer: str = Field(..., description="The public key of the buyer."),
    amount: int = Field(..., description="The amount of tokens to buy."),
) -> str:
    """Executes an existing order in the DEX."""
    try:
        buyer_pubkey = Pubkey.from_string(buyer)
        return await dex.execute_order(context, ico_id, order_id, buyer_pubkey, amount)
    except Exception as e:
        return f"Error executing order: {e}"

@mcp.tool()
async def get_discount(
    context: Context,
    ico_id: str = Field(..., description="The ICO ID."),
    amount: int = Field(..., description="The amount of tokens to use for discount."),
) -> str:
    """Gets a discount based on the amount of tokens held."""
    try:
        if ico_id not in ico_data:
            return f"ICO with id {ico_id} not found."
        ico = ico_data[ico_id]

        # Example: 1% discount for every 1000 tokens
        discount = amount / 1000 * 0.01
        if discount > 0.1:
            discount = 0.1  # Cap at 10%

        return f"Discount: {discount:.2f}"
    except Exception as e:
        return f"Error getting discount: {e}"


async def get_token_balance(client: httpx.AsyncClient, account_pubkey: Pubkey) -> int:
    """Get the token balance of an account."""
    # Explicitly use jsonParsed encoding for token accounts
    resp = await client.post(
        RPC_ENDPOINT,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountBalance",
            "params": [str(account_pubkey), {"commitment": "processed"}],
        },
    )
    resp.raise_for_status()
    result = resp.json()
    return int(result["result"]["value"]["amount"])


if __name__ == "__main__":
    import asyncio

    asyncio.run(mcp.run(transport="stdio"))