"""Global limits and timeouts for the trusted update backend."""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("TUF_DATA_DIR", "data"))

# Metadata size caps (bytes)
MAX_ROOT_BYTES = 1_000_000
MAX_TIMESTAMP_BYTES = 128_000
MAX_SNAPSHOT_BYTES = 1_000_000
MAX_TARGETS_BYTES = 4_000_000

# Target download cap (bytes)
MAX_DOWNLOAD_BYTES = 50_000_000

# Out-of-band root.json upload cap
MAX_UPLOAD_ROOT_BYTES = 1_000_000

# HTTP timeouts (seconds)
HTTP_CONNECT_TIMEOUT = 5.0
HTTP_READ_TIMEOUT = 10.0

# Max root versions chased in one refresh
MAX_ROOT_CHAIN = 128

TOP_LEVEL_ROLES = ("root", "timestamp", "snapshot", "targets")
SUPPORTED_KEYTYPE = "ed25519"
SUPPORTED_SCHEME = "ed25519"
SPEC_MAJOR = "1.0"
