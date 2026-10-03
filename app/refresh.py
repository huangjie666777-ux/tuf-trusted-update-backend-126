"""TUF client refresh pipeline (TUF 1.0 detailed client workflow).

Stages: root rotation -> timestamp -> snapshot -> targets -> publish.
Each accepted root is persisted immediately and never rolled back; a
failure in any later stage leaves previously accepted roots in place and
keeps the previously published directory untouched.
"""
import datetime
import logging
from typing import Optional

from tuf.api.exceptions import (
    BadVersionNumberError,
    ExpiredMetadataError,
    RepositoryError,
    UnsignedMetadataError,
)
from tuf.api.metadata import Metadata, Root, Snapshot, Targets, Timestamp

from . import config, validation
from .errors import FetchError, UpdateError
from .fetch import fetch_bytes

log = logging.getLogger("tuf-update.refresh")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _verify_role(root: Root, role: str, md: Metadata, stage: str) -> None:
    """Verify md's signatures against root's keys/threshold for role.

    verify_delegate counts each key at most once, so one key cannot
    satisfy a threshold by signing multiple times.
    """
    try:
        root.verify_delegate(role, md.signed_bytes, md.signatures)
    except UnsignedMetadataError as exc:
        raise UpdateError(stage, f"{role} signature threshold not met: {exc}")


def _check_expiry(signed, stage: str, name: str) -> None:
    if signed.is_expired(_now()):
        raise UpdateError(stage, f"{name} expired at {signed.expires.isoformat()}")


def _check_hashes_and_length(data: bytes, meta, stage: str, name: str) -> None:
    if meta.length is not None and meta.length != len(data):
        raise UpdateError(
            stage, f"{name} length mismatch: expected {meta.length}, got {len(data)}"
        )
    try:
        meta.verify_length_and_hashes(data)
    except RepositoryError as exc:
        raise UpdateError(stage, f"{name} hash/length verification failed: {exc}")


async def _update_root(source) -> Root:
    """Rotate root: fetch N+1, N+2, ... until 404.

    Each intermediate root must be signed by both the previous root's
    threshold and its own (new) threshold. Accepted roots are persisted
    immediately; a later failure never rolls them back.
    """
    stage = "root"
    trusted = Metadata.from_bytes(source.store.load_metadata("root.json"))
    validation.validate_root(trusted)
    if trusted.signed.is_expired(_now()):
        raise UpdateError(stage, f"trusted root v{trusted.signed.version} is expired")

    next_version = trusted.signed.version + 1
    while True:
        url = f"{source.metadata_url}/{next_version}.root.json"
        try:
            data = await fetch_bytes(url, config.MAX_ROOT_BYTES, stage)
        except FetchError as exc:
            if exc.not_found:
                break
            raise
        try:
            new_md = Metadata.from_bytes(data)
        except Exception as exc:
            raise UpdateError(stage, f"unparseable root v{next_version}: {exc}")
        if not isinstance(new_md.signed, Root):
            raise UpdateError(stage, f"{next_version}.root.json is not a root role")
        validation.validate_root(new_md)
        if new_md.signed.version != next_version:
            raise UpdateError(
                stage,
                f"root version mismatch: expected {next_version}, "
                f"got {new_md.signed.version}",
            )
        # Old root's threshold must authorize the rotation ...
        _verify_role(trusted.signed, "root", new_md, stage)
        # ... and the new root must reach its own threshold (self-signed).
        _verify_role(new_md.signed, "root", new_md, stage)
        _check_expiry(new_md.signed, stage, f"root v{next_version}")
        source.store.save_root(next_version, data)
        log.info("source %s: accepted root v%d", source.name, next_version)
        trusted = new_md
        next_version += 1
    return trusted.signed


async def _update_timestamp(source, root: Root) -> Metadata:
    stage = "timestamp"
    data = await fetch_bytes(
        f"{source.metadata_url}/timestamp.json", config.MAX_TIMESTAMP_BYTES, stage
    )
    try:
        md = Metadata.from_bytes(data)
    except Exception as exc:
        raise UpdateError(stage, f"unparseable timestamp: {exc}")
    if not isinstance(md.signed, Timestamp):
        raise UpdateError(stage, "timestamp.json is not a timestamp role")
    _verify_role(root, "timestamp", md, stage)
    _check_expiry(md.signed, stage, "timestamp")

    trusted_raw = source.store.load_metadata("timestamp.json")
    if trusted_raw is not None:
        trusted = Metadata.from_bytes(trusted_raw)
        if md.signed.version < trusted.signed.version:
            raise UpdateError(
                stage,
                f"timestamp rollback: {md.signed.version} < {trusted.signed.version}",
            )
        if md.signed.snapshot_meta.version < trusted.signed.snapshot_meta.version:
            raise UpdateError(
                stage,
                "snapshot version rollback in timestamp: "
                f"{md.signed.snapshot_meta.version} < "
                f"{trusted.signed.snapshot_meta.version}",
            )
    source.store.save_metadata("timestamp.json", data)
    return md


async def _update_snapshot(source, root: Root, timestamp: Metadata) -> Metadata:
    stage = "snapshot"
    snap_meta = timestamp.signed.snapshot_meta
    data = await fetch_bytes(
        f"{source.metadata_url}/snapshot.json", config.MAX_SNAPSHOT_BYTES, stage
    )
    _check_hashes_and_length(data, snap_meta, stage, "snapshot.json")
    try:
        md = Metadata.from_bytes(data)
    except Exception as exc:
        raise UpdateError(stage, f"unparseable snapshot: {exc}")
    if not isinstance(md.signed, Snapshot):
        raise UpdateError(stage, "snapshot.json is not a snapshot role")
    _verify_role(root, "snapshot", md, stage)
    _check_expiry(md.signed, stage, "snapshot")
    if md.signed.version != snap_meta.version:
        raise UpdateError(
            stage,
            f"snapshot version {md.signed.version} != version {snap_meta.version} "
            "referenced by timestamp",
        )
    trusted_raw = source.store.load_metadata("snapshot.json")
    if trusted_raw is not None:
        trusted = Metadata.from_bytes(trusted_raw)
        new_v = md.signed.meta["targets.json"].version
        old_v = trusted.signed.meta["targets.json"].version
        if new_v < old_v:
            raise UpdateError(
                stage, f"targets version rollback in snapshot: {new_v} < {old_v}"
            )
    source.store.save_metadata("snapshot.json", data)
    return md


async def _update_targets(source, root: Root, snapshot: Metadata) -> bytes:
    stage = "targets"
    targets_meta = snapshot.signed.meta["targets.json"]
    data = await fetch_bytes(
        f"{source.metadata_url}/targets.json", config.MAX_TARGETS_BYTES, stage
    )
    _check_hashes_and_length(data, targets_meta, stage, "targets.json")
    try:
        md = Metadata.from_bytes(data)
    except Exception as exc:
        raise UpdateError(stage, f"unparseable targets: {exc}")
    if not isinstance(md.signed, Targets):
        raise UpdateError(stage, "targets.json is not a targets role")
    validation.validate_targets(md)
    _verify_role(root, "targets", md, stage)
    _check_expiry(md.signed, stage, "targets")
    if md.signed.version != targets_meta.version:
        raise UpdateError(
            stage,
            f"targets version {md.signed.version} != version {targets_meta.version} "
            "referenced by snapshot",
        )
    source.store.save_metadata("targets.json", data)
    return data


async def refresh_source(source) -> dict:
    """Run the full refresh pipeline for one source.

    Caller must hold the source's refresh lock. Returns the published
    manifest on success; raises UpdateError (stage-tagged) on failure.
    """
    from .publish import build_manifest

    root = await _update_root(source)
    timestamp = await _update_timestamp(source, root)
    snapshot = await _update_snapshot(source, root, timestamp)
    targets_bytes = await _update_targets(source, root, snapshot)

    targets_md = Metadata.from_bytes(targets_bytes)
    manifest = build_manifest(targets_md)
    source.store.publish(manifest, targets_bytes)
    source.store.save_state(
        {
            "last_refresh": _now().isoformat(),
            "last_error": None,
            "root_version": root.version,
            "targets_version": targets_md.signed.version,
        }
    )
    return manifest


def record_failure(source, exc: UpdateError) -> None:
    """Mark the refresh as failed without touching the published directory."""
    state = source.store.load_state()
    state["last_refresh"] = _now().isoformat()
    state["last_error"] = {"stage": exc.stage, "reason": exc.reason}
    source.store.save_state(state)
