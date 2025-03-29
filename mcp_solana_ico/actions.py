import asyncio # Added for async client usage
import httpx
import os
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
# TODO: Replace with a real icon URL, potentially configurable
ACTION_ICON_URL = os.getenv("ACTION_ICON_URL", "https://via.placeholder.com/150/0000FF/FFFFFF?text=ICO")

# --- CORS Headers ---
CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',  # Be more specific in production
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization', # Added Authorization if needed later
    'Access-Control-Max-Age': '3600'
}

# --- Flask Routes ---

@app.route('/buy_tokens_action', methods=['OPTIONS'])
def handle_options_buy_tokens():
    """Handles CORS preflight requests."""
    return '', 204, CORS_HEADERS

@app.route('/buy_tokens_action', methods=['GET'])
def get_buy_tokens_action_metadata():
    """Provides metadata for the Solana Action."""
    # TODO: Potentially make metadata dynamic based on query params (e.g., ico_id)
    metadata = {
        "icon": ACTION_ICON_URL,
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
    return jsonify(metadata), 200, CORS_HEADERS

@app.route('/buy_tokens_action', methods=['POST'])
async def post_buy_tokens_action():
    """Handles the POST request to create the token purchase transaction."""
    try:
        payload = request.get_json()
        if not payload or "account" not in payload:
             return jsonify({"message": "Account not provided in request."}), 400, CORS_HEADERS

        user_account_str = payload["account"]
        try:
            user_account_pubkey = Pubkey.from_string(user_account_str)
        except ValueError:
            return jsonify({"message": f"Invalid user account address: {user_account_str}"}), 400, CORS_HEADERS

        # --- Get Input Parameters ---
        # Use request.args for query parameters if needed (e.g., ico_id)
        # Use payload for POST body parameters
        try:
            # Assuming amount is passed in POST body based on GET metadata
            amount_str = payload.get("amount")
            if amount_str is None:
                 return jsonify({"message": "'amount' parameter is required."}), 400, CORS_HEADERS
            amount = int(amount_str) # Convert base units
            if amount <= 0:
                 return jsonify({"message": "'amount' must be positive."}), 400, CORS_HEADERS
        except (ValueError, TypeError):
             return jsonify({"message": "'amount' must be a valid integer."}), 400, CORS_HEADERS

        # TODO: Get ico_id from request if supporting multiple ICOs
        ico_id = 'main_ico' # Hardcoded for now

        # --- Fetch ICO Data ---
        ico = ico_manager.get_ico(ico_id)
        if not ico:
            logger.error(f"ICO not found for id: {ico_id} in actions endpoint.")
            return jsonify({"message": f"ICO with id {ico_id} not found"}), 404, CORS_HEADERS # Use 404

        # --- Calculate Price (using pricing module) ---
        # Note: Action API likely doesn't need price calculation here,
        # as the transaction it returns is *signed by the user*,
        # implying the user already agreed to the price/amount.
        # The primary job here is constructing the *token transfer* from ICO -> User.
        # Price calculation might be needed if the Action *also* handled the SOL payment.
        # required_sol = pricing.calculate_token_price(amount, ico, is_sell=False)
        # required_lamports = int(required_sol * config.LAMPORTS_PER_SOL)

        # --- Construct Token Transfer Transaction ---
        try:
            # Determine token mint address for this ICO
            try:
                token_mint_address = Pubkey.from_string(ico.token.token_address)
            except AttributeError:
                logger.warning(f"token_address not found in config for {ico_id}, using default.")
                token_mint_address = config.DEFAULT_TOKEN_MINT_ADDRESS
            except ValueError as e:
                 logger.error(f"Invalid token_address format for {ico_id}: {ico.token.token_address}. Error: {e}")
                 return jsonify({"message": f"Internal server error: Invalid token configuration for {ico_id}"}), 500, CORS_HEADERS

            # Get Associated Token Accounts
            user_assoc_token_account = get_associated_token_address(user_account_pubkey, token_mint_address)
            ico_wallet_assoc_token_account = get_associated_token_address(config.ICO_WALLET.pubkey(), token_mint_address)

            transfer_ix = transfer_checked(
                TransferCheckedParams(
                    program_id=TOKEN_PROGRAM_ID,
                    source=ico_wallet_assoc_token_account, # Source is ICO wallet's ATA
                    mint=token_mint_address,
                    dest=user_assoc_token_account, # Destination is user's ATA
                    owner=config.ICO_WALLET.pubkey(), # Owner of source ATA is ICO Wallet
                    amount=amount,
                    decimals=ico.token.decimals,
                    signers=[], # ICO_WALLET will sign the transaction
                )
            )
        except Exception as e:
             logger.exception(f"Error creating transfer instruction for {ico_id}: {e}")
             return jsonify({"message": "Error preparing transaction instruction."}), 500, CORS_HEADERS

        # --- Get Blockhash ---
        try:
            async with httpx.AsyncClient() as client:
                blockhash_resp = await client.get(
                    config.RPC_ENDPOINT, # Use config
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
        except Exception as e:
            logger.exception(f"Error fetching blockhash: {e}")
            return jsonify({"message": "Error fetching latest blockhash."}), 500, CORS_HEADERS

        # --- Create and Serialize Transaction ---
        try:
            message = Message.new_with_blockhash(
                instructions=[transfer_ix],
                payer=config.ICO_WALLET.pubkey(), # Payer is the ICO wallet (it signs)
                recent_blockhash=blockhash
            )
            # Transaction needs to be signed by the ICO_WALLET
            # The user (via Blink client) will sign with their key if needed for other instructions (not in this case)
            txn = Transaction([config.ICO_WALLET]).populate(message) # Sign with ICO Wallet

            # Serialize the *partially signed* transaction
            serialized_transaction = txn.serialize(verify_signatures=False).hex() # Send hex encoded

            # Return the serialized transaction for the user to sign and send
            response_body = {
                "transaction": serialized_transaction,
                "message": f"Transfer {amount / (10**ico.token.decimals)} {ico.token.symbol} tokens." # Optional message
            }
            return jsonify(response_body), 200, CORS_HEADERS

        except Exception as e:
            logger.exception(f"Error creating/serializing transaction for {ico_id}: {e}")
            return jsonify({"message": "Error finalizing transaction."}), 500, CORS_HEADERS

    except Exception as e:
        logger.exception(f"Unexpected error in post_buy_tokens_action: {e}")
        return jsonify({"message": "An unexpected server error occurred."}), 500, CORS_HEADERS

# --- Main Execution (for running Flask app directly) ---
if __name__ == '__main__':
    # Make sure ico_manager loads data if running standalone
    if not ico_manager.ico_data:
         logger.info("Loading ICO data for standalone Flask app run...")
         ico_manager.load_icos_from_config_files() # Ensure data is loaded

    port = int(os.getenv("ACTIONS_PORT", 5000))
    logger.info(f"Starting Flask Action API server on port {port}...")
    # Consider using a production server like gunicorn/uvicorn instead of Flask's dev server
    app.run(debug=os.getenv("FLASK_DEBUG", "False").lower() == "true", port=port, host="0.0.0.0")