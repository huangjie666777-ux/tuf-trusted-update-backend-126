"""Publish a fully verified metadata set as the source's current directory."""

from datetime import datetime, timezone

from tuf.api.metadata import Metadata


def publish(source, ts_data: bytes, snap_data: bytes, tg_data: bytes, tg_md: Metadata) -> None:
    store = source.store
    # Replace the published set as a whole; files are written atomically.
    store.publish_meta("timestamp", ts_data)
    store.publish_meta("snapshot", snap_data)
    store.publish_meta("targets", tg_data)
    state = store.load_state()
    state.update(
        published=True,
        timestamp_version=0,  # placeholder, set below from bytes
        last_refresh=datetime.now(timezone.utc).isoformat(),
        last_error=None,
    )
    ts_md = Metadata.from_bytes(ts_data)
    snap_md = Metadata.from_bytes(snap_data)
    state["timestamp_version"] = ts_md.signed.version
    state["snapshot_version"] = snap_md.signed.version
    state["targets_version"] = tg_md.signed.version
    state["targets_expires"] = tg_md.signed.expires.isoformat()
    store.save_state(state)


def record_failure(source, stage: str, reason: str) -> None:
    """Mark the refresh as failed; the previously published directory is kept."""
    store = source.store
    state = store.load_state()
    state["last_refresh"] = datetime.now(timezone.utc).isoformat()
    state["last_error"] = {"stage": stage, "reason": reason}
    store.save_state(state)


def listing(source) -> dict:
    """Current published directory: paths, lengths, sha256, role versions."""
    store = source.store
    state = store.load_state()
    result = {
        "published": state["published"],
        "last_refresh": state["last_refresh"],
        "last_error": state["last_error"],
        "root_version": None,
        "timestamp_version": state["timestamp_version"],
        "snapshot_version": state["snapshot_version"],
        "targets_version": state["targets_version"],
        "targets_expires": state["targets_expires"],
        "targets": [],
    }
    root_data = store.load_root()
    if root_data is not None:
        result["root_version"] = Metadata.from_bytes(root_data).signed.version
    tg_data = store.load_meta("targets")
    if tg_data is not None:
        tg_md = Metadata.from_bytes(tg_data)
        for path, tf in sorted(tg_md.signed.targets.items()):
            result["targets"].append(
                {"path": path, "length": tf.length, "sha256": tf.hashes.get("sha256")}
            )
    return result
