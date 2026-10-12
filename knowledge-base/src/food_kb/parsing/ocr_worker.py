"""One OCR page per process so a timeout can terminate native recognition."""
import json
import sys

import pymupdf


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    try:
        request = json.load(sys.stdin)
        with pymupdf.open(request['path']) as document:
            page = document[request['page'] - 1]
            page.set_rotation(0)
            textpage = page.get_textpage_ocr(language=request['language'], dpi=request['dpi'],
                                             full=True, tessdata=request['tessdata'])
            result = page.get_text('dict', textpage=textpage, sort=True)
            result['blocks'] = [block for block in result['blocks'] if block['type'] == 0]
            print(json.dumps(result, ensure_ascii=False))
    except Exception:
        # Native errors can contain filesystem names or source content. The caller
        # stores a safe code and the page image, rather than exposing stderr.
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
