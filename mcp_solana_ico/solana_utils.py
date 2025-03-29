import httpx
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.hash import Hash as Blockhash
from solders.message import Message
from solders.transaction import Transaction
from spl.token.instructions import transfer_checked, TransferCheckedParams, get_associated_token_address
from spl.token.constants import TOKEN_PROGRAM_ID
import asyncio # Added asyncio import

# Assuming ico_manager holds the ico_data dictionary after refactoring
# We might need to pass ico_data or specific ICO config to some functions
from mcp_solana_ico import ico_manager
from mcp_solana_ico.errors import (
    InvalidTransactionError,
    InsufficientFundsError,
    TransactionFailedError,
    TokenBalanceError, # Added TokenBalanceError import
)
from mcp_solana_ico.config import (
    RPC_ENDPOINT,
    LAMPORTS_PER_SOL,
    ICO_WALLET,
    DEFAULT_TOKEN_MINT_ADDRESS, # Use default mint from config
)
from mcp.server.fastmcp.utilities.logging import get_logger # Assuming logger is needed

logger = get_logger(__name__) # Initialize logger if needed

# --- Token Account Utility ---

def get_token_account(payer: Pubkey, token_mint_address: Pubkey = DEFAULT_TOKEN_MINT_ADDRESS) -> Pubkey:
    """
    Gets the associated token account for a payer and a specific token mint.
    Uses the default token mint address from config if not provided.
    """
    # Note: This assumes one global TOKEN_MINT_ADDRESS or relies on passing it.
    # If each ICO can have a different mint, this needs adjustment.
    return get_associated_token_address(payer, token_mint_address)

# --- Transaction Validation & Creation ---

async def validate_payment_transaction(client: httpx.AsyncClient, tx_signature: Signature, required_sol: float) -> Pubkey:
    """
    Validates the SOL payment transaction and returns the payer's public key.
    """
    try:
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

        if transaction_data.get("error"):
            raise InvalidTransactionError(f"Error fetching transaction: {transaction_data['error']}")

        if not transaction_data.get("result"):
             raise InvalidTransactionError(f"Transaction not found or failed: {tx_signature}")

        tx = transaction_data["result"]["transaction"]
        meta = transaction_data["result"]["meta"]

        if meta and meta.get("err"):
             raise InvalidTransactionError(f"Transaction {tx_signature} failed on-chain: {meta['err']}")


        # **CRUCIAL VALIDATION:**
        instructions = tx["message"]["instructions"]
        if (
            not instructions
            or instructions[0].get("programId") != "11111111111111111111111111111111" # System Program ID as string
        ):
            raise InvalidTransactionError("Invalid transaction. Not a system program transfer.")

        # Ensure it's a transfer instruction
        if instructions[0].get("parsed", {}).get("type") != "transfer":
             raise InvalidTransactionError("Invalid transaction. Instruction is not a transfer.")

        transfer_info = instructions[0]["parsed"]["info"]
        transfer_amount_lamports = int(transfer_info["lamports"]) # Ensure integer
        transfer_amount_sol = transfer_amount_lamports / LAMPORTS_PER_SOL

        # Check destination is the ICO wallet
        destination = Pubkey.from_string(transfer_info["destination"])
        if destination != ICO_WALLET.pubkey():
             raise InvalidTransactionError(f"Invalid transaction destination. Expected {ICO_WALLET.pubkey()}, got {destination}")

        # Check sufficient payment
        # Add a small tolerance for potential float precision issues if necessary
        if transfer_amount_sol < required_sol:
            raise InsufficientFundsError(
                f"Insufficient payment. Required: {required_sol:.9f} SOL. Received: {transfer_amount_sol:.9f} SOL"
            )

        payer = Pubkey.from_string(transfer_info["source"])
        logger.info(f"Validated payment transaction {tx_signature} from {payer} for {transfer_amount_sol:.9f} SOL.")
        return payer

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error validating transaction {tx_signature}: {e.response.status_code} - {e.response.text}")
        raise InvalidTransactionError(f"HTTP error validating transaction: {e.response.status_code}")
    except KeyError as e:
        logger.error(f"Missing expected key in transaction data for {tx_signature}: {e}")
        raise InvalidTransactionError(f"Malformed transaction data received: Missing key {e}")
    except Exception as e:
        logger.exception(f"Unexpected error validating transaction {tx_signature}: {e}")
        raise InvalidTransactionError(f"Unexpected error validating transaction: {e}")


async def create_and_send_token_transfer(client: httpx.AsyncClient, payer: Pubkey, amount: int, ico_id: str) -> str:
    """
    Creates and sends the token transfer instruction from the ICO wallet to the payer.
    """
    ico_config = ico_manager.get_ico(ico_id)
    if not ico_config:
        # This should ideally be checked before calling this function
        raise ValueError(f"ICO configuration not found for ico_id: {ico_id}")

    try:
        token_mint_address = Pubkey.from_string(ico_config.token.token_address) # Use specific mint from ICO config
    except AttributeError:
         # Fallback or error if token_address isn't in the schema/config
         logger.warning(f"token_address not found in config for {ico_id}, using default.")
         token_mint_address = DEFAULT_TOKEN_MINT_ADDRESS
    except ValueError as e:
         logger.error(f"Invalid token_address format for {ico_id}: {ico_config.token.token_address}. Error: {e}")
         raise ValueError(f"Invalid token_address for {ico_id}")


    payer_token_account = get_token_account(payer, token_mint_address)
    ico_wallet_token_account = get_token_account(ICO_WALLET.pubkey(), token_mint_address)

    transfer_ix = transfer_checked(
        TransferCheckedParams(
            program_id=TOKEN_PROGRAM_ID,
            source=ico_wallet_token_account,
            mint=token_mint_address,
            dest=payer_token_account,
            owner=ICO_WALLET.pubkey(),
            amount=amount,
            decimals=ico_config.token.decimals, # Use decimals from specific ICO config
            signers=[], # The payer (ICO_WALLET) will sign the transaction
        )
    )

    try:
        # Get latest blockhash
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
        blockhash_data = blockhash_resp.json()["result"]["value"]
        blockhash = Blockhash.from_string(blockhash_data["blockhash"])
        last_valid_block_height = blockhash_data["lastValidBlockHeight"] # Added for better transaction handling

        message = Message.new_with_blockhash([transfer_ix], ICO_WALLET.pubkey(), blockhash)
        txn = Transaction([ICO_WALLET]).populate(message) # Populate transaction correctly

        # Send the transaction
        resp = await client.post(
            RPC_ENDPOINT,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    txn.serialize().hex(), # Send serialized transaction as hex string
                    {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"}, # Use base64 encoding, standard preflight
                ],
            },
        )
        resp.raise_for_status()
        tx_hash_str = resp.json()["result"]
        tx_signature = Signature.from_string(tx_hash_str) # Convert to Signature object
        logger.info(f"Sent token transfer transaction {tx_signature} for {amount} tokens to {payer} for ICO {ico_id}.")


        # Wait for transaction confirmation using getSignatureStatuses
        confirmed = False
        elapsed_time = 0
        timeout = 60 # seconds
        check_interval = 2 # seconds

        while not confirmed and elapsed_time < timeout:
            await asyncio.sleep(check_interval)
            elapsed_time += check_interval
            try:
                status_resp = await client.post(
                    RPC_ENDPOINT,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignatureStatuses",
                        "params": [[str(tx_signature)], {"searchTransactionHistory": True}],
                    }
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()["result"]["value"][0]

                if status_data:
                    if status_data["confirmationStatus"] in ["confirmed", "finalized"]:
                        if status_data["err"] is None:
                            confirmed = True
                            logger.info(f"Token transfer transaction {tx_signature} confirmed.")
                        else:
                            logger.error(f"Token transfer transaction {tx_signature} failed on-chain: {status_data['err']}")
                            raise TransactionFailedError(f"Token transfer failed: {status_data['err']}")
                    elif elapsed_time >= timeout:
                         logger.warning(f"Token transfer transaction {tx_signature} confirmation timed out.")
                         raise TransactionFailedError("Token transfer confirmation timed out.")
                elif elapsed_time >= timeout: # Also timeout if status is null after timeout period
                    logger.warning(f"Token transfer transaction {tx_signature} status not found after timeout.")
                    raise TransactionFailedError("Token transfer status not found after timeout.")

            except Exception as status_e:
                 logger.error(f"Error checking status for transaction {tx_signature}: {status_e}")
                 # Decide if we should retry or raise immediately
                 if elapsed_time >= timeout:
                     raise TransactionFailedError(f"Failed to confirm transaction status after timeout: {status_e}")


        if not confirmed:
             # This case should be covered by timeouts above, but as a safeguard
             raise TransactionFailedError("Token transfer failed to confirm.")

        return str(tx_signature)

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error sending token transfer for {ico_id} to {payer}: {e.response.status_code} - {e.response.text}")
        raise TransactionFailedError(f"HTTP error sending token transfer: {e.response.status_code}")
    except Exception as e:
        logger.exception(f"Unexpected error sending token transfer for {ico_id} to {payer}: {e}")
        raise TransactionFailedError(f"Unexpected error sending token transfer: {e}")


# --- Token Balance ---

async def get_token_balance(client: httpx.AsyncClient, account_pubkey: Pubkey) -> int:
    """Get the token balance of a specific token account."""
    try:
        resp = await client.post(
            RPC_ENDPOINT,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountBalance",
                "params": [str(account_pubkey), {"commitment": "confirmed"}], # Use confirmed commitment
            },
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get("error"):
            raise TokenBalanceError(f"Error fetching token balance for {account_pubkey}: {result['error']}")

        if not result.get("result") or "value" not in result["result"] or "amount" not in result["result"]["value"]:
             raise TokenBalanceError(f"Unexpected response format for token balance of {account_pubkey}")

        return int(result["result"]["value"]["amount"])
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching token balance for {account_pubkey}: {e.response.status_code} - {e.response.text}")
        raise TokenBalanceError(f"HTTP error fetching token balance: {e.response.status_code}")
    except Exception as e:
        logger.exception(f"Unexpected error fetching token balance for {account_pubkey}: {e}")
        raise TokenBalanceError(f"Unexpected error fetching token balance: {e}")