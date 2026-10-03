"""Verified target downloads pinned to the currently published directory."""

import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx
from tuf.api.metadata import Metadata

from . import config
from .errors import DownloadError
from .http_fetch import fetch_to_file


def _validate_relpath(target_path: str) -> str:
    p = PurePosixPath(target_path)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise DownloadError(f"invalid target path {target_path!r}")
    return str(p)


def load_pinned_targets(source) -> Metadata:
    """Snapshot the published targets metadata for this download.

    The published set is replaced atomically by refresh, so the bytes read
    here are one consistent, fully verified directory. A concurrent refresh
    cannot change the basis of this download.
    """
    store = source.store
    state = store.load_state()
    if not state["published"]:
        raise DownloadError("no published directory; run a successful refresh first")
    data = store.load_meta("targets")
    if data is None:
        raise DownloadError("published targets metadata missing")
    try:
        tg_md = Metadata.from_bytes(data)
    except Exception as exc:
        raise DownloadError(f"stored targets metadata unreadable: {exc}")
    if tg_md.signed.is_expired(datetime.now(timezone.utc)):
        raise DownloadError("published directory is expired; refresh required")
    return tg_md


def download_target(source, target_path: str) -> tuple[str, str]:
    """Fetch a target to a temp file and verify it. Returns (tmp_path, relpath).

    Nothing is delivered to the caller before length and sha256 match the
    pinned targets metadata.
    """
    relpath = _validate_relpath(target_path)
    tg_md = load_pinned_targets(source)
    target = tg_md.signed.targets.get(relpath)
    if target is None:
        raise DownloadError(f"unknown target {relpath!r}")
    expected_len = target.length
    expected_sha256 = (target.hashes or {}).get("sha256")
    if expected_len is None or expected_sha256 is None:
        raise DownloadError("target metadata lacks length or sha256")
    if expected_len > config.MAX_DOWNLOAD_BYTES:
        raise DownloadError("target exceeds maximum allowed download size")

    url = f"{source.upstream_url}/targets/{quote(relpath)}"
    fd, tmp_path = tempfile.mkstemp(prefix="tuf-dl-", dir=source.tmp_dir)
    try:
        with open(fd, "wb") as fh:
            got = fetch_to_file(url, min(expected_len, config.MAX_DOWNLOAD_BYTES), fh)
        if got != expected_len:
            raise DownloadError(
                f"truncated or oversized content: got {got} bytes, expected {expected_len}"
            )
        digest = hashlib.sha256()
        with open(tmp_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise DownloadError("sha256 mismatch")
    except DownloadError:
        _cleanup(tmp_path)
        raise
    except (httpx.HTTPError, ValueError, OSError) as exc:
        _cleanup(tmp_path)
        raise DownloadError(f"fetch failed: {exc}")
    return tmp_path, relpath


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
