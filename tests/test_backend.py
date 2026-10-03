import functools
import http.server
import json
import os
import shutil
import threading

import pytest
from fastapi.testclient import TestClient

from app import config
from app.downloads import normalize_target_path
from app.errors import DownloadError
from app.main import app
from app.sources import SourceRegistry
from demo.repolib import RepoBuilder


class RepoServer:
    def __init__(self, repo_dir):
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=repo_dir
        )
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        host, port = self.httpd.server_address
        return f"http://127.0.0.1:{port}"

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture()
def repo(tmp_path):
    repo_dir = tmp_path / "repo"
    builder = RepoBuilder(str(repo_dir))
    builder.add_target("app/hello.txt", b"hello trusted world\n")
    builder.build()
    server = RepoServer(str(repo_dir))
    yield builder, server
    server.stop()


client = TestClient(app)
_counter = 0


def make_source(builder, server):
    global _counter
    _counter += 1
    name = f"src{_counter}"
    resp = client.post("/sources", json={
        "name": name,
        "metadata_url": f"{server.base_url}/metadata",
        "targets_url": f"{server.base_url}/targets",
        "root_json": builder.trusted_root_bytes().decode(),
    })
    assert resp.status_code == 201, resp.text
    return name


def test_refresh_and_download(repo):
    builder, server = repo
    name = make_source(builder, server)

    resp = client.post(f"/sources/{name}/refresh")
    body = resp.json()
    assert body["status"] == "ok", body
    files = body["published"]["files"]
    assert files[0]["path"] == "app/hello.txt"
    assert files[0]["length"] == len(b"hello trusted world\n")
    assert len(files[0]["sha256"]) == 64
    assert files[0]["role_version"] == 1

    resp = client.get(f"/sources/{name}/targets/app/hello.txt")
    assert resp.status_code == 200
    assert resp.content == b"hello trusted world\n"

    status = client.get(f"/sources/{name}").json()
    assert status["refresh_failed"] is False
    assert status["published"]["targets_version"] == 1


def test_tampered_target_rejected(repo):
    builder, server = repo
    name = make_source(builder, server)
    assert client.post(f"/sources/{name}/refresh").json()["status"] == "ok"
    target = os.path.join(builder.targets_dir, "app/hello.txt")
    with open(target, "wb") as f:
        f.write(b"evil tampered world!")
    resp = client.get(f"/sources/{name}/targets/app/hello.txt")
    assert resp.status_code == 502
    assert "sha256" in resp.json()["detail"]["reason"]


def test_truncated_and_unknown_target(repo):
    builder, server = repo
    name = make_source(builder, server)
    assert client.post(f"/sources/{name}/refresh").json()["status"] == "ok"
    target = os.path.join(builder.targets_dir, "app/hello.txt")
    with open(target, "wb") as f:
        f.write(b"short")
    assert client.get(f"/sources/{name}/targets/app/hello.txt").status_code == 502
    assert client.get(f"/sources/{name}/targets/nope.txt").status_code == 404


def test_path_traversal_rejected():
    for bad in ("../x", "a/../../b", "/abs", "a\\b", ".."):
        with pytest.raises(DownloadError):
            normalize_target_path(bad)
    assert normalize_target_path("a/b/c.txt") == "a/b/c.txt"


def test_root_rotation(repo):
    builder, server = repo
    name = make_source(builder, server)
    assert client.post(f"/sources/{name}/refresh").json()["status"] == "ok"
    builder.rotate_root()
    resp = client.post(f"/sources/{name}/refresh").json()
    assert resp["status"] == "ok", resp
    # Accepted root v2 persisted immediately and survives restart.
    store_dir = os.path.join(config.DATA_DIR, name, "metadata")
    assert os.path.exists(os.path.join(store_dir, "2.root.json"))


def test_rollback_rejected_and_old_published_kept(repo):
    builder, server = repo
    name = make_source(builder, server)
    assert client.post(f"/sources/{name}/refresh").json()["status"] == "ok"

    saved = tmp = os.path.join(builder.repo_dir, "saved")
    os.makedirs(saved)
    builder.add_target("app/hello.txt", b"version two payload\n")
    builder.build()  # targets v2
    resp = client.post(f"/sources/{name}/refresh").json()
    assert resp["status"] == "ok", resp
    assert resp["published"]["targets_version"] == 2

    # Attacker rolls the repository back to v1 metadata.
    for fname in ("timestamp.json", "snapshot.json", "targets.json"):
        shutil.copy(os.path.join(builder.metadata_dir, fname),
                    os.path.join(saved, fname + ".v2"))
    builder._targets_version = 0
    builder.targets["app/hello.txt"] = b"hello trusted world\n"
    builder.build()  # regenerates v1 metadata
    resp = client.post(f"/sources/{name}/refresh").json()
    assert resp["status"] == "failed"
    assert resp["stage"] == "timestamp"
    # Old published directory is kept and failure is marked.
    assert resp["published"]["targets_version"] == 2
    status = client.get(f"/sources/{name}").json()
    assert status["refresh_failed"] is True
    assert status["last_error"]["stage"] == "timestamp"


def test_bad_config_rejected(repo):
    builder, server = repo
    root = json.loads(builder.trusted_root_bytes())
    root["signed"]["consistent_snapshot"] = True
    resp = client.post("/sources", json={
        "name": "badcfg",
        "metadata_url": f"{server.base_url}/metadata",
        "targets_url": f"{server.base_url}/targets",
        "root_json": json.dumps(root),
    })
    assert resp.status_code == 400
    assert "consistent_snapshot" in resp.json()["detail"]["reason"]


def test_restart_preserves_state(repo):
    builder, server = repo
    name = make_source(builder, server)
    assert client.post(f"/sources/{name}/refresh").json()["status"] == "ok"
    registry2 = SourceRegistry(config.DATA_DIR)
    source = registry2.get(name)
    assert source is not None
    assert source.store.load_manifest()["targets_version"] == 1


def test_expired_directory_rejects_download(repo):
    builder, server = repo
    name = make_source(builder, server)
    assert client.post(f"/sources/{name}/refresh").json()["status"] == "ok"
    manifest_path = os.path.join(config.DATA_DIR, name, "published", "manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)
    manifest["expires"] = "2000-01-01T00:00:00+00:00"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)
    resp = client.get(f"/sources/{name}/targets/app/hello.txt")
    assert resp.status_code == 409
