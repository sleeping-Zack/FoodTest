"""Write an offline reader of frozen classification examples and source evidence."""
import html
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from food_kb.common import file_hash


def write_viewer(snapshot, examples, output_path, source_root):
    output = Path(output_path).resolve()
    sources = Path(source_root).resolve()
    if output == sources or output.is_relative_to(sources):
        raise ValueError("Write the viewer outside the preserved source directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    resources = Path(__file__).with_name("viewer_assets")
    resource_output = output.parent / (output.stem + "-assets")
    shutil.copytree(resources, resource_output, dirs_exist_ok=True)
    source_files = {}
    for source in snapshot.get("sources", []):
        key = str(source.get("source_key", ""))
        candidate = (sources / key).resolve()
        entry = {"status": "missing", "url": None}
        if not key or Path(key).is_absolute() or not candidate.is_relative_to(sources):
            entry["status"] = "path_rejected"
        elif candidate.is_file():
            expected = source.get("sha256") or source.get("asset_id")
            if source.get("status") != "hash_verified" or not expected:
                entry["status"] = "unverified"
            elif file_hash(candidate) != expected:
                entry["status"] = "hash_mismatch"
            else:
                relative = os.path.relpath(candidate, output.parent).replace(os.sep, "/")
                entry = {"status": "hash_verified", "url": quote(relative, safe="/")}
        source_files[key] = entry
    payload = {"snapshot": snapshot, "examples": examples, "source_files": source_files}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    data = data.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    resource_url = quote(os.path.relpath(resource_output, output.parent).replace(os.sep, "/"), safe="/")
    replacements = {"@@TITLE@@": html.escape("KB-08 分类与适用结果"),
                    "@@RESOURCES@@": html.escape(resource_url, quote=True), "@@DATA@@": data}
    template = (resources / "template.html").read_text(encoding="utf-8")
    output.write_text(re.sub(r"@@TITLE@@|@@RESOURCES@@|@@DATA@@", lambda match: replacements[match.group()], template), encoding="utf-8")
