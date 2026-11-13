"""
Robust DNS and Network Session Utilities.
Uses public DNS nameservers (Cloudflare 1.1.1.1, Google 8.8.8.8) to bypass
local ISP/router DNS restrictions on cryptocurrency exchange endpoints.
"""

import asyncio
import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

PUBLIC_NAMESERVERS = ["8.8.8.8", "1.1.1.1", "8.8.4.4", "1.0.0.1"]


def create_robust_session() -> aiohttp.ClientSession:
    """Create an aiohttp.ClientSession with AsyncResolver using public DNS."""
    try:
        loop = asyncio.get_running_loop()
        resolver = aiohttp.AsyncResolver(loop=loop, nameservers=PUBLIC_NAMESERVERS)
        connector = aiohttp.TCPConnector(resolver=resolver)
        return aiohttp.ClientSession(connector=connector)
    except Exception as e:
        logger.debug("create_robust_session fallback to default ClientSession: %s", e)
        return aiohttp.ClientSession()
