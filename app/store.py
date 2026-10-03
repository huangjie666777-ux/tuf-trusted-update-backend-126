"""Persistent trust store for one update source.

Layout under <data_dir>/<source>/:
  metadata/root.json           current trusted root
  metadata/<n>.root.json       every accepted root version (audit trail)
  metadata/timestamp.json      latest verified timestamp (if any)
  metadata/snapshot.json       latest verified snapshot (if any)
  metadata/targets.json        latest verified targets (if any)
  published/manifest.json      currently published target directory
  published/targets.json       targets metadata the manifest was built from
  state.json                   refresh status / last error

All writes are atomic (tmp file + rename) so a crash never leaves a
half-written trust state, and a failed refresh never touches the
previously published directory.
"""
import json
import os
import tempfile
from typing import Optional


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class TrustStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.metadata_dir = os.path.join(base_dir, "metadata")
        self.published_dir = os.path.join(base_dir, "published")
        self.state_path = os.path.join(base_dir, "state.json")

    # -- trusted metadata -------------------------------------------------
    def metadata_path(self, filename: str) -> str:
        return os.path.join(self.metadata_dir, filename)

    def load_metadata(self, filename: str) -> Optional[bytes]:
        path = self.metadata_path(filename)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def save_metadata(self, filename: str, data: bytes) -> None:
        _atomic_write(self.metadata_path(filename), data)

    def save_root(self, version: int, data: bytes) -> None:
        """Persist an accepted root immediately; never rolled back."""
        _atomic_write(self.metadata_path(f"{version}.root.json"), data)
        _atomic_write(self.metadata_path("root.json"), data)

    # -- published directory ----------------------------------------------
    def publish(self, manifest: dict, targets_bytes: bytes) -> None:
        _atomic_write(
            os.path.join(self.published_dir, "manifest.json"),
            json.dumps(manifest, indent=2, sort_keys=True).encode(),
        )
        _atomic_write(os.path.join(self.published_dir, "targets.json"), targets_bytes)

    def load_manifest(self) -> Optional[dict]:
        path = os.path.join(self.published_dir, "manifest.json")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return json.load(f)

    # -- refresh status -----------------------------------------------------
    def load_state(self) -> dict:
        if not os.path.exists(self.state_path):
            return {"last_refresh": None, "last_error": None}
        with open(self.state_path, "rb") as f:
            return json.load(f)

    def save_state(self, state: dict) -> None:
        _atomic_write(self.state_path, json.dumps(state, indent=2, sort_keys=True).encode())
