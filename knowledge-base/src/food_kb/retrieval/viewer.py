"""Write an offline reader of already computed KB-10 retrieval results."""
import html
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from food_kb.common import file_hash


def write_viewer(snapshot, cases, output_path, source_root, evaluation=None):
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
            if source.get("status") in ("unverified", "hash_mismatch") or not expected:
                entry["status"] = "unverified"
            elif file_hash(candidate) != expected:
                entry["status"] = "hash_mismatch"
            else:
                relative = os.path.relpath(candidate, output.parent).replace(os.sep, "/")
                entry = {"status": "hash_verified", "url": quote(relative, safe="/")}
        source_files[key] = entry
    # The corpus, term postings, and global gaps stay in separately saved JSON files.
    summary = {key: snapshot[key] for key in (
        "schema_version", "processor_version", "algorithm_version", "generation_id", "snapshot_id",
        "input_hash", "payload_hash", "summary", "sources", "coverage", "review_state",
        "scope_complete", "executable", "limitations",
    ) if key in snapshot}
    payload = {"snapshot": summary, "cases": cases, "evaluation": evaluation or {},
               "source_files": source_files,
               "omitted_global_fields": [key for key in snapshot if key not in summary]}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    data = data.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    resource_url = quote(os.path.relpath(resource_output, output.parent).replace(os.sep, "/"), safe="/")
    replacements = {"@@TITLE@@": html.escape("KB-10 精确查询与中文全文检索"),
                    "@@RESOURCES@@": html.escape(resource_url, quote=True), "@@DATA@@": data}
    template = (resources / "template.html").read_text(encoding="utf-8")
    output.write_text(re.sub(r"@@TITLE@@|@@RESOURCES@@|@@DATA@@", lambda match: replacements[match.group()], template), encoding="utf-8")
