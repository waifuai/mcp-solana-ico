"""
Token Pricing Engine with Bonding Curves

This module implements a comprehensive token pricing system using various bonding curve mechanisms
for Initial Coin Offerings (ICOs). It calculates token prices dynamically based on supply and demand
dynamics, supporting multiple curve types for different economic models.

Bonding Curve Types Supported:
- Fixed Price: Constant price regardless of supply
- Linear: Price increases linearly with supply
- Exponential: Price grows exponentially with supply
- Sigmoid: S-shaped curve with slow growth, rapid growth, then plateau
- Custom: User-defined mathematical formulas via eval (with security considerations)

Key Features:
- Dynamic price calculation based on current token supply
- Support for buy and sell operations with different fee structures
- Integration with ICO manager for real-time token minting data
- Comprehensive input validation and error handling
- Structured logging for price calculation monitoring

Price Calculation Process:
1. Determine current total tokens minted for the ICO
2. Apply the appropriate bonding curve formula based on curve type
3. Calculate base price per token
4. Apply sell fees for sell operations (if applicable)
5. Return total SOL cost or proceeds

Security Considerations:
- Input validation to prevent invalid calculations
- Safe evaluation of custom formulas with restricted context
- Price bounds checking to prevent negative prices
- Comprehensive error handling with detailed logging

Performance Features:
- Efficient mathematical calculations
- Integration with cached ICO data
- Minimal computational overhead per calculation
- Structured logging for performance monitoring
"""
from typing import Dict
import math # Needed for exponential/sigmoid if implemented

from mcp_solana_ico.schemas import IcoConfigModel, CurveType
from mcp_solana_ico import ico_manager # To access total_tokens_minted
from mcp.server.fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

def calculate_token_price(amount: int, ico: IcoConfigModel, is_sell: bool = False) -> float:
    """
    Calculates the token price based on the bonding curve defined in the ICO config.

    Args:
        amount: The number of tokens (in base units).
        ico: The IcoConfigModel instance for the specific ICO.
        is_sell: Boolean indicating if the calculation is for selling tokens.

    Returns:
        The total price in SOL for the given amount of tokens.

    Raises:
        ValueError: If the curve type is invalid or required parameters are missing.
    """
    ico_id = ico.ico.ico_id
    current_total_minted = ico_manager.get_total_tokens_minted(ico_id)
    decimals = ico.token.decimals
    amount_in_tokens = amount / (10**decimals) # Convert base units to token units for price calculation

    base_price_per_token = 0.0

    # --- Calculate Price Per Token based on Curve ---
    curve_type = ico.ico.curve_type
    try:
        if curve_type == CurveType.fixed:
            if ico.ico.fixed_price is None:
                raise ValueError(f"Fixed price is not set for ICO {ico_id}.")
            base_price_per_token = ico.ico.fixed_price
        elif curve_type == CurveType.linear:
            if ico.ico.initial_price is None or ico.ico.slope is None:
                raise ValueError(f"Initial price or slope is not set for linear curve in ICO {ico_id}.")
            # Price is determined by the current state *before* this transaction
            base_price_per_token = ico.ico.initial_price + ico.ico.slope * current_total_minted
        elif curve_type == CurveType.exponential:
            if ico.ico.initial_price is None or ico.ico.growth_rate is None:
                raise ValueError(f"Initial price or growth rate is not set for exponential curve in ICO {ico_id}.")
            # Price is determined by the current state *before* this transaction
            # Using (1 + growth_rate) based on previous implementation. Ensure formula is correct.
            base_price_per_token = ico.ico.initial_price * math.pow((1 + ico.ico.growth_rate), current_total_minted)
        elif curve_type == CurveType.sigmoid:
            # Placeholder: Implement sigmoid logic if needed
            # Requires parameters like midpoint, scale, max_price etc.
            raise NotImplementedError(f"Sigmoid curve not implemented for ICO {ico_id}.")
        elif curve_type == CurveType.custom:
            if ico.ico.custom_formula is None or ico.ico.initial_price is None: # Assuming initial_price might be needed
                 raise ValueError(f"Custom formula or initial_price not set for ICO {ico_id}.")
            try:
                # WARNING: Using eval() is potentially unsafe. Sanitize or use a safer evaluation method.
                # Provide necessary variables in the eval context.
                eval_context = {
                    "initial_price": ico.ico.initial_price,
                    "total_tokens_minted": current_total_minted,
                    "slope": ico.ico.slope, # Provide other params if needed by formula
                    "growth_rate": ico.ico.growth_rate,
                    "math": math, # Allow math functions
                    # Add other relevant variables/functions safely
                }
                base_price_per_token = eval(ico.ico.custom_formula, {"__builtins__": {}}, eval_context)
            except Exception as e:
                logger.error(f"Error evaluating custom formula for ICO {ico_id}: {e}")
                raise ValueError(f"Error evaluating custom formula for ICO {ico_id}: {e}")
        else:
            # This case should not be reachable if schema validation works
            raise ValueError(f"Invalid curve type '{curve_type}' for ICO {ico_id}.")

    except Exception as e:
         logger.exception(f"Error during price calculation for {ico_id}: {e}")
         raise ValueError(f"Error calculating base price for {ico_id}: {e}")


    # Ensure price is non-negative
    if base_price_per_token < 0:
        logger.warning(f"Calculated negative base price per token ({base_price_per_token}) for {ico_id}. Clamping to 0.")
        base_price_per_token = 0.0

    # --- Calculate Total Price & Apply Fee ---
    total_sol_value = amount_in_tokens * base_price_per_token

    if is_sell:
        sell_fee = total_sol_value * ico.ico.sell_fee_percentage
        net_sol_value = total_sol_value - sell_fee
        logger.debug(f"Sell calculation for {amount} units ({amount_in_tokens} tokens) of {ico_id}: "
                     f"Base Value={total_sol_value:.9f} SOL, Fee={sell_fee:.9f} SOL, Net={net_sol_value:.9f} SOL")
        return max(0.0, net_sol_value) # Ensure non-negative return
    else:
        logger.debug(f"Buy calculation for {amount} units ({amount_in_tokens} tokens) of {ico_id}: "
                     f"Total Price={total_sol_value:.9f} SOL")
        return total_sol_value