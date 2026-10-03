"""Build a local TUF 1.0 signing repository for demos and tests.

Produces a static directory layout:
  metadata/1.root.json, timestamp.json, snapshot.json, targets.json
  targets/<path>...
which can be served with any static HTTP server.
"""
import os
from datetime import datetime, timedelta, timezone

from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import (
    MetaFile,
    Metadata,
    Role,
    Root,
    Snapshot,
    TargetFile,
    Targets,
    Timestamp,
)

SPEC_VERSION = "1.0.0"


def _expires(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


class RepoBuilder:
    """A minimal TUF 1.0 repository: four top-level roles, ed25519,
    consistent_snapshot disabled, no delegations, threshold 1 per role."""

    def __init__(self, repo_dir: str, expires_days: int = 30):
        self.repo_dir = repo_dir
        self.metadata_dir = os.path.join(repo_dir, "metadata")
        self.targets_dir = os.path.join(repo_dir, "targets")
        os.makedirs(self.metadata_dir, exist_ok=True)
        os.makedirs(self.targets_dir, exist_ok=True)
        self.expires_days = expires_days
        self.signers = {role: CryptoSigner.generate_ed25519()
                        for role in ("root", "timestamp", "snapshot", "targets")}
        self.root_version = 0
        self.targets: dict[str, bytes] = {}

    def add_target(self, path: str, data: bytes) -> None:
        self.targets[path] = data
        full = os.path.join(self.targets_dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    def _root_md(self, version: int, root_signer) -> Metadata:
        signers = dict(self.signers)
        signers["root"] = root_signer
        keys = {}
        roles = {}
        for role, signer in signers.items():
            key = signer.public_key
            keys[key.keyid] = key
            roles[role] = Role([key.keyid], 1)
        root = Root(
            version=version,
            spec_version=SPEC_VERSION,
            expires=_expires(self.expires_days),
            keys=keys,
            roles=roles,
            consistent_snapshot=False,
        )
        return Metadata(root)

    def rotate_root(self) -> None:
        """Create root vN+1 with a fresh root key, signed by old and new."""
        old_signer = self.signers["root"]
        new_signer = CryptoSigner.generate_ed25519()
        md = self._root_md(self.root_version + 1, new_signer)
        md.sign(old_signer)
        md.sign(new_signer, append=True)
        self.signers["root"] = new_signer
        self.root_version += 1
        self._write(f"{self.root_version}.root.json", md.to_bytes())

    def build(self) -> None:
        """(Re)generate and sign all metadata from current targets."""
        if self.root_version == 0:
            self.root_version = 1
            root_md = self._root_md(1, self.signers["root"])
            root_md.sign(self.signers["root"])
            self._write("1.root.json", root_md.to_bytes())

        version = getattr(self, "_targets_version", 0) + 1
        targets = Targets(
            version=version, spec_version=SPEC_VERSION,
            expires=_expires(self.expires_days), targets={}, delegations=None,
        )
        for path, data in sorted(self.targets.items()):
            targets.targets[path] = TargetFile.from_data(path, data, ["sha256"])
        targets_md = Metadata(targets)
        targets_md.sign(self.signers["targets"])
        targets_bytes = targets_md.to_bytes()
        self._write("targets.json", targets_bytes)
        self._targets_version = targets.version

        tf = TargetFile.from_data("targets.json", targets_bytes, ["sha256"])
        meta_entry = MetaFile(targets.version, tf.length, tf.hashes)
        snapshot = Snapshot(
            version=targets.version, spec_version=SPEC_VERSION,
            expires=_expires(self.expires_days), meta={"targets.json": meta_entry},
        )
        snapshot_md = Metadata(snapshot)
        snapshot_md.sign(self.signers["snapshot"])
        snapshot_bytes = snapshot_md.to_bytes()
        self._write("snapshot.json", snapshot_bytes)

        sf = TargetFile.from_data("snapshot.json", snapshot_bytes, ["sha256"])
        snap_entry = MetaFile(snapshot.version, sf.length, sf.hashes)
        timestamp = Timestamp(
            version=targets.version, spec_version=SPEC_VERSION,
            expires=_expires(self.expires_days), snapshot_meta=snap_entry,
        )
        timestamp_md = Metadata(timestamp)
        timestamp_md.sign(self.signers["timestamp"])
        self._write("timestamp.json", timestamp_md.to_bytes())

    def trusted_root_bytes(self) -> bytes:
        with open(os.path.join(self.metadata_dir, "1.root.json"), "rb") as f:
            return f.read()

    def _write(self, name: str, data: bytes) -> None:
        with open(os.path.join(self.metadata_dir, name), "wb") as f:
            f.write(data)
