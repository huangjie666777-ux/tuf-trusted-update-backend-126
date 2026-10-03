# Trusted Software Update Backend

Pure-backend trusted software update service built on TUF 1.0. It protects
against tampering and rollback/downgrade attacks by verifying the full TUF
metadata chain before publishing anything, and by verifying every downloaded
target byte against pinned, signed metadata.

## Trust anchor and scope

- **Trust anchor**: each update source is created with an **out-of-band
  trusted root.json** (POST /sources). This initial root is the only trust
  anchor; everything else is derived from it via TUF verification.
- **Supported configuration** (anything else is rejected):
  - TUF spec version **1.0.x**
  - **Ed25519** keys and signatures only
  - exactly the four top-level roles: root, timestamp, snapshot, targets
  - **consistent snapshots disabled**
  - **no delegations**, no target installation (download + verify only)
- Each source has independent trust state under data/sources/<id>/ and an
  independent refresh lock: same-source refreshes are serialized, different
  sources never block each other.

## Refresh semantics

POST /sources/{id}/refresh performs, in order:

1. **Root rotation**: fetch N+1.root.json, N+2.root.json, ... in version
   order. Each step must satisfy the signature threshold of **both** the old
   root and the new root (a key counts at most once per threshold). Every
   accepted root is **persisted immediately** (trusted/roots/<v>.root.json)
   and is never rolled back, even if a later stage fails.
2. **Timestamp**: role signature threshold, expiry, and version must be newer
   than the stored one (rollback protection).
3. **Snapshot**: length + SHA256 pinned by timestamp, role signatures, expiry,
   version match and no rollback vs. stored version.
4. **Targets**: length + SHA256 pinned by snapshot, role signatures, expiry,
   version match and no rollback; delegations rejected.
5. **Publish**: only after all checks pass, the metadata set is atomically
   published as the source's current directory. On failure the old directory
   stays published and last_error records the failing stage and reason —
   metadata from different runs is never mixed.

GET /sources/{id}/targets lists the published directory: target paths,
lengths, SHA256 digests and the role versions.

## Download semantics

GET /sources/{id}/download/{path}:

- pins the **currently published** targets metadata at request start, so a
  concurrent refresh cannot change the basis of the download;
- refuses to serve from an **expired** directory;
- rejects unknown targets and path traversal (.., absolute paths);
- streams the upstream file to a **temporary file** with a hard size cap,
  then checks **length and SHA256** — truncated, oversized or mismatched
  content is discarded and never delivered.

## Limits

See app/config.py: per-role metadata size caps, download size cap
(50 MiB default), HTTP connect/read timeouts, root-chain length cap.

## Layout

- app/truststore.py — persistent trust state (roots, published meta, state)
- app/tuf_refresh.py — TUF refresh/verification pipeline
- app/publisher.py — atomic directory publish + failure recording
- app/downloads.py — pinned, verified target downloads
- app/sources.py — source registry, per-source locks, restart recovery
- app/main.py — FastAPI HTTP API
- demo/make_repo.py — local signed demo repository (incl. root key rotation)
- tests/test_api.py — end-to-end tests (refresh, tamper, rollback, restart)

## Demo

\`\`\`bash
# 1. generate a signed demo repository (root v1 -> v2 key rotation included)
.venv/bin/python demo/make_repo.py demo/repo

# 2. serve the repository (upstream)
(cd demo/repo && ../../.venv/bin/python -m http.server 8001 &)

# 3. start the update backend
.venv/bin/python -m uvicorn app.main:app --port 8000 &

# 4. create a source with the out-of-band trust anchor
ROOT=$(python3 -c "import json;print(json.dumps(open('demo/repo/root.json').read()))")
SID=$(curl -s -X POST http://127.0.0.1:8000/sources -H 'Content-Type: application/json' \
  -d "{\"upstream_url\":\"http://127.0.0.1:8001\",\"root_json\":$ROOT}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['source_id'])")

# 5. refresh (follows root rotation 1 -> 2, verifies the whole chain)
curl -s -X POST http://127.0.0.1:8000/sources/$SID/refresh

# 6. list the published directory and download a verified target
curl -s http://127.0.0.1:8000/sources/$SID/targets
curl -s http://127.0.0.1:8000/sources/$SID/download/hello.txt
\`\`\`

Or run the scripted version: bash demo/run_demo.sh

## Tests

\`\`\`bash
.venv/bin/python -m pytest tests -q
\`\`\`
