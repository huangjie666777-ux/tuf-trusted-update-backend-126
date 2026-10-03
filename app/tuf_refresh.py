"""TUF 1.0 client-side refresh: root rotation, timestamp, snapshot, targets.

Constraints enforced (per deployment policy):
  * TUF spec 1.0.x only
  * Ed25519 keys/signatures only
  * exactly the four top-level roles, no delegations
  * consistent snapshots disabled
"""

import hashlib
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from tuf.api.exceptions import UnsignedMetadataError
from tuf.api.metadata import Metadata, Root

from . import config
from .errors import RefreshError
from .http_fetch import fetch_bytes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def validate_root_config(root: Root) -> None:
    """Reject unsupported repository configurations."""
    if not root.spec_version.startswith(config.SPEC_MAJOR + "."):
        raise RefreshError("root", f"unsupported spec_version {root.spec_version!r}; TUF 1.0.x required")
    if root.consistent_snapshot:
        raise RefreshError("root", "consistent snapshots are not supported")
    roles = set(root.roles.keys())
    if roles != set(config.TOP_LEVEL_ROLES):
        raise RefreshError("root", f"roles must be exactly {sorted(config.TOP_LEVEL_ROLES)}, got {sorted(roles)}")
    for name, role in root.roles.items():
        if role.threshold < 1:
            raise RefreshError("root", f"role {name} has invalid threshold {role.threshold}")
        if len(role.keyids) < role.threshold:
            raise RefreshError("root", f"role {name} threshold exceeds its key count")
    for keyid, key in root.keys.items():
        if key.keytype != config.SUPPORTED_KEYTYPE or key.scheme != config.SUPPORTED_SCHEME:
            raise RefreshError("root", f"key {keyid[:12]} is {key.keytype}/{key.scheme}; ed25519 only")


def _verify_delegate(delegator_md: Metadata, role: str, md: Metadata, stage: str) -> None:
    # tuf counts each distinct keyid at most once towards the threshold.
    try:
        delegator_md.verify_delegate(role, md)
    except UnsignedMetadataError as exc:
        raise RefreshError(stage, f"{role} signature threshold not met: {exc}")


def _check_expiry(md: Metadata, stage: str) -> None:
    if md.signed.is_expired(_now()):
        raise RefreshError(stage, f"{md.signed.type} expired at {md.signed.expires.isoformat()}")


def _check_hashes(data: bytes, meta, stage: str, name: str) -> None:
    if meta.length is not None and meta.length != len(data):
        raise RefreshError(stage, f"{name} length {len(data)} != expected {meta.length}")
    hashes = meta.hashes or {}
    if "sha256" not in hashes:
        raise RefreshError(stage, f"{name} meta is missing a sha256 hash")
    digest = hashlib.sha256(data).hexdigest()
    if digest != hashes["sha256"]:
        raise RefreshError(stage, f"{name} sha256 mismatch")


def _fetch(url: str, max_bytes: int, stage: str, allow_404: bool = False):
    try:
        return fetch_bytes(url, max_bytes)
    except httpx.HTTPStatusError as exc:
        if allow_404 and exc.response.status_code == 404:
            return None
        raise RefreshError(stage, f"GET {url} failed: HTTP {exc.response.status_code}")
    except (httpx.HTTPError, ValueError) as exc:
        raise RefreshError(stage, f"GET {url} failed: {exc}")


def _parse(data: bytes, stage: str, expected_type: str) -> Metadata:
    try:
        md = Metadata.from_bytes(data)
    except Exception as exc:
        raise RefreshError(stage, f"cannot parse {expected_type} metadata: {exc}")
    if md.signed.type != expected_type:
        raise RefreshError(stage, f"expected {expected_type}, got {md.signed.type}")
    return md


def refresh_root_chain(source) -> Metadata:
    """Fetch and verify newer roots in version order; persist each accepted root."""
    store = source.store
    root_md = _parse(store.load_root(), "root", "root")
    validate_root_config(root_md.signed)
    accepted = 0
    for _ in range(config.MAX_ROOT_CHAIN):
        next_version = root_md.signed.version + 1
        url = f"{source.upstream_url}/{next_version}.root.json"
        data = _fetch(url, config.MAX_ROOT_BYTES, "root", allow_404=True)
        if data is None:
            break
        new_md = _parse(data, "root", "root")
        new_root: Root = new_md.signed
        if new_root.version != next_version:
            raise RefreshError("root", f"root version {new_root.version} != expected {next_version}")
        validate_root_config(new_root)
        # old root must authorize the new root, and the new root must sign itself;
        # each key counts at most once per threshold check.
        _verify_delegate(root_md, "root", new_md, "root")
        _verify_delegate(new_md, "root", new_md, "root")
        _check_expiry(new_md, "root")
        # Persist immediately: a later failure must not roll back accepted roots.
        store.persist_root(next_version, data)
        root_md = new_md
        accepted += 1
    if accepted == config.MAX_ROOT_CHAIN:
        raise RefreshError("root", "root chain too long")
    return root_md


def refresh(source) -> dict:
    """Run a full TUF refresh. Returns a report; raises RefreshError on failure."""
    store = source.store
    state = store.load_state()
    base = source.upstream_url

    root_md = refresh_root_chain(source)

    # --- timestamp ---
    ts_data = _fetch(f"{base}/timestamp.json", config.MAX_TIMESTAMP_BYTES, "timestamp")
    ts_md = _parse(ts_data, "timestamp", "timestamp")
    _verify_delegate(root_md, "timestamp", ts_md, "timestamp")
    _check_expiry(ts_md, "timestamp")
    if ts_md.signed.version <= state["timestamp_version"]:
        raise RefreshError(
            "timestamp",
            f"timestamp version {ts_md.signed.version} not newer than stored {state['timestamp_version']}",
        )
    snap_meta = ts_md.signed.snapshot_meta

    # --- snapshot ---
    snap_data = _fetch(f"{base}/snapshot.json", config.MAX_SNAPSHOT_BYTES, "snapshot")
    _check_hashes(snap_data, snap_meta, "snapshot", "snapshot.json")
    snap_md = _parse(snap_data, "snapshot", "snapshot")
    _verify_delegate(root_md, "snapshot", snap_md, "snapshot")
    _check_expiry(snap_md, "snapshot")
    if snap_md.signed.version != snap_meta.version:
        raise RefreshError("snapshot", f"snapshot version {snap_md.signed.version} != timestamp meta version {snap_meta.version}")
    if snap_md.signed.version < state["snapshot_version"]:
        raise RefreshError("snapshot", "snapshot version rollback detected")
    targets_meta = snap_md.signed.meta.get("targets.json")
    if targets_meta is None:
        raise RefreshError("snapshot", "snapshot does not reference targets.json")

    # --- targets ---
    tg_data = _fetch(f"{base}/targets.json", config.MAX_TARGETS_BYTES, "targets")
    _check_hashes(tg_data, targets_meta, "targets", "targets.json")
    tg_md = _parse(tg_data, "targets", "targets")
    _verify_delegate(root_md, "targets", tg_md, "targets")
    _check_expiry(tg_md, "targets")
    if tg_md.signed.delegations is not None:
        raise RefreshError("targets", "delegations are not supported")
    if tg_md.signed.version != targets_meta.version:
        raise RefreshError("targets", f"targets version {tg_md.signed.version} != snapshot meta version {targets_meta.version}")
    if tg_md.signed.version < state["targets_version"]:
        raise RefreshError("targets", "targets version rollback detected")

    # --- publish only after every check passed ---
    from .publisher import publish

    publish(source, ts_data, snap_data, tg_data, tg_md)
    return {
        "root_version": root_md.signed.version,
        "timestamp_version": ts_md.signed.version,
        "snapshot_version": snap_md.signed.version,
        "targets_version": tg_md.signed.version,
        "targets_expires": tg_md.signed.expires.isoformat(),
        "target_count": len(tg_md.signed.targets),
    }
