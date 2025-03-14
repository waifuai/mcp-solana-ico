from flask import Flask, request, jsonify
from urllib.parse import quote
from mcp_solana_ico import server
from mcp_solana_ico.server import ico_data, LAMPORTS_PER_SOL, TOKEN_PROGRAM_ID, ICO_WALLET
from solders.pubkey import Pubkey
from solders.hash import Hash as Blockhash
from solders.message import Message
from solders.transaction import Transaction
from solders.system_program import transfer, TransferParams
from spl.token.instructions import transfer_checked, TransferCheckedParams
from spl.token.constants import ASSOCIATED_TOKEN_PROGRAM_ID, TOKEN_PROGRAM_ID, WRAPPED_SOL_MINT
from spl.token.instructions import get_associated_token_address
import httpx
from solders import signature
import os

app = Flask(__name__)

ACTION_TITLE = "Token Purchase"
ACTION_DESCRIPTION = "Buy tokens using this Blink."
ACTION_LABEL = "Buy Tokens"
ACTION_ICON_URL = "URL_TO_YOUR_ICON"  # Replace with your icon URL

@app.route('/buy_tokens_action', methods=['OPTIONS'])
def handle_options_buy_tokens():
    headers = {
        'Access-Control-Allow-Origin': '*',  # Be more specific in production
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Max-Age': '3600'
    }
    return '', 204, headers

@app.route('/buy_tokens_action', methods=['GET'])
def get_buy_tokens_action_metadata():
    metadata = {
        "type": "action",
        "icon": ACTION_ICON_URL,
        "title": ACTION_TITLE,
        "description": ACTION_DESCRIPTION,
        "label": ACTION_LABEL,
        "input": [
            {"name": "amount", "type": "number", "label": "Amount of tokens to buy"}
        ]
    }
    headers = {'Access-Control-Allow-Origin': '*'}  # Be more specific in production
    return jsonify(metadata), 200, headers

@app.route('/buy_tokens_action', methods=['POST'])
async def post_buy_tokens_action():
    headers = {'Access-Control-Allow-Origin': '*'}  # Be more specific in production
    try:
        user_input = request.get_json()
        amount = user_input.get("amount")

        # Get ICO data (assuming ico_id is 'main_ico' for now)
        ico_id = 'main_ico'
        ico = ico_data.get(ico_id)
        if not ico:
            return jsonify({"error": f"ICO with id {ico_id} not found"}), 400, headers

        # Calculate required SOL
        required_sol = server.calculate_token_price(amount, ico)
        required_lamports = int(required_sol * LAMPORTS_PER_SOL)

        # Construct Solana transaction
        transfer_ix = transfer_checked(
            TransferCheckedParams(
                program_id=TOKEN_PROGRAM_ID,
                source=get_associated_token_address(
                    ICO_WALLET.pubkey(), Pubkey.from_string(ico.token.token_address)
                ),
                mint=Pubkey.from_string(ico.token.token_address),
                dest=Pubkey.from_string("YOUR_USER_TOKEN_ACCOUNT_ADDRESS"), # Replace with the user's token account
                owner=ICO_WALLET.pubkey(),
                amount=amount,
                decimals=ico.token.decimals,
                signers=[],
            )
        )

        # Get latest blockhash
        async with httpx.AsyncClient() as client:
            blockhash_resp = await client.get(
                server.RPC_ENDPOINT,
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

        # Serialize the transaction
        serialized_transaction = txn.serialize().hex()

        # Return the serialized transaction
        return jsonify({"transaction": serialized_transaction}), 200, headers

    except Exception as e:
        return jsonify({"error": str(e)}), 500, headers

if __name__ == '__main__':
    app.run(debug=True, port=5000)  # Run on port 5000 for example