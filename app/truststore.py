"""Filesystem-backed trust state for one update source.

Layout under the source directory:
  trusted/root.json          currently trusted root (updated in place, never rolled back)
  trusted/roots/<v>.root.json  every accepted root version, append-only
  meta/timestamp.json        published metadata set (only replaced as a verified whole)
  meta/snapshot.json
  meta/targets.json
  state.json                 versions, expiry, publish status, last error
"""

import json
import os
from pathlib import Path
from typing import Optional


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class TrustStore:
    def __init__(self, source_dir: Path):
        self.dir = Path(source_dir)
        (self.dir / "trusted" / "roots").mkdir(parents=True, exist_ok=True)
        (self.dir / "meta").mkdir(parents=True, exist_ok=True)

    # --- root chain ---
    def persist_root(self, version: int, data: bytes) -> None:
        _atomic_write(self.dir / "trusted" / "roots" / f"{version}.root.json", data)
        _atomic_write(self.dir / "trusted" / "root.json", data)

    def load_root(self) -> Optional[bytes]:
        p = self.dir / "trusted" / "root.json"
        return p.read_bytes() if p.exists() else None

    # --- published metadata set ---
    def publish_meta(self, name: str, data: bytes) -> None:
        _atomic_write(self.dir / "meta" / f"{name}.json", data)

    def load_meta(self, name: str) -> Optional[bytes]:
        p = self.dir / "meta" / f"{name}.json"
        return p.read_bytes() if p.exists() else None

    # --- state ---
    def load_state(self) -> dict:
        p = self.dir / "state.json"
        if not p.exists():
            return {
                "published": False,
                "timestamp_version": 0,
                "snapshot_version": 0,
                "targets_version": 0,
                "targets_expires": None,
                "last_refresh": None,
                "last_error": None,
            }
        return json.loads(p.read_text())

    def save_state(self, state: dict) -> None:
        _atomic_write(
            self.dir / "state.json", json.dumps(state, indent=2).encode()
        )
