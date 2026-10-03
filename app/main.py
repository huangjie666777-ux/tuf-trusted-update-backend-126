"""FastAPI entry point for the trusted software update backend."""
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from . import downloads, refresh
from .errors import ConfigError, UpdateError
from .sources import SourceRegistry

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="TUF trusted update backend")
registry = SourceRegistry()


class CreateSourceRequest(BaseModel):
    name: str
    metadata_url: str
    targets_url: str
    root_json: str  # out-of-band trusted root.json (JSON text)


def _get_source(name: str):
    source = registry.get(name)
    if source is None:
        raise HTTPException(404, f"unknown source {name!r}")
    return source


@app.post("/sources", status_code=201)
def create_source(req: CreateSourceRequest):
    try:
        source = registry.create(
            req.name, req.metadata_url, req.targets_url, req.root_json.encode()
        )
    except ConfigError as exc:
        raise HTTPException(400, {"stage": exc.stage, "reason": exc.reason})
    return {"name": source.name, "status": "trust anchor installed"}


@app.get("/sources")
def list_sources():
    return {"sources": registry.names()}


@app.get("/sources/{name}")
def source_status(name: str):
    source = _get_source(name)
    state = source.store.load_state()
    manifest = source.store.load_manifest()
    return {
        "name": name,
        "metadata_url": source.metadata_url,
        "targets_url": source.targets_url,
        "last_refresh": state.get("last_refresh"),
        "refresh_failed": state.get("last_error") is not None,
        "last_error": state.get("last_error"),
        "published": manifest,
    }


@app.post("/sources/{name}/refresh")
async def refresh_source(name: str):
    source = _get_source(name)
    # Same-source refreshes are serialized; other sources are unaffected.
    async with source.lock:
        try:
            manifest = await refresh.refresh_source(source)
        except UpdateError as exc:
            refresh.record_failure(source, exc)
            return {
                "status": "failed",
                "stage": exc.stage,
                "reason": exc.reason,
                "published": source.store.load_manifest(),
            }
    return {"status": "ok", "published": manifest}


@app.get("/sources/{name}/targets/{path:path}")
async def get_target(name: str, path: str):
    source = _get_source(name)
    try:
        norm = downloads.normalize_target_path(path)
        entry = downloads.get_pinned_entry(source, norm)
        tmp_path = await downloads.download_verified(source, entry)
    except UpdateError as exc:
        status = getattr(exc, "status_code", 400)
        raise HTTPException(status, {"stage": exc.stage, "reason": exc.reason})
    return FileResponse(
        tmp_path,
        filename=os.path.basename(norm),
        background=BackgroundTask(os.unlink, tmp_path),
    )
