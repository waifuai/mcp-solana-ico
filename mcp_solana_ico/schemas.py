"""
Pydantic Data Models and Validation Schemas

This module defines comprehensive data models and validation schemas for the Solana ICO system
using Pydantic. It provides type safety, input validation, and structured data handling for
all ICO-related configurations and operations.

Key Components:
- CurveType Enum: Defines supported bonding curve types
- TokenConfig: Token metadata and configuration schema
- IcoConfig: ICO-specific configuration schema
- IcoConfigModel: Complete ICO configuration combining token and ICO settings

Data Validation Features:
- Automatic type conversion and validation
- Optional field handling with sensible defaults
- Structured error reporting for invalid data
- Enum-based curve type validation
- Numeric range validation for pricing parameters

Schema Structure:
- TokenConfig: Contains token name, symbol, supply, and decimal configuration
- IcoConfig: Contains ICO timing, pricing curve, and fee configuration
- IcoConfigModel: Combines token and ICO configs with optional resource links

Usage:
- Used throughout the application for configuration loading
- Provides validation for JSON configuration files
- Enables type hints and IDE support
- Supports serialization and deserialization
- Integrated with ICO manager for configuration validation

Security:
- Input sanitization through Pydantic validation
- Type safety to prevent injection attacks
- Structured error handling for invalid configurations
"""
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