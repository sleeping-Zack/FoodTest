"""Local, page-isolated OCR. No download or network call is made here."""
import json
from pathlib import Path
import re
import subprocess
import sys

import pymupdf

from ..common import KBError, file_hash


def settings(options):
    mode = options.get('ocr', 'auto')
    dpi = options.get('dpi', 200)
    timeout = options.get('timeout_seconds', 60)
    language = options.get('language', 'chi_sim+eng')
    if mode not in {'off', 'auto', 'force'}:
        raise KBError('OCR_CONFIG', 'OCR mode must be off, auto or force.')
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise KBError('OCR_CONFIG', 'OCR dpi must be an integer from 72 to 600.')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
        raise KBError('OCR_CONFIG', 'OCR timeout must be greater than zero and at most 600 seconds.')
    if not isinstance(language, str) or not re.fullmatch(r'[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*', language):
        raise KBError('OCR_CONFIG', 'Invalid OCR language identifiers.')
    return {'ocr': mode, 'dpi': dpi, 'language': language, 'timeout_seconds': timeout}


def language_data(options):
    config = settings(options)
    if config['ocr'] == 'off':
        return None, {}
    try:
        folder = Path(pymupdf.get_tessdata(options.get('tessdata'))).resolve()
    except Exception as exc:
        raise KBError('OCR_UNAVAILABLE', 'Local OCR language data is not configured.') from exc
    hashes = {}
    for language in config['language'].split('+'):
        path = folder / f'{language}.traineddata'
        if not path.is_file():
            raise KBError('OCR_UNAVAILABLE', f'Local OCR language data is missing: {language}.')
        hashes[language] = file_hash(path)
    return folder, hashes


def describe_engine(options):
    config = settings(options)
    engine = {'adapter': 'food-kb-pdf-1', 'pymupdf': pymupdf.VersionBind,
              'mupdf': pymupdf.VersionFitz, 'options': config,
              'ocr_provider': 'pymupdf-bundled-tesseract', 'language_sha256': {}}
    if config['ocr'] != 'off':
        try:
            _, engine['language_sha256'] = language_data(options)
            engine['ocr_available'] = True
        except KBError:
            engine['ocr_available'] = False
        # The binding does not expose a separate Tesseract version. Identify its
        # actual binary as well as the containing MuPDF release instead of guessing.
        binary = Path(pymupdf.__file__).parent / 'mupdfcpp64.dll'
        engine['tesseract_version'] = None
        if binary.is_file():
            engine['ocr_binary_sha256'] = file_hash(binary)
    return engine


def recognize_page(path, page_number, options):
    config = settings(options)
    if config['ocr'] == 'off':
        raise KBError('OCR_DISABLED', 'Local OCR is disabled.')
    tessdata, _ = language_data(options)
    request = {'path': str(Path(path).resolve()), 'page': page_number,
               'tessdata': str(tessdata), 'dpi': config['dpi'], 'language': config['language']}
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        result = subprocess.run([sys.executable, '-m', 'food_kb.parsing.ocr_worker'],
                                input=json.dumps(request), capture_output=True, text=True,
                                encoding='utf-8', timeout=config['timeout_seconds'], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise KBError('OCR_TIMEOUT', 'Local OCR exceeded its per-page timeout; the worker was stopped.') from exc
    if result.returncode:
        raise KBError('OCR_FAILED', 'Local OCR worker failed; retry this page or review its preserved image.')
    try:
        payload = json.loads(result.stdout)
        if not isinstance(payload.get('blocks'), list):
            raise ValueError('Missing blocks')
    except (ValueError, AttributeError) as exc:
        raise KBError('OCR_INVALID_OUTPUT', 'Local OCR returned an invalid page result.') from exc
    return payload
