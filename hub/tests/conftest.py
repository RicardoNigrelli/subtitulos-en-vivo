"""Fixtures de pytest para los tests del hub (helpers en hub/tests/util.py)."""
import aiohttp
import pytest_asyncio

from hub.tests.util import levantar


@pytest_asyncio.fixture
async def hub():
    h = await levantar()
    yield h
    await h.parar()


@pytest_asyncio.fixture
async def http():
    async with aiohttp.ClientSession() as s:
        yield s
