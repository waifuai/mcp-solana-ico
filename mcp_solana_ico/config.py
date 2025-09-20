import os
import logging
from typing import Optional
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from dotenv import load_dotenv

# Import custom errors
from mcp_solana_ico.errors import ConfigurationError

"""
Configuration Management for Solana ICO Server

This module handles all configuration loading, validation, and management for the Solana ICO server.
It loads settings from environment variables with sensible defaults and provides validation
to ensure the server is configured correctly.

Configuration Sources (in order of precedence):
1. Environment variables
2. Default values defined in this module
3. Configuration validation and type conversion

Security Considerations:
- ICO wallet seed should be securely managed in production
- RPC endpoints should be trusted and monitored
- CORS origins should be restricted in production
- Rate limiting values should be tuned for production load

Environment Variables:
    RPC_ENDPOINT: Solana RPC endpoint URL
    TOKEN_MINT_ADDRESS: Default token mint address
    TOKEN_DECIMALS: Default token decimals (6-18)
    ICO_WALLET_SEED: Comma-separated seed bytes for ICO wallet
    ICO_START_TIMESTAMP: ICO start time (Unix timestamp)
    ICO_END_TIMESTAMP: ICO end time (Unix timestamp)
    SELL_FEE_PERCENTAGE: Fee for selling tokens (0.0-1.0)
    CURVE_TYPE: Bonding curve type
    RATE_LIMIT_PER_MINUTE: Rate limit per IP address
    CORS_ALLOWED_ORIGINS: Comma-separated allowed CORS origins
    ACTIONS_PORT: Port for Action API server
"""

# Set up logger
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


def _get_env_str(key: str, default: str, required: bool = False) -> str:
    """Get environment variable as string with validation."""
    value = os.getenv(key, default)
    if required and not value:
        raise ConfigurationError(f"Required environment variable {key} is not set")
    return value


def _get_env_int(key: str, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """Get environment variable as integer with validation."""
    try:
        value = int(os.getenv(key, str(default)))
        if min_val is not None and value < min_val:
            raise ConfigurationError(f"Environment variable {key} must be >= {min_val}")
        if max_val is not None and value > max_val:
            raise ConfigurationError(f"Environment variable {key} must be <= {max_val}")
        return value
    except ValueError:
        raise ConfigurationError(f"Environment variable {key} must be a valid integer")


def _get_env_float(key: str, default: float, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    """Get environment variable as float with validation."""
    try:
        value = float(os.getenv(key, str(default)))
        if min_val is not None and value < min_val:
            raise ConfigurationError(f"Environment variable {key} must be >= {min_val}")
        if max_val is not None and value > max_val:
            raise ConfigurationError(f"Environment variable {key} must be <= {max_val}")
        return value
    except ValueError:
        raise ConfigurationError(f"Environment variable {key} must be a valid float")


def _get_env_pubkey(key: str, default: str) -> Pubkey:
    """Get environment variable as Pubkey with validation."""
    try:
        value = os.getenv(key, default)
        return Pubkey.from_string(value)
    except Exception as e:
        raise ConfigurationError(f"Environment variable {key} must be a valid public key: {e}")


def _load_ico_wallet() -> Keypair:
    """Load ICO wallet from environment with validation."""
    seed_str = os.getenv("ICO_WALLET_SEED", ",".join(["1"] * 32))

    try:
        # Parse comma-separated integers
        seed_parts = [x.strip() for x in seed_str.split(",")]
        if len(seed_parts) != 32:
            raise ValueError(f"ICO_WALLET_SEED must contain exactly 32 comma-separated integers, got {len(seed_parts)}")

        seed_bytes = bytes([int(x) for x in seed_parts])
        if len(seed_bytes) != 32:
            raise ValueError("ICO_WALLET_SEED must result in exactly 32 bytes")

        wallet = Keypair.from_seed(seed_bytes)
        logger.info(f"Successfully loaded ICO wallet: {wallet.pubkey()}")
        return wallet

    except (ValueError, TypeError) as e:
        error_msg = f"Error loading ICO_WALLET_SEED: {e}. Using a default insecure seed for development."
        logger.warning(error_msg)
        # Fallback to a default (insecure) seed if parsing fails
        return Keypair.from_seed(bytes([1] * 32))


# --- Solana Configuration ---
try:
    RPC_ENDPOINT = _get_env_str("RPC_ENDPOINT", "http://localhost:8899", required=True)
    LAMPORTS_PER_SOL = 10**9

    # --- Token Configuration ---
    DEFAULT_TOKEN_MINT_ADDRESS = _get_env_pubkey("TOKEN_MINT_ADDRESS", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
    DEFAULT_TOKEN_DECIMALS = _get_env_int("TOKEN_DECIMALS", 9, min_val=0, max_val=18)

    # --- ICO Wallet Configuration ---
    ICO_WALLET = _load_ico_wallet()

    # --- Default ICO Configuration ---
    DEFAULT_ICO_START_TIMESTAMP = _get_env_int("ICO_START_TIMESTAMP", 0, min_val=0)
    DEFAULT_ICO_END_TIMESTAMP = _get_env_int("ICO_END_TIMESTAMP", 0, min_val=0)
    DEFAULT_SELL_FEE_PERCENTAGE = _get_env_float("SELL_FEE_PERCENTAGE", 0.0, min_val=0.0, max_val=1.0)

    # --- Default Curve Configuration ---
    DEFAULT_CURVE_TYPE = _get_env_str("CURVE_TYPE", "fixed")
    DEFAULT_FIXED_PRICE = _get_env_float("FIXED_PRICE", 0.000001, min_val=0.0)
    DEFAULT_INITIAL_PRICE = _get_env_float("INITIAL_PRICE", 0.0000001, min_val=0.0)
    DEFAULT_SLOPE = _get_env_float("SLOPE", 0.000000001)
    DEFAULT_GROWTH_RATE = _get_env_float("GROWTH_RATE", 0.0000000001)
    DEFAULT_CUSTOM_FORMULA = _get_env_str("CUSTOM_FORMULA", "initial_price + slope * total_tokens_minted")

    # --- Rate Limiting ---
    RATE_LIMIT_PER_MINUTE = _get_env_int("RATE_LIMIT_PER_MINUTE", 10, min_val=1, max_val=1000)

    # --- Directories ---
    ICO_CONFIG_DIR = _get_env_str("ICO_CONFIG_DIR", "ico_configs")

    # --- Action API Configuration ---
    ACTIONS_PORT = _get_env_int("ACTIONS_PORT", 5000, min_val=1024, max_val=65535)
    ACTION_ICON_URL = _get_env_str("ACTION_ICON_URL", "https://via.placeholder.com/150/0000FF/FFFFFF?text=ICO")

    logger.info("Configuration loaded successfully")

except ConfigurationError as e:
    logger.error(f"Configuration error: {e}")
    raise
except Exception as e:
    logger.error(f"Unexpected error loading configuration: {e}")
    raise ConfigurationError(f"Failed to load configuration: {e}")

# --- Default ICO Configuration (used if not specified in JSON config) ---
DEFAULT_ICO_START_TIMESTAMP = int(os.getenv("ICO_START_TIMESTAMP", "0"))
DEFAULT_ICO_END_TIMESTAMP = int(os.getenv("ICO_END_TIMESTAMP", "0"))
DEFAULT_SELL_FEE_PERCENTAGE = float(os.getenv("SELL_FEE_PERCENTAGE", "0.0"))

# --- Default Curve Configuration (used if not specified in JSON config) ---
DEFAULT_CURVE_TYPE = os.getenv("CURVE_TYPE", "fixed")
DEFAULT_FIXED_PRICE = float(os.getenv("FIXED_PRICE", "0.000001"))
DEFAULT_INITIAL_PRICE = float(os.getenv("INITIAL_PRICE", "0.0000001"))
DEFAULT_SLOPE = float(os.getenv("SLOPE", "0.000000001"))
DEFAULT_GROWTH_RATE = float(os.getenv("GROWTH_RATE", "0.0000000001"))
DEFAULT_CUSTOM_FORMULA = os.getenv("CUSTOM_FORMULA", "initial_price + slope * total_tokens_minted")

# --- Rate Limiting ---
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

# --- Directories ---
ICO_CONFIG_DIR = "ico_configs"