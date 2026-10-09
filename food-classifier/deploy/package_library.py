"""Package only the classifier's referenced standards and required indexes.

No reports, PRD, query databases, original workspace documents or arbitrary files.
The archive is an external deployment artifact, never a source-control input.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
sys.path.insert(0, str(APP))
from engine import Library


def files_to_package(library):
    lib = library.lib
    candidates = [lib / '索引' / name for name in (
        '食品项目标准方法关联.json', '标准总目录.json', '标准逐层引用关系.json')]
    candidates += sorted((lib / '分类体系').glob('*.json'))
    candidates += [APP / 'resources/guide-pages.json']
    originals = sorted({f['path'].resolve() for f in library.files.values()})
    allowed_root_originals = {
        'GB 2762-2022 食品安全国家标准 食品中污染物限量.pdf',
        'GB 5009.12-2023 食品安全国家标准 食品中铅的测定.pdf',
        'GB 2763-2026.pdf',
        '国家食品安全抽检实施细则（2026年版 上册）.pdf',
    }
    for path in originals:
        in_library = any(path.is_relative_to(lib / folder) for folder in ['标准原文', '待核验原文'])
        root_standard = path.parent == ROOT / 'references' and path.name in allowed_root_originals
        if not (in_library or root_standard) or path.suffix.lower() not in {'.pdf', '.doc', '.docx'}:
            raise ValueError(f'Unexpected document outside the standards allowlist: {path}')
    return sorted(set(candidates + originals))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.suffixes[-2:] != ['.tar', '.gz']:
        parser.error('Output must end in .tar.gz')
    if output.is_relative_to(ROOT / 'references'):
        parser.error('Write deployment artifacts outside the source reference library')
    library = Library()
    paths = files_to_package(library)
    manifest = {
        'data_fingerprint': library.fingerprint,
        'purpose': 'Only classification reference data; no query history, reports or internal requirements.',
        'files': [],
    }
    for path in paths:
        digest = hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()
        manifest['files'].append({'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest})
    manifest['total_bytes'] = sum(f['bytes'] for f in manifest['files'])
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = output.with_name(output.name.removesuffix('.tar.gz') + '-manifest.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    if not args.manifest_only:
        if output.exists():
            parser.error(f'Archive already exists: {output}')
        with tarfile.open(output, 'w:gz', compresslevel=1, format=tarfile.PAX_FORMAT) as archive:
            for path, entry in zip(paths, manifest['files']):
                # All entries are regular files; do not include symlinks, permissions or owners.
                info = tarfile.TarInfo(entry['path'])
                info.size = entry['bytes']
                info.mode = 0o644
                with path.open('rb') as source:
                    archive.addfile(info, source)
            archive.add(manifest_path, arcname='library-manifest.json', recursive=False)
    print(json.dumps({'files': len(paths), 'bytes': manifest['total_bytes'], 'archive': None if args.manifest_only else str(output), 'manifest': str(manifest_path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
