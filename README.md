# MCP Solana ICO Server

This project provides a simplified example of an MCP (Model Context Protocol) server for a Solana ICO (Initial Coin Offering). It demonstrates core concepts for building an MCP server that interacts with the Solana blockchain, including:

*   **Multiple ICOs:** The server can manage multiple ICOs simultaneously, each with its own configuration.
*   **Bonding Curves:**  Support for various bonding curve types (fixed, linear, exponential, sigmoid, and custom) to determine token pricing.
*   **Sell Fee:**  A configurable sell fee is applied when users sell their tokens back to the ICO.
*   **Token Utility:** Includes a basic `get_discount` tool as an example of token utility.
*   **ICO Creation:**  A new `ico://create` resource enables dynamic creation of new ICOs through a JSON configuration.
* **Affiliate Program:** The affiliate program is now completely separate and resides in its own MCP server, `mcp_solana_affiliate`. This server has no dependencies on the affiliate program.

**Disclaimer:** This is a simplified example and is **not** intended for production use.  A real-world ICO would require significantly more complexity, rigorous security measures, thorough error handling, and professional auditing.  This project is for educational purposes and to demonstrate the basic structure of an MCP server interacting with Solana.

## Requirements

*   Python 3.11+
*   Poetry
*   Solana CLI
*   solana-test-validator

## Installation

1.  **Clone the repository:**

    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  **Install dependencies:**

    ```bash
    poetry install
    ```

3.  **Set up a Solana environment:**

    *   Install the Solana CLI: [https://docs.solana.com/cli/install](https://docs.solana.com/cli/install)
    *   Start a local Solana validator: `solana-test-validator`

## Configuration

### `.env` file

The server uses environment variables for configuration. Create a `.env` file in the root directory with the following content:

```
RPC_ENDPOINT="http://localhost:8899"  # Your Solana RPC endpoint
TOKEN_MINT_ADDRESS="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # Replace with your actual token mint address
ICO_START_TIMESTAMP=0  # Example: Unix timestamp for ICO start
ICO_END_TIMESTAMP=0  # Example: Unix timestamp for ICO end
TOKEN_PRICE_PER_LAMPORTS=0.000001  # Default fixed price (if used)
ICO_WALLET_SEED="1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1" # Replace with a secure method in production!
CURVE_TYPE="fixed"  # Can be fixed, linear, exponential, sigmoid, or custom
FIXED_PRICE=0.000001  # Only used if CURVE_TYPE is fixed
INITIAL_PRICE=0.0000001 # Only used if CURVE_TYPE is not fixed
SLOPE=0.000000001   # Only used if CURVE_TYPE is linear
GROWTH_RATE=0.0000000001  # Only used if CURVE_TYPE is exponential
CUSTOM_FORMULA="initial_price + slope * total_tokens_minted"  # Only used if CURVE_TYPE is custom
ICO_IDS="main_ico,secondary_ico" # Comma-separated list of ICO IDs
SELL_FEE_PERCENTAGE=0.02
RATE_LIMIT_PER_MINUTE=10
```

**Important:**  The `ICO_WALLET_SEED` is used here for demonstration purposes only.  **Never hardcode private keys in a production environment.**  Use a secure method like environment variables, a secrets manager (AWS Secrets Manager, HashiCorp Vault), or a dedicated key management system.

### ICO Configurations (ico_configs/)

ICO configurations are stored as JSON files in the `ico_configs/` directory.  The server loads all `.json` files from this directory on startup.  Each file represents a single ICO.

**Example (`ico_configs/main_ico.json`):**

```json
{
  "token": {
    "name": "My Main Token",
    "symbol": "MMT",
    "total_supply": 1000000000,
    "decimals": 9
  },
  "ico": {
    "ico_id": "main_ico",
    "start_time": 1704067200,
    "end_time": 1735689600,
    "curve_type": "linear",
    "initial_price": 0.000001,
    "slope": 0.00000001,
    "sell_fee_percentage": 0.01
  },
  "resources": []
}
```

**Example (`ico_configs/secondary_ico.json`):**

```json
{
    "token":{
      "name": "Secondary Token",
      "symbol": "SCT",
      "total_supply": 500000,
      "decimals": 6
   },
   "ico":{
      "ico_id":"secondary_ico",
      "start_time":1706745600,
      "end_time":1738368000,
      "curve_type":"fixed",
      "fixed_price":0.000005,
      "sell_fee_percentage":0.03
   },
   "resources":[]
}
```

**Fields:**

*   **`token`:**
    *   `name`: The name of the token.
    *   `symbol`: The token symbol.
    *   `total_supply`:  The total supply of tokens (in base units).
    *   `decimals`: The number of decimal places for the token.
*   **`ico`:**
    *   `ico_id`:  A unique identifier for the ICO.
    *   `start_time`: The start time of the ICO (Unix timestamp).
    *   `end_time`: The end time of the ICO (Unix timestamp).
    *   `curve_type`: The bonding curve type (`fixed`, `linear`, `exponential`, `sigmoid`, or `custom`).
    *   `fixed_price`: (Optional) The fixed price of the token (in SOL per token).  Used only if `curve_type` is `fixed`.
    *   `initial_price`: (Optional) The initial price for bonding curves.
    *   `slope`: (Optional) The slope for linear bonding curves.
    *   `growth_rate`: (Optional) The growth rate for exponential bonding curves.
    *   `custom_formula`: (Optional) A custom formula for calculating the price.
    *   `sell_fee_percentage`: The percentage fee applied when selling tokens back to the ICO.
* **`resources`:** Currently unused, for future expansion.

## Usage

1.  **Start the server:**

    ```bash
    poetry run python mcp_solana_ico/server.py
    ```

2.  **Interact with the server using an MCP client:** (e.g., the Claude Desktop App or a custom client).

## MCP Resources and Tools

### Resources

*   **`ico://info?ico_id=<ico_id>`:**  Gets information about a specific ICO.  Replace `<ico_id>` with the ID of the ICO.  Returns a JSON representation of the ICO configuration.

*   **`ico://create`:** Creates a new ICO dynamically.  Requires a JSON payload conforming to the `ICOConfig` schema (see `mcp_solana_ico/schemas.py`). The configuration will be validated, a new `ICO` instance created and added to `ico_data`, and the config saved to `ico_configs/`.

### Tools

*   **`buy_tokens`:**  Buys or sells tokens from an ICO.
    *   `ico_id`: (String) The ID of the ICO.
    *   `amount`: (Integer) The number of tokens to buy or sell (in base units).
    *   `payment_transaction`: (String) The transaction signature of the SOL payment (for buying) or the token transfer (for selling). *Must be a pre-signed transaction*.
    *   `client_ip`: (String) The client's IP address (for rate limiting).
    *   `sell`: (Boolean, optional)  `False` for buying (default), `True` for selling.
*   **`get_discount`:** Gets a discount based on the number of tokens held (example utility).
    *  `ico_id`: (String) The ID of the ICO.
    *  `amount`: (Integer) The amount of tokens to use for the discount calculation.

## Action API

The Action API allows the server to be integrated with Solana Blinks.  **Note:** The affiliate program is handled by a separate server (`mcp_solana_affiliate`), so the Action API here *does not* include any affiliate-related functionality.

* **`GET /buy_tokens_action`**: Returns metadata about the buy tokens action, such as the title, description, icon, and input fields.

* **`POST /buy_tokens_action`**: This endpoint receives input data (amount) from the Blink client, constructs the appropriate Solana transaction (a token transfer from the ICO wallet to the user's token account), serializes it, and returns the serialized transaction. The Blink client then prompts the user to sign and submit this transaction.

## Project Structure

```
mcp-solana-ico/           # Main project directory
├── mcp_solana_ico/       # Core ICO server package
│   ├── actions.py        # Action API endpoints
│   ├── errors.py         # Custom exception classes
│   ├── server.py         # Main ICO server code
│   ├── schemas.py        # Pydantic models for data validation
│   ├── utils.py          # Utility functions
│   └── __init__.py
├── plans/                # Planning documents
├── tests/                # Tests
├── .env                  # Environment variables for ICO server
├── .gitignore
├── LICENSE
├── pyproject.toml        # Poetry configuration for main project
├── pytest.ini            # Pytest configuration
└── README.md             # This file
```

## Key Improvements and Explanations
* **Completely Separated Affiliate Program:** The affiliate program is now fully contained within its own separate MCP server (`mcp_solana_affiliate`).
* **No Affiliate Logic in Main Server:** The `mcp_solana_ico` server has *no* code or configuration related to the affiliate program.
* **Action API Focus:** The Action API (`actions.py`) is solely responsible for constructing and returning Solana transactions for token purchases.

## Future Considerations

*   **Advanced Token Utility:** Develop more complex token utility features.
*   **User Accounts:** Implement a user account system.
*   **UI:**  Create a user interface for easier interaction with the server.
*   **Security Audit:**  Get a professional security audit before deploying to a production environment.
*   **Production-Ready Key Management:**  Implement a secure key management solution.