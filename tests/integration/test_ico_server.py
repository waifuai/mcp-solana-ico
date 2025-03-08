import pytest
import pytest_asyncio
import httpx
import os
import asyncio
from mcp_solana_ico import server
from mcp_solana_ico import errors
from mcp_solana_ico import actions
from mcp_solana_ico import affiliates
from mcp_solana_ico.server import LAMPORTS_PER_SOL, ICO_WALLET, ico_data, calculate_token_price
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from dotenv import load_dotenv
from unittest.mock import patch

load_dotenv()

@pytest.fixture(scope="session", autouse=True)
def load_test_env():
    # Load environment variables from .env file
    load_dotenv()

@pytest.fixture
def test_client():
    # Create a test client for the FastMCP server
    server.mcp.debug = True  # Enable debug mode for testing
    return httpx.AsyncClient(app=server.mcp.app, base_url="http://test")

@pytest.mark.asyncio
async def test_basic_functionality(test_client):
    # A simple test to check if the server is running
    response = await test_client.get("/ico://info?ico_id=main_ico")
    assert response.status_code == 200

@pytest.mark.asyncio
async def test_get_ico_info(test_client):
    # Call get_ico_info
    response = await test_client.get("/ico://info?ico_id=main_ico")
    assert response.status_code == 200

    # Assert that the returned information matches the ICO configuration
    ico = ico_data["main_ico"]
    expected_info = ico.model_dump()
    actual_info = response.json()
    assert actual_info == expected_info

@pytest.mark.asyncio
async def test_affiliate_registration(test_client):
    # Call affiliate://register
    response = await test_client.get("/affiliate://register")
    assert response.status_code == 200

    # Assert that it returns a valid Solana Blink URL
    blink_url = response.text
    assert blink_url.startswith("Affiliate registered successfully! Your Solana Blink URL is: solana-action:")

    # Extract the affiliate_id from the blink_url
    import urllib.parse
    query = urllib.parse.urlparse(blink_url).query
    affiliate_id = urllib.parse.parse_qs(query)["affiliate_id"][0]

    # Assert that the affiliate data is stored correctly
    # This requires accessing the affiliate_data dictionary in affiliates.py
    from mcp_solana_ico import affiliates
    affiliate_data = affiliates.get_affiliate_data(affiliate_id)
    assert affiliate_data is not None

@pytest.mark.asyncio
async def test_buy_tokens_action_get(test_client):
    # Send a GET request to /buy_tokens_action
    response = await test_client.get("/buy_tokens_action")
    assert response.status_code == 200

    # Assert that the response body contains the correct action metadata
    expected_metadata = {
        "type": "action",
        "icon": actions.ACTION_ICON_URL,
        "title": actions.ACTION_TITLE,
        "description": actions.ACTION_DESCRIPTION,
        "label": actions.ACTION_LABEL,
        "input": [
            {"name": "amount", "type": "number", "label": "Amount of tokens to buy"}
        ]
    }
    assert response.json() == expected_metadata

@pytest.mark.asyncio
async def test_buy_tokens_action_post(test_client):
    # Send a POST request to /buy_tokens_action with valid input data
    data = {"amount": 1000}  # Example amount
    response = await test_client.post("/buy_tokens_action", json=data)
    assert response.status_code == 200

    # Assert that the response body contains a valid serialized Solana transaction
    transaction = response.json().get("transaction")
    assert transaction is not None
    # Add more sophisticated transaction validation here if needed
    # For example, check if it's a valid hex string
    try:
        bytes.fromhex(transaction)
    except ValueError:
        pytest.fail("Transaction is not a valid hex string")

@pytest.mark.asyncio
async def test_get_discount(test_client):
    # Call get_discount with different token amounts
    response1 = await test_client.get("/get_discount?ico_id=main_ico&amount=1000")
    assert response1.status_code == 200
    assert response1.text == "Discount: 0.01"

    response2 = await test_client.get("/get_discount?ico_id=main_ico&amount=5000")
    assert response2.status_code == 200
    assert response2.text == "Discount: 0.05"

    response3 = await test_client.get("/get_discount?ico_id=main_ico&amount=15000")
    assert response3.status_code == 200
    assert response3.text == "Discount: 0.10"

@pytest.mark.asyncio
async def test_successful_token_purchase(test_client):
    # Mock the _validate_payment_transaction function to simulate a valid transaction
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

        # Set up a valid ICO
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "valid_transaction_signature"  # Placeholder

        # Calculate required SOL
        ico = ico_data[ico_id]
        required_sol = calculate_token_price(amount, ico)

        # Call buy_tokens with valid parameters
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
            },
        )
        assert response.status_code == 200

        # Assert that the response indicates success
        assert f"Successfully purchased {amount / (10 ** ico.token.decimals)} {ico.token.symbol} at a price of {required_sol:.6f} SOL." in response.text

@pytest.mark.asyncio
async def test_successful_token_purchase_with_affiliate(test_client):
    # Mock the _validate_payment_transaction function to simulate a valid transaction
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

        # Set up a valid ICO
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "valid_transaction_signature"  # Placeholder

        # Register an affiliate
        response = await test_client.get("/affiliate://register")
        assert response.status_code == 200
        blink_url = response.text
        import urllib.parse
        query = urllib.parse.urlparse(blink_url).query
        affiliate_id = urllib.parse.parse_qs(query)["affiliate_id"][0]

        # Calculate required SOL
        ico = ico_data[ico_id]
        required_sol = calculate_token_price(amount, ico)

        # Call buy_tokens with valid parameters, including the affiliate ID
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
                "affiliate_id": affiliate_id,
            },
        )
        assert response.status_code == 200

        # Assert that the response indicates success
        assert f"Successfully purchased {amount / (10 ** ico.token.decimals)} {ico.token.symbol} at a price of {required_sol:.6f} SOL." in response.text

        # Assert that the affiliate commission was recorded correctly
        # This requires accessing the affiliate_data dictionary in affiliates.py and checking for commission data
        from mcp_solana_ico import affiliates
        affiliate_data = affiliates.get_affiliate_data(affiliate_id)
        assert affiliate_data is not None
        # Since commission is not stored in affiliate_data, we can't directly assert it.
        # In a real implementation, you would need to query a database or other storage mechanism.

@pytest.mark.asyncio
async def test_inactive_ico_error(test_client):
    # Mock the ICO_START_TIMESTAMP and ICO_END_TIMESTAMP
    with patch("mcp_solana_ico.server.ICO_START_TIMESTAMP", 2147483647):  # Far future timestamp
        with patch("mcp_solana_ico.server.ICO_END_TIMESTAMP", 2147483648):  # Even further future timestamp
            # Mock the _validate_payment_transaction function to simulate a valid transaction
            with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
                mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

                # Set up a valid ICO
                ico_id = "main_ico"
                amount = 1000
                payment_transaction = "valid_transaction_signature"  # Placeholder

                # Call buy_tokens with valid parameters
                response = await test_client.post(
                    "/buy_tokens",
                    params={
                        "ico_id": ico_id,
                        "amount": amount,
                        "payment_transaction": payment_transaction,
                        "client_ip": "127.0.0.1",  # Example IP address
                    },
                )
                assert response.status_code == 200
                assert "ICO is not active." in response.text

    with patch("mcp_solana_ico.server.ICO_START_TIMESTAMP", 0):  # Far past timestamp
        with patch("mcp_solana_ico.server.ICO_END_TIMESTAMP", 1):  # Even further past timestamp
            # Mock the _validate_payment_transaction function to simulate a valid transaction
            with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
                mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

                # Set up a valid ICO
                ico_id = "main_ico"
                amount = 1000
                payment_transaction = "valid_transaction_signature"  # Placeholder

                # Call buy_tokens with valid parameters
                response = await test_client.post(
                    "/buy_tokens",
                    params={
                        "ico_id": ico_id,
                        "amount": amount,
                        "payment_transaction": payment_transaction,
                        "client_ip": "127.0.0.1",  # Example IP address
                    },
                )
                assert response.status_code == 200
                assert "ICO is not active." in response.text

@pytest.mark.asyncio
async def test_insufficient_funds_error(test_client):
    # Mock the _validate_payment_transaction function to raise InsufficientFundsError
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.side_effect = errors.InsufficientFundsError("Insufficient payment.")

        # Set up a valid ICO
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "invalid_transaction_signature"  # Placeholder

        # Call buy_tokens with valid parameters
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
            },
        )
        assert response.status_code == 200
        assert "Insufficient payment." in response.text

@pytest.mark.asyncio
async def test_invalid_transaction_error(test_client):
    # Mock the _validate_payment_transaction function to raise InvalidTransactionError
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.side_effect = errors.InvalidTransactionError("Invalid transaction.")

        # Set up a valid ICO
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "invalid_transaction_signature"  # Placeholder

        # Call buy_tokens with valid parameters
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
            },
        )
        assert response.status_code == 200
        assert "Invalid transaction." in response.text

@pytest.mark.asyncio
async def test_transaction_failed_error(test_client):
    # Mock the _create_and_send_token_transfer function to raise TransactionFailedError
    with patch("mcp_solana_ico.server._create_and_send_token_transfer") as mock_transfer:
        mock_transfer.side_effect = errors.TransactionFailedError("Transaction failed.")

        # Mock the _validate_payment_transaction function to simulate a valid transaction
        with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
            mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

            # Set up a valid ICO
            ico_id = "main_ico"
            amount = 1000
            payment_transaction = "valid_transaction_signature"  # Placeholder

            # Call buy_tokens with valid parameters
            response = await test_client.post(
                "/buy_tokens",
                params={
                    "ico_id": ico_id,
                    "amount": amount,
                    "payment_transaction": payment_transaction,
                    "client_ip": "127.0.0.1",  # Example IP address
                },
        )
        assert response.status_code == 200
        assert "Transaction failed." in response.text

@pytest.mark.asyncio
async def test_rate_limit_exceeded_error(test_client):
    # Mock the _validate_payment_transaction function to simulate a valid transaction
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

        # Set up a valid ICO
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "valid_transaction_signature"  # Placeholder
        client_ip = "127.0.0.1"

        # Make multiple requests to buy_tokens in quick succession
        for _ in range(server.RATE_LIMIT_PER_MINUTE + 1):
            response = await test_client.post(
                "/buy_tokens",
                params={
                    "ico_id": ico_id,
                    "amount": amount,
                    "payment_transaction": payment_transaction,
                    "client_ip": client_ip,
                },
            )

        # The last request should be rate limited
        assert response.status_code == 200
        assert "Rate limit exceeded" in response.text

@pytest.mark.asyncio
async def test_fixed_price_curve(test_client):
    # Mock the _validate_payment_transaction function to simulate a valid transaction
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

        # Set up a valid ICO with fixed price
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "valid_transaction_signature"  # Placeholder

        # Call buy_tokens with valid parameters
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
            },
        )
        assert response.status_code == 200
        # Add more sophisticated assertions here if needed

@pytest.mark.asyncio
async def test_linear_curve(test_client):
    # Mock the _validate_payment_transaction function to simulate a valid transaction
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

        # Set up a valid ICO with linear curve
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "valid_transaction_signature"  # Placeholder

        # Call buy_tokens with valid parameters
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
            },
        )
        assert response.status_code == 200
        # Add more sophisticated assertions here if needed

@pytest.mark.asyncio
async def test_exponential_curve(test_client):
    # Mock the _validate_payment_transaction function to simulate a valid transaction
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

        # Set up a valid ICO with exponential curve
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "valid_transaction_signature"  # Placeholder

        # Call buy_tokens with valid parameters
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
            },
        )
        assert response.status_code == 200
        # Add more sophisticated assertions here if needed

@pytest.mark.asyncio
async def test_custom_curve(test_client):
    # Mock the _validate_payment_transaction function to simulate a valid transaction
    with patch("mcp_solana_ico.server._validate_payment_transaction") as mock_validate:
        mock_validate.return_value = ICO_WALLET.pubkey()  # Simulate a valid payer

        # Set up a valid ICO with custom curve
        ico_id = "main_ico"
        amount = 1000
        payment_transaction = "valid_transaction_signature"  # Placeholder

        # Call buy_tokens with valid parameters
        response = await test_client.post(
            "/buy_tokens",
            params={
                "ico_id": ico_id,
                "amount": amount,
                "payment_transaction": payment_transaction,
                "client_ip": "127.0.0.1",  # Example IP address
            },
        )
        assert response.status_code == 200
        # Add more sophisticated assertions here if needed