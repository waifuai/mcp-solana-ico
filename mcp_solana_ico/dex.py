import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple

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
from mcp_solana_ico.server import ico_data, TOKEN_PROGRAM_ID, ICO_WALLET, RPC_ENDPOINT

logger = get_logger(__name__)

# --- DEX Data Structures ---

class Order(BaseModel):
    ico_id: str
    amount: int
    price: float
    owner: Pubkey

# In-memory order book (replace with a more robust solution for production)
order_book: Dict[str, List[Order]] = {}

# --- DEX Functions ---

async def create_order(context: Context, ico_id: str, amount: int, price: float, owner: Pubkey) -> str:
    """Creates a new order in the order book."""
    if ico_id not in ico_data:
        return f"ICO with id {ico_id} not found."

    order = Order(ico_id=ico_id, amount=amount, price=price, owner=owner)
    if ico_id not in order_book:
        order_book[ico_id] = []
    order_book[ico_id].append(order)
    return f"Order created successfully for {amount} tokens of {ico_id} at price {price}."

async def cancel_order(context: Context, ico_id: str, order_id: int, owner: Pubkey) -> str:
    """Cancels an existing order in the order book."""
    if ico_id not in order_book:
        return f"No orders found for ICO with id {ico_id}."

    if order_id < 0 or order_id >= len(order_book[ico_id]):
        return f"Invalid order id: {order_id}."

    order = order_book[ico_id][order_id]
    if order.owner != owner:
        return f"You are not the owner of this order."

    del order_book[ico_id][order_id]
    return f"Order cancelled successfully."

async def execute_order(context: Context, ico_id: str, order_id: int, buyer: Pubkey, amount: int) -> str:
    """Executes an existing order in the order book."""
    if ico_id not in order_book:
        return f"No orders found for ICO with id {ico_id}."

    if order_id < 0 or order_id >= len(order_book[ico_id]):
        return f"Invalid order id: {order_id}."

    order = order_book[ico_id][order_id]

    if amount > order.amount:
        return f"Not enough tokens available in this order."

    # Transfer tokens from seller to buyer
    # This is a simplified example and does not handle slippage or partial fills
    try:
        async with httpx.AsyncClient() as client:
            # Get associated token accounts for buyer and seller
            buyer_token_account = get_token_account(buyer)
            seller_token_account = get_token_account(order.owner)

            # Transfer tokens from seller to buyer
            transfer_ix = transfer_checked(
                TransferCheckedParams(
                    program_id=TOKEN_PROGRAM_ID,
                    source=get_associated_token_address(
                        ICO_WALLET.pubkey(), Pubkey.from_string(ico_data[ico_id].token.token_address)
                    ),
                    mint=Pubkey.from_string(ico_data[ico_id].token.token_address),
                    dest=buyer_token_account,
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
                return f"Transaction failed: {e}"

            # Update order amount
            order.amount -= amount
            if order.amount == 0:
                del order_book[ico_id][order_id]

            return f"Order executed successfully. Transferred {amount} tokens of {ico_id} to {buyer}."

    except Exception as e:
        logger.exception(f"Error executing order: {e}")
        return f"An error occurred: {e}"