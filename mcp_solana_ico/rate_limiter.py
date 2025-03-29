import time
from typing import Dict, Tuple

from mcp_solana_ico.config import RATE_LIMIT_PER_MINUTE
from mcp.server.fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# In-memory cache for rate limiting: {ip: (count, first_request_timestamp_in_window)}
rate_limit_cache: Dict[str, Tuple[int, int]] = {}

def check_rate_limit(ip: str) -> bool:
    """
    Checks if the given IP address has exceeded the rate limit.

    Args:
        ip: The client's IP address.

    Returns:
        True if the request is allowed, False if rate limit exceeded.
    """
    now = int(time.time())
    limit = RATE_LIMIT_PER_MINUTE
    window = 60 # seconds

    if ip in rate_limit_cache:
        count, timestamp = rate_limit_cache[ip]
        # Check if the window has expired
        if now - timestamp >= window:
            # Reset window
            rate_limit_cache[ip] = (1, now)
            logger.debug(f"Rate limit window reset for IP: {ip}")
            return True
        else:
            # Check if count exceeds limit within the window
            if count >= limit:
                logger.warning(f"Rate limit exceeded for IP: {ip}. Count: {count}, Limit: {limit}")
                return False
            else:
                # Increment count within the window
                rate_limit_cache[ip] = (count + 1, timestamp)
                logger.debug(f"Rate limit check passed for IP: {ip}. Count: {count + 1}")
                return True
    else:
        # First request from this IP in a while
        rate_limit_cache[ip] = (1, now)
        logger.debug(f"Rate limit initiated for IP: {ip}")
        return True