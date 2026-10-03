"""Validation of repository configuration against this service's policy.

Policy: TUF spec 1.0.x, Ed25519 keys only, exactly the four top-level
roles, consistent snapshots disabled, no delegations.
"""
from tuf.api.metadata import Metadata, Root, Targets

from . import config
from .errors import ConfigError


def _check_spec_version(signed) -> None:
    try:
        major, minor, _ = (int(p) for p in signed.spec_version.split("."))
    except ValueError:
        raise ConfigError(f"unparseable spec_version {signed.spec_version!r}")
    if major != config.SUPPORTED_SPEC_MAJOR or minor != config.SUPPORTED_SPEC_MINOR:
        raise ConfigError(
            f"unsupported spec_version {signed.spec_version!r}: only TUF 1.0.x is accepted"
        )


def validate_root(md: Metadata) -> None:
    """Reject roots that violate the supported configuration."""
    if not isinstance(md.signed, Root):
        raise ConfigError("metadata is not a root role")
    root = md.signed
    _check_spec_version(root)
    if root.consistent_snapshot:
        raise ConfigError("consistent_snapshot is not supported and must be false")
    roles = set(root.roles.keys())
    expected = set(config.TOP_LEVEL_ROLES)
    if roles != expected:
        raise ConfigError(f"roles must be exactly {sorted(expected)}, got {sorted(roles)}")
    for role_name, role in root.roles.items():
        if role.threshold < 1:
            raise ConfigError(f"role {role_name} has invalid threshold {role.threshold}")
        if not role.keyids:
            raise ConfigError(f"role {role_name} has no keys")
    for keyid, key in root.keys.items():
        if key.keytype != config.REQUIRED_KEYTYPE or key.scheme != config.REQUIRED_SCHEME:
            raise ConfigError(
                f"key {keyid} is {key.keytype}/{key.scheme}: only ed25519 is accepted"
            )


def validate_targets(md: Metadata) -> None:
    """Reject targets metadata that uses unsupported features."""
    if not isinstance(md.signed, Targets):
        raise ConfigError("metadata is not a targets role")
    _check_spec_version(md.signed)
    if md.signed.delegations is not None:
        raise ConfigError("delegations are not supported")
