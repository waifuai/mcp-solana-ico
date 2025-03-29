from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class CurveType(str, Enum):
    fixed = "fixed"
    linear = "linear"
    exponential = "exponential"
    sigmoid = "sigmoid"
    custom = "custom"

class TokenConfig(BaseModel):
    name: str
    symbol: str
    total_supply: int
    decimals: int
    # Add token_address here if it's part of the config file structure
    # token_address: str = Field(description="The on-chain address of the token")

class IcoConfig(BaseModel):
    ico_id: str
    start_time: int
    end_time: int
    curve_type: CurveType # Use the enum here
    fixed_price: Optional[float] = None
    initial_price: Optional[float] = None
    slope: Optional[float] = None
    growth_rate: Optional[float] = None
    custom_formula: Optional[str] = None
    sell_fee_percentage: float = 0.0
    # Add token_address here if it should be configured per ICO
    # token_address: Optional[str] = None

class IcoConfigModel(BaseModel):
    token: TokenConfig
    ico: IcoConfig
    resources: Optional[list] = []