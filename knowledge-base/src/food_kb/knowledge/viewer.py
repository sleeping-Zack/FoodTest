"""Export a local, read-only reader of candidate knowledge and its evidence."""
import html
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote

from food_kb.knowledge.facts import export_query_examples


def _relative_url(path, directory):
    try:
        return quote(os.path.relpath(path, directory).replace(os.sep, "/"), safe="/:")
    except ValueError:
        return Path(path).as_uri()


def write_viewer(report, output_path, source_dir):
    output = Path(output_path).resolve()
    sources = Path(source_dir).resolve()
    if output == sources or output.is_relative_to(sources):
        raise ValueError("Write the viewer outside the preserved source directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    resources = Path(__file__).with_name("viewer_assets")
    resource_output = output.parent / (output.stem + "-assets")
    shutil.copytree(resources, resource_output, dirs_exist_ok=True)
    vendor = Path(__file__).parent.parent / "chunking" / "viewer_assets" / "vendor"
    shutil.copytree(vendor, resource_output / "vendor", dirs_exist_ok=True)
    images = {}
    asset_root = sources / "assets"
    if asset_root.is_dir():
        for image in sorted(asset_root.iterdir()):
            if (image.is_file() and image.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
                    and image.resolve().is_relative_to(sources)):
                images["assets/" + image.name] = _relative_url(image, output.parent)
    pages = {}
    mapping = sources / "source-map.json"
    if mapping.is_file():
        for page in json.loads(mapping.read_text(encoding="utf-8-sig")).get("pages", []):
            number = page.get("pdf_page")
            if type(number) is int and number > 0:
                pages[str(number)] = {"printed_page": page.get("printed_page"),
                                      "image": images.get(page.get("image", ""))}
    for number in range(1, int(report.get("source", {}).get("page_count") or 0) + 1):
        pages.setdefault(str(number), {"printed_page": None, "image": images.get(f"assets/page-{number:02}.png")})
    payload = {"report": report, "fact_examples": export_query_examples(report), "pages": pages, "images": images,
               "pdf": _relative_url(sources / "source.pdf", output.parent) if (sources / "source.pdf").is_file() else None}
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    data = data.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    replacements = {"@@TITLE@@": html.escape(str(report.get("source", {}).get("title", "候选知识审阅"))),
                    "@@RESOURCES@@": html.escape(_relative_url(resource_output, output.parent), quote=True),
                    "@@DATA@@": data}
    template = (resources / "template.html").read_text(encoding="utf-8")
    output.write_text(re.sub(r"@@TITLE@@|@@RESOURCES@@|@@DATA@@", lambda match: replacements[match.group()], template), encoding="utf-8")
