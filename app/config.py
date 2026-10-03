"""Service-wide limits and constants."""
import os

DATA_DIR = os.environ.get("TUF_UPDATE_DATA_DIR", "data")

# Metadata size caps (bytes)
MAX_ROOT_BYTES = 512 * 1024
MAX_TIMESTAMP_BYTES = 64 * 1024
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_TARGETS_BYTES = 4 * 1024 * 1024

# Target download cap (bytes)
MAX_TARGET_BYTES = 64 * 1024 * 1024

# Upstream HTTP timeout (seconds)
HTTP_TIMEOUT = 10.0

# Supported TUF spec version (1.0.x)
SUPPORTED_SPEC_MAJOR = 1
SUPPORTED_SPEC_MINOR = 0

TOP_LEVEL_ROLES = ("root", "timestamp", "snapshot", "targets")
REQUIRED_KEYTYPE = "ed25519"
REQUIRED_SCHEME = "ed25519"
