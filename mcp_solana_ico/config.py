import os
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from dotenv import load_dotenv

load_dotenv()

# --- Solana Configuration ---
RPC_ENDPOINT = os.getenv("RPC_ENDPOINT", "http://localhost:8899")
LAMPORTS_PER_SOL = 10**9

# --- Token Configuration ---
# Default Token Mint Address (can be overridden by specific ICO configs)
DEFAULT_TOKEN_MINT_ADDRESS = Pubkey.from_string(
    os.getenv("TOKEN_MINT_ADDRESS", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
)
# Default Token Decimals (can be overridden by specific ICO configs)
DEFAULT_TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", "9")) # Assuming a default if not set

# --- ICO Wallet Configuration ---
# Load from a secure configuration in production!
ICO_WALLET_SEED_STR = os.getenv("ICO_WALLET_SEED", ",".join(["1"] * 32))
try:
    ICO_WALLET_SEED_BYTES = bytes([int(x) for x in ICO_WALLET_SEED_STR.split(",")])
    if len(ICO_WALLET_SEED_BYTES) != 32:
        raise ValueError("ICO_WALLET_SEED must contain 32 comma-separated integers.")
    ICO_WALLET = Keypair.from_seed(ICO_WALLET_SEED_BYTES)
except (ValueError, TypeError) as e:
    print(f"Error loading ICO_WALLET_SEED: {e}. Using a default insecure seed.")
    # Fallback to a default (insecure) seed if parsing fails
    ICO_WALLET = Keypair.from_seed(bytes([1] * 32))

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