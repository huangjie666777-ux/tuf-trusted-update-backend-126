import functools
import hashlib
import http.server
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from demo.make_repo import main as make_repo

HELLO = b"hello from the trusted demo repository" + bytes([10])


@pytest.fixture()
def repo_server(tmp_path):
    repo_dir = tmp_path / "repo"
    make_repo(repo_dir)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=repo_dir)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", repo_dir
    server.shutdown()


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path / "data"))


def create_source(client, repo_server):
    upstream, repo_dir = repo_server
    root = (repo_dir / "root.json").read_text()
    resp = client.post("/sources", json={"upstream_url": upstream, "root_json": root})
    assert resp.status_code == 201, resp.text
    return resp.json()["source_id"]


def test_refresh_and_download(client, repo_server):
    sid = create_source(client, repo_server)
    resp = client.post(f"/sources/{sid}/refresh")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["root_version"] == 2  # rotated root chain followed
    assert report["targets_version"] == 1

    listing = client.get(f"/sources/{sid}/targets").json()
    assert listing["published"] is True
    assert listing["targets"] == [
        {"path": "hello.txt", "length": len(HELLO), "sha256": hashlib.sha256(HELLO).hexdigest()}
    ]

    resp = client.get(f"/sources/{sid}/download/hello.txt")
    assert resp.status_code == 200
    assert resp.content == HELLO


def test_unknown_and_traversal_rejected(client, repo_server):
    sid = create_source(client, repo_server)
    assert client.post(f"/sources/{sid}/refresh").status_code == 200
    assert client.get(f"/sources/{sid}/download/nope.txt").status_code == 410
    assert client.get(f"/sources/{sid}/download/..%2F..%2Fetc%2Fpasswd").status_code in (404, 410)


def test_tampered_targets_rejected(client, repo_server):
    upstream, repo_dir = repo_server
    sid = create_source(client, repo_server)
    assert client.post(f"/sources/{sid}/refresh").status_code == 200
    (repo_dir / "targets" / "hello.txt").write_bytes(b"evil\\n")
    resp = client.get(f"/sources/{sid}/download/hello.txt")
    assert resp.status_code == 410


def test_tampered_metadata_refresh_fails_and_keeps_old(client, repo_server):
    upstream, repo_dir = repo_server
    sid = create_source(client, repo_server)
    assert client.post(f"/sources/{sid}/refresh").status_code == 200
    ts = json.loads((repo_dir / "timestamp.json").read_text())
    ts["signatures"][0]["sig"] = "00" * 64
    (repo_dir / "timestamp.json").write_text(json.dumps(ts))
    resp = client.post(f"/sources/{sid}/refresh")
    assert resp.status_code == 422
    assert resp.json()["detail"]["stage"] == "timestamp"
    status = client.get(f"/sources/{sid}").json()
    assert status["published"] is True
    assert status["last_error"]["stage"] == "timestamp"
    assert client.get(f"/sources/{sid}/download/hello.txt").status_code == 200


def test_bad_root_config_rejected(client, repo_server):
    upstream, repo_dir = repo_server
    root = json.loads((repo_dir / "root.json").read_text())
    root["signed"]["consistent_snapshot"] = True
    resp = client.post("/sources", json={"upstream_url": upstream, "root_json": json.dumps(root)})
    assert resp.status_code == 422
    assert "consistent" in resp.json()["detail"]["reason"]


def test_state_survives_restart(client, repo_server, tmp_path):
    sid = create_source(client, repo_server)
    assert client.post(f"/sources/{sid}/refresh").status_code == 200
    client2 = TestClient(create_app(tmp_path / "data"))
    status = client2.get(f"/sources/{sid}").json()
    assert status["published"] is True
    assert status["root_version"] == 2
    assert client2.get(f"/sources/{sid}/download/hello.txt").status_code == 200
