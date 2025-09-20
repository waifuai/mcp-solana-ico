import asyncio # Added for async client usage
import httpx
import os
from typing import Dict, Any, Tuple
from flask import Flask, request, jsonify
from urllib.parse import quote
from solders.pubkey import Pubkey
from solders.hash import Hash as Blockhash
from solders.message import Message
from solders.transaction import Transaction
from solders.system_program import transfer, TransferParams
from spl.token.instructions import transfer_checked, TransferCheckedParams, get_associated_token_address
from spl.token.constants import ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_PROGRAM_ID, WRAPPED_SOL_MINT
from solders import signature

# Import from refactored modules
from mcp_solana_ico import ico_manager
from mcp_solana_ico import pricing
from mcp_solana_ico import config # Import config module
from mcp_solana_ico.schemas import IcoConfigModel # Import schema if needed for type hints

# Assuming logger might be useful here too
from mcp.server.fastmcp.utilities.logging import get_logger
logger = get_logger(__name__)


app = Flask(__name__)

# --- Action Metadata ---
# Consider loading these from config or ICO data if they vary per ICO
ACTION_TITLE = "Token Purchase"
ACTION_DESCRIPTION = "Buy tokens using this Blink."
ACTION_LABEL = "Buy Tokens"
# Action configuration is now imported from config.py

# --- CORS Headers ---
# Security: In production, specify allowed origins instead of '*'
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")

def get_cors_headers(origin: str) -> Dict[str, str]:
    """Get CORS headers with origin validation."""
    allowed_origin = "*"
    if "*" not in CORS_ALLOWED_ORIGINS:
        if origin in CORS_ALLOWED_ORIGINS:
            allowed_origin = origin
        else:
            allowed_origin = CORS_ALLOWED_ORIGINS[0] if CORS_ALLOWED_ORIGINS else "null"

    return {
        'Access-Control-Allow-Origin': allowed_origin,
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600',
        'Access-Control-Allow-Credentials': 'false'
    }

cors_headers = get_cors_headers("*")  # Default for non-request contexts

# --- Flask Routes ---

@app.route('/buy_tokens_action', methods=['OPTIONS'])
def handle_options_buy_tokens() -> Tuple[str, int, Dict[str, str]]:
    """Handles CORS preflight requests."""
    origin = request.headers.get('Origin', '*')
    cors_headers = get_cors_headers(origin)
    return '', 204, cors_headers

@app.route('/buy_tokens_action', methods=['GET'])
def get_buy_tokens_action_metadata() -> Tuple[Any, int, Dict[str, str]]:
    """Provides metadata for the Solana Action."""
    origin = request.headers.get('Origin', '*')
    cors_headers = get_cors_headers(origin)

    # TODO: Potentially make metadata dynamic based on query params (e.g., ico_id)
    metadata = {
        "icon": config.ACTION_ICON_URL,
        "title": ACTION_TITLE,
        "description": ACTION_DESCRIPTION,
        "label": ACTION_LABEL,
        "links": { # Added links for potential future actions
            "actions": [
                 # Example: Add other related actions if any
                 # { "label": "View ICO Info", "href": "/get_ico_info?ico_id=main_ico" }
            ]
        },
        # Parameters required by the POST request
        "parameters": [
            {"name": "amount", "label": "Amount of tokens to buy", "required": True}
            # Add ico_id parameter if supporting multiple ICOs via this action
            # {"name": "ico_id", "label": "ICO ID", "required": True, "defaultValue": "main_ico"}
        ]
    }
    # Note: The original code had "input" field, but spec usually uses "parameters"
    return jsonify(metadata), 200, cors_headers

@app.route('/buy_tokens_action', methods=['POST'])
async def post_buy_tokens_action() -> Tuple[Any, int, Dict[str, str]]:
    """Handles the POST request to create the token purchase transaction."""
    origin = request.headers.get('Origin', '*')
    cors_headers = get_cors_headers(origin)

    try:
        # Input validation - check Content-Type
        if not request.is_json:
            return jsonify({"message": "Content-Type must be application/json"}), 415, cors_headers

        payload = request.get_json()
        if not payload:
            return jsonify({"message": "Empty or invalid JSON payload"}), 400, cors_headers

        # Validate required fields
        if "account" not in payload:
            return jsonify({"message": "Account not provided in request"}), 400, cors_headers

        user_account_str = payload["account"]
        if not isinstance(user_account_str, str) or not user_account_str.strip():
            return jsonify({"message": "Account must be a non-empty string"}), 400, cors_headers

        # Validate public key format
        try:
            user_account_pubkey = Pubkey.from_string(user_account_str.strip())
        except ValueError as e:
            logger.warning(f"Invalid user account address: {user_account_str}")
            return jsonify({"message": f"Invalid user account address: {user_account_str}"}), 400, cors_headers

        # Get and validate amount parameter
        try:
            amount_str = payload.get("amount")
            if amount_str is None:
                return jsonify({"message": "Amount parameter is required"}), 400, cors_headers

            amount = int(amount_str)
            if amount <= 0:
                return jsonify({"message": "Amount must be positive"}), 400, cors_headers
            if amount > 10**18:  # Reasonable upper limit
                return jsonify({"message": "Amount is too large"}), 400, cors_headers

        except (ValueError, TypeError, OverflowError):
            return jsonify({"message": "Amount must be a valid integer"}), 400, cors_headers

        # Get ICO ID from request or use default
        ico_id = payload.get("ico_id", "main_ico").strip()
        if not ico_id or len(ico_id) > 100:
            return jsonify({"message": "Invalid ICO ID"}), 400, cors_headers

        # Fetch ICO Data
        ico = ico_manager.get_ico(ico_id)
        if not ico:
            logger.error(f"ICO not found for id: {ico_id} in actions endpoint")
            return jsonify({"message": f"ICO with id {ico_id} not found"}), 404, cors_headers

        # Construct Token Transfer Transaction
        try:
            # Determine token mint address for this ICO
            try:
                token_mint_address = Pubkey.from_string(ico.token.token_address)
            except AttributeError:
                logger.warning(f"token_address not found in config for {ico_id}, using default")
                token_mint_address = config.DEFAULT_TOKEN_MINT_ADDRESS
            except ValueError as e:
                logger.error(f"Invalid token_address format for {ico_id}: {ico.token.token_address}")
                return jsonify({"message": f"Internal server error: Invalid token configuration for {ico_id}"}), 500, cors_headers

            # Get Associated Token Accounts
            user_assoc_token_account = get_associated_token_address(user_account_pubkey, token_mint_address)
            ico_wallet_assoc_token_account = get_associated_token_address(config.ICO_WALLET.pubkey(), token_mint_address)

            transfer_ix = transfer_checked(
                TransferCheckedParams(
                    program_id=TOKEN_PROGRAM_ID,
                    source=ico_wallet_assoc_token_account,
                    mint=token_mint_address,
                    dest=user_assoc_token_account,
                    owner=config.ICO_WALLET.pubkey(),
                    amount=amount,
                    decimals=ico.token.decimals,
                    signers=[], # ICO_WALLET will sign the transaction
                )
            )
        except Exception as e:
            logger.exception(f"Error creating transfer instruction for {ico_id}: {e}")
            return jsonify({"message": "Error preparing transaction instruction"}), 500, cors_headers

        # Get Blockhash
        try:
            async with httpx.AsyncClient() as client:
                blockhash_resp = await client.get(
                    config.RPC_ENDPOINT,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getLatestBlockhash",
                        "params": [{"commitment": "finalized"}],
                    },
                    timeout=10.0  # Add timeout
                )
                blockhash_resp.raise_for_status()
                blockhash_data = blockhash_resp.json()["result"]["value"]
                blockhash = Blockhash.from_string(blockhash_data["blockhash"])
        except httpx.TimeoutException:
            logger.error("Timeout fetching blockhash")
            return jsonify({"message": "Service temporarily unavailable"}), 503, cors_headers
        except Exception as e:
            logger.exception(f"Error fetching blockhash: {e}")
            return jsonify({"message": "Error fetching latest blockhash"}), 500, cors_headers

        # Create and Serialize Transaction
        try:
            message = Message.new_with_blockhash(
                instructions=[transfer_ix],
                payer=config.ICO_WALLET.pubkey(),
                recent_blockhash=blockhash
            )
            # Transaction needs to be signed by the ICO_WALLET
            # The user (via Blink client) will sign with their key if needed for other instructions (not in this case)
            txn = Transaction([config.ICO_WALLET]).populate(message)

            # Serialize the *partially signed* transaction
            serialized_transaction = txn.serialize(verify_signatures=False).hex()

            # Validate transaction size (Solana limit is ~1232 bytes for serialized transaction)
            if len(serialized_transaction) > 3000:  # hex encoding doubles the size
                logger.error(f"Transaction too large for ICO {ico_id}")
                return jsonify({"message": "Transaction too large to process"}), 400, cors_headers

            # Return the serialized transaction for the user to sign and send
            response_body = {
                "transaction": serialized_transaction,
                "message": f"Transfer {amount / (10**ico.token.decimals):.{ico.token.decimals}f} {ico.token.symbol} tokens"
            }
            logger.info(f"Generated token purchase transaction for {ico_id}, amount: {amount}")
            return jsonify(response_body), 200, cors_headers

        except Exception as e:
            logger.exception(f"Error creating/serializing transaction for {ico_id}: {e}")
            return jsonify({"message": "Error finalizing transaction"}), 500, cors_headers

    except Exception as e:
        logger.exception(f"Unexpected error in post_buy_tokens_action: {e}")
        return jsonify({"message": "An unexpected server error occurred"}), 500, cors_headers

# --- Main Execution (for running Flask app directly) ---
if __name__ == '__main__':
    # Make sure ico_manager loads data if running standalone
    if not ico_manager.ico_data:
         logger.info("Loading ICO data for standalone Flask app run...")
         ico_manager.load_icos_from_config_files() # Ensure data is loaded

    port = config.ACTIONS_PORT
    logger.info(f"Starting Flask Action API server on port {port}...")
    # Consider using a production server like gunicorn/uvicorn instead of Flask's dev server
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", port=port, host="0.0.0.0")