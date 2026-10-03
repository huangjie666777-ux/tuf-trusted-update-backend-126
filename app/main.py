"""FastAPI entrypoint for the trusted software update backend."""

import os
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config
from .downloads import download_target
from .errors import DownloadError, RefreshError
from .publisher import listing, record_failure
from .sources import Registry
from .tuf_refresh import refresh


class CreateSourceRequest(BaseModel):
    upstream_url: str = Field(min_length=1)
    root_json: str = Field(description="out-of-band trusted root.json content")


def create_app(data_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Trusted Update Backend")
    app.state.registry = Registry(data_dir or config.DATA_DIR)

    def get_source(source_id: str):
        source = app.state.registry.get(source_id)
        if source is None:
            raise HTTPException(404, f"unknown source {source_id!r}")
        return source

    @app.post("/sources", status_code=201)
    def create_source(req: CreateSourceRequest):
        try:
            source = app.state.registry.create(req.upstream_url, req.root_json.encode())
        except RefreshError as exc:
            raise HTTPException(422, {"stage": exc.stage, "reason": exc.reason})
        return {"source_id": source.id, "upstream_url": source.upstream_url}

    @app.get("/sources")
    def list_sources():
        return [
            {"source_id": s.id, "upstream_url": s.upstream_url}
            for s in app.state.registry.list()
        ]

    @app.get("/sources/{source_id}")
    def source_status(source_id: str):
        source = get_source(source_id)
        return {"source_id": source.id, "upstream_url": source.upstream_url, **listing(source)}

    @app.post("/sources/{source_id}/refresh")
    async def refresh_source(source_id: str):
        source = get_source(source_id)
        # Same-source refreshes are serialized; different sources never block.
        async with source.lock:
            try:
                report = refresh(source)
            except RefreshError as exc:
                record_failure(source, exc.stage, exc.reason)
                raise HTTPException(422, {"stage": exc.stage, "reason": exc.reason})
        return {"status": "ok", **report}

    @app.get("/sources/{source_id}/targets")
    def list_targets(source_id: str):
        source = get_source(source_id)
        return listing(source)

    @app.get("/sources/{source_id}/download/{target_path:path}")
    def download(source_id: str, target_path: str, background: BackgroundTasks):
        source = get_source(source_id)
        try:
            tmp_path, relpath = download_target(source, target_path)
        except DownloadError as exc:
            raise HTTPException(410, str(exc))

        def cleanup():
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        background.add_task(cleanup)
        return FileResponse(tmp_path, filename=os.path.basename(relpath))

    return app


app = create_app()
