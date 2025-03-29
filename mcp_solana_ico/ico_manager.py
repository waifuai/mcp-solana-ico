import json
import os
from pathlib import Path
from typing import Dict, Optional

from pydantic import ValidationError

from mcp_solana_ico.schemas import IcoConfigModel, CurveType
from mcp_solana_ico.config import ICO_CONFIG_DIR, DEFAULT_TOKEN_MINT_ADDRESS
from mcp.server.fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# In-memory storage for loaded ICO configurations and state
ico_data: Dict[str, IcoConfigModel] = {}
total_tokens_minted: Dict[str, int] = {} # Tracks minted tokens per ICO for bonding curves

def load_icos_from_config_files(config_dir: str = ICO_CONFIG_DIR) -> Dict[str, IcoConfigModel]:
    """
    Loads ICO configurations from JSON files in the specified directory.

    Args:
        config_dir: The directory containing ICO configuration files.

    Returns:
        A dictionary mapping ico_id to the validated IcoConfigModel instance.
    """
    loaded_icos: Dict[str, IcoConfigModel] = {}
    config_path = Path(config_dir)

    if not config_path.is_dir():
        logger.warning(f"ICO configuration directory not found: {config_dir}. No ICOs loaded.")
        return loaded_icos

    logger.info(f"Loading ICO configurations from: {config_path.resolve()}")
    for file_path in config_path.glob("*.json"):
        try:
            with open(file_path, "r") as f:
                config_data = json.load(f)
                ico_config = IcoConfigModel.model_validate(config_data)

                # Basic validation: Ensure ico_id in file matches the one in the config
                if ico_config.ico.ico_id != file_path.stem:
                     logger.warning(f"ICO ID mismatch in {file_path}: expected '{file_path.stem}', found '{ico_config.ico.ico_id}'. Skipping.")
                     continue

                if ico_config.ico.ico_id in loaded_icos:
                    logger.warning(f"Duplicate ICO ID '{ico_config.ico.ico_id}' found in {file_path}. Skipping.")
                    continue

                loaded_icos[ico_config.ico.ico_id] = ico_config
                # Initialize minted count if not already present (e.g., from previous state)
                if ico_config.ico.ico_id not in total_tokens_minted:
                    total_tokens_minted[ico_config.ico.ico_id] = 0
                logger.info(f"Successfully loaded ICO config: {ico_config.ico.ico_id}")

        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from file: {file_path}")
        except ValidationError as e:
            logger.error(f"Invalid ICO configuration in file {file_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading ICO config from {file_path}: {e}")

    logger.info(f"Finished loading ICOs. Total loaded: {len(loaded_icos)}")
    return loaded_icos

def get_ico(ico_id: str) -> Optional[IcoConfigModel]:
    """Retrieves an ICO configuration by its ID."""
    return ico_data.get(ico_id)

def get_total_tokens_minted(ico_id: str) -> int:
    """Retrieves the total tokens minted for a specific ICO."""
    return total_tokens_minted.get(ico_id, 0)

def increment_tokens_minted(ico_id: str, amount: int):
    """Increments the total tokens minted for a specific ICO."""
    if ico_id in total_tokens_minted:
        total_tokens_minted[ico_id] += amount
    else:
        # Should ideally not happen if ICO exists, but handle defensively
        total_tokens_minted[ico_id] = amount
    logger.debug(f"Updated total_tokens_minted for {ico_id}: {total_tokens_minted[ico_id]}")


def add_or_update_ico(ico_config: IcoConfigModel) -> bool:
    """Adds a new ICO or updates an existing one in memory and saves its config file."""
    ico_id = ico_config.ico.ico_id
    ico_data[ico_id] = ico_config
    if ico_id not in total_tokens_minted:
        total_tokens_minted[ico_id] = 0 # Initialize if new

    # Save the configuration to a file
    config_path = Path(ICO_CONFIG_DIR)
    config_path.mkdir(parents=True, exist_ok=True) # Ensure directory exists
    file_path = config_path / f"{ico_id}.json"
    try:
        with open(file_path, "w") as f:
            # Use model_dump to get dict, then dump as JSON
            json.dump(ico_config.model_dump(mode='json'), f, indent=4)
        logger.info(f"Successfully saved ICO configuration to {file_path}")
        return True
    except Exception as e:
        logger.error(f"Error saving ICO configuration to {file_path}: {e}")
        # Consider rolling back the in-memory addition if saving fails
        # del ico_data[ico_id]
        # if ico_id in total_tokens_minted and total_tokens_minted[ico_id] == 0: # Basic check if it was just added
        #     del total_tokens_minted[ico_id]
        return False


# --- Initial Load ---
# Load ICOs when the module is imported
ico_data = load_icos_from_config_files()