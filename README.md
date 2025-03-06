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

## Testing

1.  Run the tests:

    ```bash
    poetry run pytest -m integration
    ```

## Key Files

*   `mcp_solana_ico/server.py`: The main server code, including the `buy_tokens` tool and `get_ico_info` resource.
*   `tests/integration/test_ico_server.py`: Integration tests for the server.
*   `pyproject.toml`: Poetry configuration file.

## Security Considerations

*   **Never hardcode private keys in your code!** This example uses a hardcoded seed for demonstration purposes only. In a real application, you should load private keys from a secure source (e.g., environment variables, a secrets manager).
*   **Thoroughly validate all inputs and transactions.** The `buy_tokens` tool includes basic validation, but a real-world implementation would require much more robust checks to prevent attacks.
*   **Get a security audit.** Before launching any ICO, have your code audited by security professionals.

## Disclaimer

This project is for educational purposes only and should not be used as the basis for a real-world ICO.
