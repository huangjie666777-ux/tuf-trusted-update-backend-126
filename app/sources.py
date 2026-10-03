"""Registry of independent update sources.

Each source has its own trust store, fixed upstream URLs and its own
asyncio lock: refreshes of the same source are serialized, while
different sources never block each other.
"""
import asyncio
import json
import os
import re
from dataclasses import dataclass, field

from tuf.api.metadata import Metadata

from . import config, validation
from .errors import ConfigError
from .store import TrustStore

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


@dataclass
class Source:
    name: str
    metadata_url: str
    targets_url: str
    store: TrustStore
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SourceRegistry:
    def __init__(self, data_dir: str = config.DATA_DIR):
        self.data_dir = data_dir
        self._sources: dict[str, Source] = {}
        os.makedirs(data_dir, exist_ok=True)
        # Reload sources that already have a trust store on disk so trust
        # state and published directories survive restarts.
        for name in sorted(os.listdir(data_dir)):
            src_dir = os.path.join(data_dir, name)
            cfg_path = os.path.join(src_dir, "source.json")
            if not os.path.isdir(src_dir) or not os.path.exists(cfg_path):
                continue
            with open(cfg_path, "rb") as f:
                cfg = json.load(f)
            self._sources[name] = Source(
                name=name,
                metadata_url=cfg["metadata_url"],
                targets_url=cfg["targets_url"],
                store=TrustStore(src_dir),
            )

    def create(self, name: str, metadata_url: str, targets_url: str,
               root_bytes: bytes) -> Source:
        if not _NAME_RE.match(name):
            raise ConfigError(f"invalid source name {name!r}")
        if name in self._sources:
            raise ConfigError(f"source {name!r} already exists")
        for url in (metadata_url, targets_url):
            if not url.startswith(("http://", "https://")):
                raise ConfigError(f"upstream URL must be http(s): {url!r}")
        try:
            root_md = Metadata.from_bytes(root_bytes)
        except Exception as exc:
            raise ConfigError(f"unparseable trusted root.json: {exc}")
        validation.validate_root(root_md)

        store = TrustStore(os.path.join(self.data_dir, name))
        # Persist the out-of-band trusted root as the initial trust anchor.
        store.save_root(root_md.signed.version, root_bytes)
        store.save_state({"last_refresh": None, "last_error": None})
        cfg_path = os.path.join(self.data_dir, name, "source.json")
        with open(cfg_path + ".tmp", "w") as f:
            json.dump({"metadata_url": metadata_url.rstrip("/"),
                       "targets_url": targets_url.rstrip("/")}, f)
        os.replace(cfg_path + ".tmp", cfg_path)

        source = Source(
            name=name,
            metadata_url=metadata_url.rstrip("/"),
            targets_url=targets_url.rstrip("/"),
            store=store,
        )
        self._sources[name] = source
        return source

    def get(self, name: str):
        return self._sources.get(name)

    def names(self):
        return sorted(self._sources)
