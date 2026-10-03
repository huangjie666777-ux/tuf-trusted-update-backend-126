"""Build the published target directory from verified targets metadata."""
from .errors import UpdateError


def build_manifest(targets_md) -> dict:
    """Manifest of the fully verified target directory.

    Lists every target path with its length, sha256 and the targets role
    version it comes from. Only called after the whole refresh pipeline
    succeeded, so a published manifest never mixes metadata generations.
    """
    signed = targets_md.signed
    files = []
    for path, target_file in sorted(signed.targets.items()):
        sha256 = target_file.hashes.get("sha256")
        if sha256 is None:
            raise UpdateError("targets", f"target {path!r} has no sha256 hash")
        files.append(
            {
                "path": path,
                "length": target_file.length,
                "sha256": sha256,
                "role_version": signed.version,
            }
        )
    return {
        "targets_version": signed.version,
        "expires": signed.expires.isoformat(),
        "files": files,
    }
