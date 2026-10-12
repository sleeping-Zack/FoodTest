from dataclasses import dataclass
import os
from pathlib import Path

from .common import KBError, digest, read_json


@dataclass(frozen=True)
class Config:
    source_root: Path
    allowed_sources: tuple[Path, ...]
    output_root: Path
    database: dict
    provider: str = "disabled"

    @classmethod
    def load(cls, path):
        if not path:
            raise KBError("CONFIG_MISSING", "Provide --config with source_root, allowed_sources, output_root and database.")
        p = Path(path).resolve()
        raw = read_json(p)
        required = {"source_root", "allowed_sources", "output_root", "database"}
        if not required <= raw.keys():
            raise KBError("CONFIG_MISSING", "Required configuration fields are missing.")
        unknown = raw.keys() - (required | {"provider"})
        if unknown:
            raise KBError("CONFIG_UNKNOWN", "Unknown configuration fields.")
        root = (p.parent / raw["source_root"]).resolve()
        output = (p.parent / raw["output_root"]).resolve()
        allowed = tuple((root / x).resolve() for x in raw["allowed_sources"])
        if not root.is_dir() or not allowed:
            raise KBError("SOURCE_MISSING", "Source root and nonempty allowed_sources are required.")
        for source in allowed:
            if not source.is_relative_to(root) or source == root:
                raise KBError("PATH_BOUNDARY", "Source allowlist must be below source_root.")
            if output.is_relative_to(source) or source.is_relative_to(output):
                raise KBError("PATH_BOUNDARY", "Output and source locations must not overlap.")
        if output.exists() and not output.is_dir():
            raise KBError("PATH_BOUNDARY", "Output must be a directory, not an original file.")
        db = raw["database"]
        if set(db) != {"host", "port", "dbname", "user", "password_env"}:
            raise KBError("CONFIG_DATABASE", "Database requires host, port, dbname, user and password_env.")
        if db["host"] not in {"127.0.0.1", "::1"} or not (db["dbname"].startswith("food_kb") or db["dbname"] == "foodtest"):
            raise KBError("LOCAL_ONLY", "M1 supports loopback food_kb databases and the user-designated foodtest database only.")
        if not isinstance(db["port"], int) or not 1024 <= db["port"] <= 65535:
            raise KBError("CONFIG_DATABASE", "Invalid database port.")
        if raw.get("provider", "disabled") != "disabled":
            raise KBError("PROVIDER_DISABLED", "External and model providers are disabled in M1.")
        return cls(root, allowed, output, db)

    def source(self, relative):
        value = Path(relative)
        if value.is_absolute() or ".." in value.parts:
            raise KBError("PATH_BOUNDARY", "Source must be a relative path without traversal.")
        path = (self.source_root / value).resolve()
        if not any(path == x or path.is_relative_to(x) for x in self.allowed_sources):
            raise KBError("PATH_BOUNDARY", "Source is outside the explicit allowlist.")
        return path

    @property
    def fingerprint(self):
        return digest({"sources": [p.relative_to(self.source_root).as_posix() for p in self.allowed_sources],
                       "provider": self.provider, "database": {k: v for k, v in self.database.items() if k != "password_env"}})

    def connection_args(self):
        password = os.environ.get(self.database["password_env"])
        if not password:
            raise KBError("PASSWORD_MISSING", "Database password environment variable is unset.")
        return {k: v for k, v in self.database.items() if k != "password_env"} | {"password": password, "connect_timeout": 5}
