"""Integration tests for the ICO server."""

import asyncio

import pytest
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction

import mcp_solana_ico.server as ico_server
from mcp.client.session import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import (
    create_connected_server_and_client_session as client_session,
)
from mcp.types import TextContent
from spl.token.constants import TOKEN_PROGRAM_ID, ASSOCIATED_TOKEN_PROGRAM_ID
from spl.token.instructions import (
    create_associated_token_account,
    get_associated_token_address,
)

# Use a pytest fixture to set up the server and client
@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for pytest-asyncio."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def client() -> ClientSession:
    """Initialize an MCP client connected to the server."""
    server = FastMCP("Solana ICO Server")

    @server.resource("ico://info")
    async def get_ico_info(context):
        return ico_server.ico_data.model_dump_json(indent=2)

    @server.tool()
    async def buy_tokens(context, amount: int, payment_transaction: str):
        return await ico_server.buy_tokens(context, amount, payment_transaction)

    async with client_session(server._mcp_server) as client:
        yield client


@pytest.mark.anyio
async def test_get_ico_info(client: ClientSession):
    """Test retrieving ICO info."""
    result = await client.read_resource("ico://info")
    assert len(result.contents) == 1
    assert isinstance(result.contents[0], TextContent)
    assert "token_mint" in result.contents[0].text
    assert "price_per_token" in result.contents[0].text
    assert "ico_start_time" in result.contents[0].text
    assert "ico_end_time" in result.contents[0].text
    assert "tokens_available" in result.contents[0].text


@pytest.mark.anyio
async def test_list_tools(client: ClientSession):
    """Test that the list_tools method works."""
    tools = await client.list_tools()
    assert len(tools.tools) == 1
    assert tools.tools[0].name == "buy_tokens"
    assert "amount" in tools.tools[0].inputSchema["properties"]
    assert "payment_transaction" in tools.tools[0].inputSchema["properties"]