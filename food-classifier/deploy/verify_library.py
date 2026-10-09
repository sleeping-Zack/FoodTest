"""Verify an unpacked deployment library against its file hashes."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    manifest = json.loads((root / 'library-manifest.json').read_text(encoding='utf8'))
    failures = []
    for entry in manifest['files']:
        path = (root / entry['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != entry['bytes']:
            failures.append(entry['path'])
            continue
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != entry['sha256']:
            failures.append(entry['path'])
    print(json.dumps({'files': len(manifest['files']), 'failed': failures}, ensure_ascii=False))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
