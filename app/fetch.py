"""Bounded, timed HTTP fetching of upstream metadata and targets."""
import httpx

from . import config
from .errors import FetchError


async def fetch_bytes(url: str, max_bytes: int, stage: str) -> bytes:
    """GET url with a hard size cap and timeout.

    Raises FetchError(not_found=True) on 404 so the root-rotation loop can
    detect the end of the version chain.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(config.HTTP_TIMEOUT), follow_redirects=False
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise FetchError(stage, f"{url} not found (404)", not_found=True)
                if resp.status_code != 200:
                    raise FetchError(stage, f"{url} returned HTTP {resp.status_code}")
                chunks = []
                total = 0
                async for chunk in resp.aiter_bytes(65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(
                            stage, f"{url} exceeds size limit of {max_bytes} bytes"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
    except httpx.HTTPError as exc:
        raise FetchError(stage, f"fetching {url} failed: {exc}")
