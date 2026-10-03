"""Pinned, verified target downloads.

The download is pinned to the manifest that is published at request
start: the manifest is read once and used for the whole request, so a
concurrent refresh cannot change the basis of this download. The target
bytes are streamed to a temporary file, verified against the pinned
length and sha256, and only then handed to the caller. Unverified bytes
are never delivered.
"""
import hashlib
import os
import posixpath
import tempfile
from datetime import datetime, timezone

import httpx

from . import config
from .errors import DownloadError


def normalize_target_path(path: str) -> str:
    if not path or path.startswith("/") or "\\" in path:
        raise DownloadError(f"invalid target path {path!r}", 400)
    norm = posixpath.normpath(path)
    if norm == "." or norm.startswith("../") or norm == ".." or "/../" in norm:
        raise DownloadError(f"path traversal rejected: {path!r}", 400)
    return norm


def get_pinned_entry(source, path: str) -> dict:
    """Look up path in the currently published manifest (read once)."""
    manifest = source.store.load_manifest()
    if manifest is None:
        raise DownloadError("no published directory: run a successful refresh first", 409)
    expires = datetime.fromisoformat(manifest["expires"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise DownloadError(
            f"published directory expired at {manifest['expires']}; refresh required", 409
        )
    for entry in manifest["files"]:
        if entry["path"] == path:
            return entry
    raise DownloadError(f"unknown target {path!r}", 404)


async def download_verified(source, entry: dict) -> str:
    """Fetch the target to a temp file and verify length + sha256.

    Returns the temp file path only after full verification. Raises
    DownloadError on truncation, oversize or hash mismatch; the temp file
    is removed in that case and no unverified bytes escape.
    """
    expected_len = entry["length"]
    if expected_len > config.MAX_TARGET_BYTES:
        raise DownloadError(
            f"target length {expected_len} exceeds limit {config.MAX_TARGET_BYTES}", 502
        )
    url = f"{source.targets_url}/{entry['path']}"
    fd, tmp_path = tempfile.mkstemp(prefix="tuf-dl-")
    hasher = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(config.HTTP_TIMEOUT), follow_redirects=False
            ) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        raise DownloadError(
                            f"upstream returned HTTP {resp.status_code} for {url}", 502
                        )
                    async for chunk in resp.aiter_bytes(65536):
                        total += len(chunk)
                        if total > expected_len:
                            raise DownloadError(
                                f"target longer than expected {expected_len} bytes", 502
                            )
                        hasher.update(chunk)
                        out.write(chunk)
        if total != expected_len:
            raise DownloadError(
                f"target truncated: expected {expected_len} bytes, got {total}", 502
            )
        digest = hasher.hexdigest()
        if digest != entry["sha256"]:
            raise DownloadError(
                f"sha256 mismatch: expected {entry['sha256']}, got {digest}", 502
            )
    except DownloadError:
        os.unlink(tmp_path)
        raise
    except httpx.HTTPError as exc:
        os.unlink(tmp_path)
        raise DownloadError(f"fetching {url} failed: {exc}", 502)
    return tmp_path
