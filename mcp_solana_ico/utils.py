from solders.pubkey import Pubkey
from spl.token.instructions import get_associated_token_address
from mcp_solana_ico.server import TOKEN_MINT_ADDRESS

def get_token_account(payer: Pubkey) -> Pubkey:
    """Gets the associated token account for the payer."""
    return get_associated_token_address(payer, TOKEN_MINT_ADDRESS)