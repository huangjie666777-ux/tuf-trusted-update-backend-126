"""Build a local, fully signed TUF 1.0 demo repository.

Creates <out>/ with:
  1.root.json, 2.root.json   (v2 rotates the root key: signed by old AND new)
  timestamp.json, snapshot.json, targets.json
  targets/hello.txt

Usage: .venv/bin/python demo/make_repo.py [outdir]
"""

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

SPEC = "1.0.31"
EXPIRES = datetime.now(timezone.utc) + timedelta(days=30)


def roles_for(keys):
    return {name: Role([k.public_key.keyid for k in keys], 1) for name in ("root", "timestamp", "snapshot", "targets")}


def keydict_for(signers):
    return {s.public_key.keyid: s.public_key for s in signers}


def sign(md: Metadata, *signers) -> None:
    md.signatures.clear()
    for s in signers:
        md.sign(s, append=True)


def main(out=None) -> None:
    out = Path(out or (sys.argv[1] if len(sys.argv) > 1 else "demo/repo"))
    targets_dir = out / "targets"
    targets_dir.mkdir(parents=True, exist_ok=True)

    root_v1_signer = CryptoSigner.generate_ed25519()
    root_v2_signer = CryptoSigner.generate_ed25519()
    role_signers = [CryptoSigner.generate_ed25519() for _ in range(3)]  # ts, snap, targets
    ts_signer, snap_signer, tg_signer = role_signers

    # root v1
    root1 = Root(
        version=1,
        spec_version=SPEC,
        expires=EXPIRES,
        keys=keydict_for([root_v1_signer, ts_signer, snap_signer, tg_signer]),
        roles=roles_for([root_v1_signer]) | {
            "timestamp": Role([ts_signer.public_key.keyid], 1),
            "snapshot": Role([snap_signer.public_key.keyid], 1),
            "targets": Role([tg_signer.public_key.keyid], 1),
        },
        consistent_snapshot=False,
    )
    root1_md = Metadata(root1)
    sign(root1_md, root_v1_signer)
    (out / "1.root.json").write_bytes(root1_md.to_bytes())

    # root v2: rotates the root key, signed by old and new root keys
    root2 = Root(
        version=2,
        spec_version=SPEC,
        expires=EXPIRES,
        keys=keydict_for([root_v2_signer, ts_signer, snap_signer, tg_signer]),
        roles={
            "root": Role([root_v2_signer.public_key.keyid], 1),
            "timestamp": Role([ts_signer.public_key.keyid], 1),
            "snapshot": Role([snap_signer.public_key.keyid], 1),
            "targets": Role([tg_signer.public_key.keyid], 1),
        },
        consistent_snapshot=False,
    )
    root2_md = Metadata(root2)
    sign(root2_md, root_v1_signer, root_v2_signer)
    (out / "2.root.json").write_bytes(root2_md.to_bytes())

    # target file
    content = b"hello from the trusted demo repository\n"
    (targets_dir / "hello.txt").write_bytes(content)
    tf = TargetFile.from_data("hello.txt", content, ["sha256"])

    targets = Targets(
        version=1,
        spec_version=SPEC,
        expires=EXPIRES,
        targets={"hello.txt": tf},
        delegations=None,
    )
    targets_md = Metadata(targets)
    sign(targets_md, tg_signer)
    tg_bytes = targets_md.to_bytes()
    (out / "targets.json").write_bytes(tg_bytes)

    snapshot = Snapshot(
        version=1,
        spec_version=SPEC,
        expires=EXPIRES,
        meta={"targets.json": MetaFile(version=1, length=len(tg_bytes), hashes={"sha256": hashlib.sha256(tg_bytes).hexdigest()})},
    )
    snapshot_md = Metadata(snapshot)
    sign(snapshot_md, snap_signer)
    snap_bytes = snapshot_md.to_bytes()
    (out / "snapshot.json").write_bytes(snap_bytes)

    timestamp = Timestamp(
        version=1,
        spec_version=SPEC,
        expires=EXPIRES,
        snapshot_meta=MetaFile(version=1, length=len(snap_bytes), hashes={"sha256": hashlib.sha256(snap_bytes).hexdigest()}),
    )
    timestamp_md = Metadata(timestamp)
    sign(timestamp_md, ts_signer)
    (out / "timestamp.json").write_bytes(timestamp_md.to_bytes())

    # The out-of-band trust anchor clients should receive:
    (out / "root.json").write_bytes((out / "1.root.json").read_bytes())
    print(f"demo repository written to {out}/")
    print(f"trust anchor: {out}/root.json")


if __name__ == "__main__":
    main()
