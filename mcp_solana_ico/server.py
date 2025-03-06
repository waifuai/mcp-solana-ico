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

# Replace these with your actual values and program IDs
RPC_ENDPOINT = "http://localhost:8899"  # Replace if not running a local node
# Replace with your token mint address (must be a Pubkey)
TOKEN_MINT_ADDRESS = Pubkey.from_string(
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
)  # Placeholder: use *your* mint
ICO_START_TIMESTAMP = 0  # Replace with your ICO start timestamp
ICO_END_TIMESTAMP = 0  # Replace with your ICO end timestamp
# Replace with your desired price (e.g., 0.000001 SOL per token)
TOKEN_PRICE_PER_LAMPORTS = 0.000001

# Load these from a secure configuration (e.g., environment variables, secrets manager)
# DO NOT hardcode your private key in a real application. This is just for the example.
# Here, we use a single keypair for both the ICO wallet *and* the fee payer.  In a
# real application, these would be separate, and the ICO wallet would be secured
# extremely carefully.
ICO_WALLET = Keypair.from_seed(bytes([1] * 32))

# SOL in lamports
LAMPORTS_PER_SOL = 10**9

# Token decimals
TOKEN_DECIMALS = 9

logger = get_logger(__name__)

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
    name: str = Field(description="Name of the token")
    symbol: str = Field(description="Symbol of the token")
    decimals: int = Field(description="Number of decimal places for the token")
    total_supply: int = Field(description="Total supply of tokens in base units")
    token_address: str = Field(description="The on-chain address of the token")
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
            name="My Token",
            symbol="MTK",
            decimals=9,
            total_supply=1_000_000_000 * (10**9),  # 1 million tokens * 10^9 (adjust for decimals)
            token_address=str(TOKEN_MINT_ADDRESS),
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
) -> str:
    # 1.  Basic Validations
    if not ICO_START_TIMESTAMP <= time.time() <= ICO_END_TIMESTAMP:
        return "ICO is not active."

    if amount <= 0:
        return "Invalid amount. Must be greater than 0."

    if amount > ico_data.tokens_available:
        return "Not enough tokens available for purchase."

    try:
        tx_signature = Signature.from_string(payment_transaction)
    except ValueError as e:
        return f"Invalid transaction signature: {e}"

    async with httpx.AsyncClient() as client:
        try:
            # Fetch and parse the transaction
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
                return f"Error fetching transaction: {transaction_data['error']}"

            tx = transaction_data["result"]["transaction"]

            # **CRUCIAL VALIDATION:**  Thoroughly check *ALL* aspects of this transaction:
            instructions = tx["message"]["instructions"]
            if (
                not instructions
                or instructions[0]["programId"] != "11111111111111111111111111111111"
            ):  # System Program ID
                return "Invalid transaction. Not a system program transfer."

            transfer_info = instructions[0]["parsed"]["info"]
            transfer_amount_lamports = transfer_info["lamports"]
            transfer_amount_sol = transfer_amount_lamports / LAMPORTS_PER_SOL
            required_sol = (
                amount / (10**ico_data.decimals) * ico_data.price_per_token
            )
            if transfer_amount_sol < required_sol:
                return (
                    f"Insufficient payment. Required: {required_sol} SOL. Received: {transfer_amount_sol} SOL"
                )

            payer = Pubkey.from_string(transfer_info["source"])
            token_account = get_associated_token_address(payer, TOKEN_MINT_ADDRESS)

            # --- Create and send token transfer instruction ---
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
                    decimals=ico_data.decimals,
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

            ico_data.tokens_available -= amount
            logger.info(
                f"Successfully purchased {amount / (10 ** ico_data.decimals)} {ico_data.symbol}. Payment of {transfer_amount_sol:.6f} SOL received (txid: {payment_transaction}). Token transfer txid: {tx_hash}"
            )
            return f"Successfully purchased {amount / (10 ** ico_data.decimals)} {ico_data.symbol}. Payment of {transfer_amount_sol:.6f} SOL received (txid: {payment_transaction}). Token transfer txid: {tx_hash}"

        except Exception as e:  # pylint: disable=broad-except-clause
            logger.exception("Error occurred during token purchase")
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