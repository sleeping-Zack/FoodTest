"""Export an offline reader for actual chunks and their bounded source context."""
import html
import json
import os
from pathlib import Path
import shutil
from urllib.parse import quote


def _relative_url(path, directory):
    try:
        return quote(os.path.relpath(path, directory).replace(os.sep, "/"), safe="/:")
    except ValueError:
        return Path(path).as_uri()


def write_viewer(bundle, output_path, source_dir):
    from .context import expand_context

    output = Path(output_path).resolve()
    sources = Path(source_dir).resolve()
    if output == sources or output.is_relative_to(sources):
        raise ValueError("Write the viewer outside the preserved source directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    resources = Path(__file__).with_name("viewer_assets")
    resource_output = output.parent / (output.stem + "-assets")
    shutil.copytree(resources, resource_output, dirs_exist_ok=True)
    context = {chunk["chunk_id"]: expand_context(bundle, chunk["chunk_id"], max_chars=24000, max_blocks=60)
               for chunk in bundle["chunks"]}
    images = {}
    asset_root = sources / "assets"
    if asset_root.is_dir():
        for image in sorted(asset_root.iterdir()):
            if image.is_file() and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"} and image.resolve().is_relative_to(sources):
                images["assets/" + image.name] = _relative_url(image, output.parent)
    page_map = {}
    mapping = sources / "source-map.json"
    if mapping.is_file():
        for page in json.loads(mapping.read_text(encoding="utf-8-sig")).get("pages", []):
            number = page.get("pdf_page")
            if type(number) is int and number > 0:
                page_map[str(number)] = {"printed_page": page.get("printed_page"),
                                         "image": images.get(page.get("image", ""))}
    for number in range(1, int(bundle["source"].get("page_count") or 0) + 1):
        page_map.setdefault(str(number), {"printed_page": None, "image": images.get(f"assets/page-{number:02}.png")})
    payload = {"bundle": bundle, "contexts": context, "images": images, "pages": page_map,
               "pdf": _relative_url(sources / "source.pdf", output.parent) if (sources / "source.pdf").is_file() else None,
               "context_budget": {"max_blocks": 60, "max_chars": 24000}}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    data = data.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = (resources / "template.html").read_text(encoding="utf-8")
    replacements = {"@@TITLE@@": html.escape(str(bundle["source"].get("title", "原文切分结果"))),
                    "@@RESOURCES@@": html.escape(_relative_url(resource_output, output.parent), quote=True),
                    "@@DATA@@": data}
    # Split the template before inserting data so input text cannot expand template tokens.
    import re
    page = re.sub(r"@@TITLE@@|@@RESOURCES@@|@@DATA@@", lambda match: replacements[match.group()], page)
    output.write_text(page, encoding="utf-8")
