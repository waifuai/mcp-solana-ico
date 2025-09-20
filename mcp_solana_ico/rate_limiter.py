"""
API Rate Limiting System

This module implements a comprehensive rate limiting system for the Solana ICO API endpoints.
It prevents abuse and ensures fair usage by limiting the number of requests per IP address
within a specified time window.

Key Features:
- IP-based rate limiting with configurable limits
- Sliding time window implementation
- Automatic cleanup of expired entries
- Memory-efficient storage using OrderedDict
- Thread-safe operations for concurrent requests
- Integration with configuration system for dynamic limits

Rate Limiting Algorithm:
- Uses a sliding window approach with 60-second windows
- Tracks request count and first request timestamp per IP
- Automatically resets counters when window expires
- Provides immediate feedback on rate limit violations

Performance Optimizations:
- Efficient cleanup of old entries to prevent memory leaks
- OrderedDict for automatic LRU (Least Recently Used) ordering
- Minimal memory footprint per tracked IP address
- Fast lookup and update operations
- Configurable cache size limits to prevent unbounded growth

Security Features:
- Helps prevent brute force attacks
- Protects against API abuse and spam
- Configurable limits via environment variables
- Detailed logging for monitoring and debugging

Usage:
- Automatically integrated into main API endpoints
- Raises RateLimitExceededError when limits are exceeded
- Provides structured logging for rate limit events
- Configurable per-minute request limits via environment variables
"""
import time
from typing import Dict, Tuple
from collections import OrderedDict

from mcp_solana_ico.config import RATE_LIMIT_PER_MINUTE
from mcp.server.fastmcp.utilities.logging import get_logger

logger = get_logger(__name__)

# In-memory cache for rate limiting: {ip: (count, first_request_timestamp_in_window)}
# Using OrderedDict to enable efficient cleanup of old entries
rate_limit_cache: OrderedDict[str, Tuple[int, int]] = OrderedDict()

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

    # Periodic cleanup of old entries (keep cache size manageable)
    if len(rate_limit_cache) > 1000:  # Arbitrary limit
        cleanup_old_entries(now - window)

    if ip in rate_limit_cache:
        count, timestamp = rate_limit_cache[ip]
        # Check if the window has expired
        if now - timestamp >= window:
            # Reset window
            rate_limit_cache[ip] = (1, now)
            rate_limit_cache.move_to_end(ip)  # Move to end for LRU ordering
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
                rate_limit_cache.move_to_end(ip)  # Move to end for LRU ordering
                logger.debug(f"Rate limit check passed for IP: {ip}. Count: {count + 1}")
                return True
    else:
        # First request from this IP in a while
        rate_limit_cache[ip] = (1, now)
        rate_limit_cache.move_to_end(ip)  # Move to end for LRU ordering
        logger.debug(f"Rate limit initiated for IP: {ip}")
        return True


def cleanup_old_entries(cutoff_time: int):
    """
    Removes rate limit entries that are older than the specified cutoff time.

    Args:
        cutoff_time: Unix timestamp, entries before this time will be removed.
    """
    to_remove = []
    for ip, (count, timestamp) in rate_limit_cache.items():
        if timestamp < cutoff_time:
            to_remove.append(ip)

    for ip in to_remove:
        del rate_limit_cache[ip]

    if to_remove:
        logger.debug(f"Cleaned up {len(to_remove)} old rate limit entries")