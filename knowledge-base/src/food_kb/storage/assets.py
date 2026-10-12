import mimetypes
import os
from pathlib import Path
import shutil
import tempfile

from ..common import KBError, file_hash


def inspect_file(path, expected=None):
    path = Path(path)
    if not path.is_file():
        raise KBError("SOURCE_MISSING", "Registered source file is missing.")
    sha = file_hash(path)
    if expected and sha != expected.lower():
        raise KBError("HASH_MISMATCH", "File bytes differ from registered fingerprint.")
    with path.open("rb") as f:
        signature = f.read(8)
    media = "application/pdf" if signature.startswith(b"%PDF-") else mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() == ".pdf" and not signature.startswith(b"%PDF-"):
        raise KBError("MEDIA_MISMATCH", "PDF extension does not match its file signature.")
    return {"asset_id": sha, "sha256": sha, "size": path.stat().st_size, "media_type": media,
            "storage_key": f"objects/{sha[:2]}/{sha}"}


def preserve(config, relative, expected=None):
    source = config.source(relative)
    info = inspect_file(source, expected)
    target = config.output_root / info["storage_key"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if file_hash(target) != info["sha256"]:
            raise KBError("STORAGE_CORRUPT", "Existing preserved original has changed; no overwrite performed.")
        return info
    fd, temporary = tempfile.mkstemp(prefix=".copy-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as dest, source.open("rb") as original:
            shutil.copyfileobj(original, dest)
            dest.flush()
            os.fsync(dest.fileno())
        if file_hash(temporary) != info["sha256"]:
            raise KBError("SOURCE_CHANGED", "Source changed while copying.")
        # Hard-link creation is atomic and does not overwrite another concurrent writer.
        try:
            os.link(temporary, target)
        except FileExistsError:
            if file_hash(target) != info["sha256"]:
                raise KBError("STORAGE_CORRUPT", "Concurrent storage conflict.")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return info
