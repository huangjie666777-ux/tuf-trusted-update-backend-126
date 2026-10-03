"""Bounded, timed HTTP fetching used by refresh and download paths."""

import httpx

from . import config


def _client() -> httpx.Client:
    timeout = httpx.Timeout(
        connect=config.HTTP_CONNECT_TIMEOUT,
        read=config.HTTP_READ_TIMEOUT,
        write=config.HTTP_READ_TIMEOUT,
        pool=config.HTTP_CONNECT_TIMEOUT,
    )
    return httpx.Client(timeout=timeout, follow_redirects=False)


def fetch_bytes(url: str, max_bytes: int) -> bytes:
    """GET url, enforcing a hard size cap. Raises httpx.HTTPError on failure."""
    with _client() as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks = []
            total = 0
            for chunk in resp.iter_bytes(65536):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"response exceeds {max_bytes} byte limit")
                chunks.append(chunk)
    return b"".join(chunks)


def fetch_to_file(url: str, max_bytes: int, fileobj) -> int:
    """Stream GET url into fileobj with a hard size cap. Returns byte count."""
    total = 0
    with _client() as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(65536):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"response exceeds {max_bytes} byte limit")
                fileobj.write(chunk)
    return total
