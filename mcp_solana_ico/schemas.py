from pydantic import BaseModel
from typing import Optional

class TokenConfig(BaseModel):
    name: str
    symbol: str
    total_supply: int
    decimals: int

class IcoConfig(BaseModel):
    ico_id: str
    start_time: int
    end_time: int
    curve_type: str
    fixed_price: Optional[float] = None
    initial_price: Optional[float] = None
    slope: Optional[float] = None
    growth_rate: Optional[float] = None
    custom_formula: Optional[str] = None
    sell_fee_percentage: float = 0.0

class IcoConfigModel(BaseModel):
    token: TokenConfig
    ico: IcoConfig
    resources: Optional[list] = []