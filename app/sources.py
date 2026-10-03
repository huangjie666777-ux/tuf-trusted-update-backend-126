"""Update source registry: independent trust state and per-source refresh lock."""

import asyncio
import uuid
from pathlib import Path

from tuf.api.exceptions import UnsignedMetadataError
from tuf.api.metadata import Metadata

from . import config
from .errors import RefreshError
from .truststore import TrustStore
from .tuf_refresh import validate_root_config


class Source:
    def __init__(self, source_id: str, upstream_url: str, source_dir: Path):
        self.id = source_id
        self.upstream_url = upstream_url.rstrip("/")
        self.dir = Path(source_dir)
        self.store = TrustStore(self.dir)
        self.lock = asyncio.Lock()
        self.tmp_dir = self.dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "upstream_url").write_text(self.upstream_url)


class Registry:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.sources_dir = self.data_dir / "sources"
        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self._sources: dict[str, Source] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        for child in sorted(self.sources_dir.iterdir()):
            url_file = child / "upstream_url"
            if child.is_dir() and url_file.exists():
                source = Source(child.name, url_file.read_text().strip(), child)
                self._sources[child.name] = source

    def create(self, upstream_url: str, root_data: bytes) -> Source:
        if len(root_data) > config.MAX_UPLOAD_ROOT_BYTES:
            raise RefreshError("root", "root.json upload too large")
        try:
            root_md = Metadata.from_bytes(root_data)
        except Exception as exc:
            raise RefreshError("root", f"cannot parse root.json: {exc}")
        if root_md.signed.type != "root":
            raise RefreshError("root", "uploaded metadata is not a root role")
        validate_root_config(root_md.signed)
        try:
            root_md.verify_delegate("root", root_md)
        except UnsignedMetadataError as exc:
            raise RefreshError("root", f"root.json is not validly self-signed: {exc}")
        if root_md.signed.is_expired():
            raise RefreshError("root", "root.json is already expired")
        source_id = uuid.uuid4().hex[:12]
        source = Source(source_id, upstream_url, self.sources_dir / source_id)
        source.store.persist_root(root_md.signed.version, root_data)
        self._sources[source_id] = source
        return source

    def get(self, source_id: str):
        return self._sources.get(source_id)

    def list(self):
        return list(self._sources.values())
