# MCP Solana ICO Server

This project provides a simplified example of an MCP (Model Context Protocol) server for a Solana ICO (Initial Coin Offering). It demonstrates the core concepts and structure for building an MCP server that interacts with the Solana blockchain.

**Disclaimer:** This is a simplified example and is not intended for production use. A real-world ICO would require significantly more complexity, security measures, and error handling.

## Requirements

*   Python 3.11+
*   Poetry
*   Solana CLI
*   solana-test-validator

## Installation

1.  Install dependencies:

    ```bash
    poetry install
    ```

2.  Set up a Solana environment:

    *   Install the Solana CLI: <https://docs.solana.com/cli/install>
    *   Start a local Solana validator: `solana-test-validator`

## Usage

1.  Start the server:

    ```bash
    poetry run python mcp_solana_ico/server.py
    ```

2.  Interact with the server using an MCP client (e.g., the Claude Desktop App).

### Creating New ICOs

The server supports creating new ICOs via the `ico://create` resource. To create a new ICO, send a POST request to `/ico://create` with a JSON payload containing the ICO configuration. The configuration should conform to the `IcoConfigModel` schema defined in `mcp_solana_ico/schemas.py`.

Example configuration:

```json
{
  "token": {
    "name": "ExampleToken",
    "symbol": "EXT",
    "total_supply": 1000000,
    "decimals": 9
  },
  "ico": {
    "ico_id": "example_ico",
    "start_time": 1741500000,
    "end_time": 1773036000,
    "curve_type": "fixed",
    "fixed_price": 10000,
    "sell_fee_percentage": 0.02
  },
  "resources": []
}
```

### Sell Fee Functionality

The server now supports a sell fee, which is a percentage of the sale amount that is deducted when a user sells tokens back to the ICO. The `sell_fee_percentage` field in the ICO configuration determines the percentage of the fee.

## Testing

1.  Run the tests:

    ```bash
    poetry run pytest -m integration
    ```

## Key Files

*   `mcp_solana_ico/server.py`: The main server code, including the `buy_tokens` tool and `get_ico_info` resource.
*   `mcp_solana_ico/schemas.py`: Defines the `IcoConfigModel` schema for ICO configurations.
*   `tests/integration/test_ico_server.py`: Integration tests for the server.
*   `pyproject.toml`: Poetry configuration file.

## Security Considerations

*   **Never hardcode private keys in your code!** This example uses a hardcoded seed for demonstration purposes only. In a real application, you should load private keys from a secure source (e.g., environment variables, a secrets manager).
*   **Thoroughly validate all inputs and transactions.** The `buy_tokens` tool includes basic validation, but a real-world implementation would require much more robust checks to prevent attacks.
*   **Get a security audit.** Before launching any ICO, have your code audited by security professionals.

## Disclaimer

This project is for educational purposes only and should not be used as the basis for a real-world ICO.
