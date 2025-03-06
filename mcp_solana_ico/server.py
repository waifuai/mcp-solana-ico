import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

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


class ICO(BaseModel):
    token: Token
    price_per_token: float = Field(
        description="Price of one token in SOL (not lamports)"
    )
    tokens_available: int = Field(
        description="Number of tokens available for sale (in base units)"
    )
    start_time: int = Field(description="Start time of the ICO (Unix timestamp)")
    end_time: int = Field(description="End time of the ICO (Unix timestamp)")

    @classmethod
    def from_config(cls) -> "ICO":
        """Loads ICO details from configuration."""
        # In a real application, load this from a config file, database, or environment variables

        return cls(
            token=Token.from_config(),
            price_per_token=TOKEN_PRICE_PER_LAMPORTS,
            tokens_available=500_000_000
            * (
                10**9
            ),  # 50% of total supply, accounting for decimals.
            start_time=ICO_START_TIMESTAMP,
            end_time=ICO_END_TIMESTAMP,
        )


# --- Server Setup ---
mcp = FastMCP(name="Solana ICO Server")
ico_data = ICO.from_config()


# --- Helper Functions ---


def lamports_to_sol(lamports: int) -> float:
    """Convert lamports to SOL."""
    return lamports / LAMPORTS_PER_SOL


def sol_to_lamports(sol: float) -> int:
    """Convert SOL to lamports."""
    return int(sol * LAMPORTS_PER_SOL)

async def _validate_payment_transaction(client: httpx.AsyncClient, tx_signature: Signature, amount: int) -> Pubkey:
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
    required_sol = (
        amount / (10**ico_data.token.decimals) * ico_data.price_per_token
    )
    if transfer_amount_sol < required_sol:
        raise InsufficientFundsError(
            f"Insufficient payment. Required: {required_sol} SOL. Received: {transfer_amount_sol} SOL"
        )

    payer = Pubkey.from_string(transfer_info["source"])
    return payer

async def _create_and_send_token_transfer(client: httpx.AsyncClient, payer: Pubkey, amount: int) -> str:
    """Creates and sends the token transfer instruction."""
    token_account = get_token_account(payer)

    transfer_ix = transfer_checked(
        TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=get_associated_token_address(
                ICO_WALLET.pubkey(), TOKEN_MINT_ADDRESS
            ),
            mint=TOKEN_MINT_ADDRESS,
            dest=token_account,
            owner=ICO_WALLET.pubkey(),
            amount=amount,
            decimals=ico_data.token.decimals,
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
async def get_ico_info(context: Context) -> str:
    """Get information about the current ICO."""
    return ico_data.model_dump_json(indent=2)


# --- MCP Tools ---


@mcp.tool()
async def buy_tokens(
    context: Context,
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
) -> str:
    async with httpx.AsyncClient() as client:
        try:
            # Rate Limiting Check
            if not check_rate_limit(client_ip):
                raise RateLimitExceededError(f"Rate limit exceeded for IP: {client_ip}")

            # 1.  Basic Validations
            if not ICO_START_TIMESTAMP <= time.time() <= ICO_END_TIMESTAMP:
                raise InactiveICOError("ICO is not active.")

            if amount <= 0:
                raise InvalidTransactionError("Invalid amount. Must be greater than 0.")

            if amount > ico_data.tokens_available:
                raise InsufficientFundsError("Not enough tokens available for purchase.")

            try:
                tx_signature = Signature.from_string(payment_transaction)
            except ValueError as e:
                raise InvalidTransactionError(f"Invalid transaction signature: {e}")

            payer = await _validate_payment_transaction(client, tx_signature, amount)
            tx_hash = await _create_and_send_token_transfer(client, payer, amount)

            ico_data.tokens_available -= amount
            logger.info(f"Successfully processed purchase of {amount} tokens. tx_hash: {tx_hash}, client_ip: {client_ip}")
            return f"Successfully purchased {amount / (10 ** ico_data.token.decimals)} {ico_data.token.symbol}. Payment of {lamports_to_sol(sol_to_lamports(amount / (10**ico_data.token.decimals) * ico_data.price_per_token)):.6f} SOL received (txid: {payment_transaction}). Token transfer txid: {tx_hash}"

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