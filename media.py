"""Damaged-file checks for images, audio, video and fonts.

Only the first and last few bytes of each file are read, so this stays fast even
for thousands of files. Finds files that are cut off (incomplete download/copy),
files that are not what their name says (e.g. a web page saved as .png), and
files with no recognisable content.
"""
import os
import struct

import knowledge as K

HEAD, TAIL = 64, 4096


def identify(head):
    """Return the real file type from its first bytes, or None."""
    h = head
    if h.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if h.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if h[:4] == b'RIFF' and h[8:12] == b'WEBP':
        return 'webp'
    if h[:4] == b'RIFF' and h[8:12] == b'WAVE':
        return 'wav'
    if h[:4] == b'RIFF' and h[8:12] == b'AVI ':
        return 'avi'
    if h[:2] == b'BM':
        return 'bmp'
    if h[:4] == b'\x00\x00\x01\x00':
        return 'ico'
    if h[4:8] == b'ftyp':
        brand = h[8:12]
        if brand in (b'avif', b'avis'):
            return 'avif'
        if brand.startswith(b'M4A') or brand == b'M4B ':
            return 'm4a'
        if brand == b'qt  ':
            return 'mov'
        return 'mp4'
    if h[4:8] in (b'moov', b'mdat', b'wide', b'free', b'skip'):
        return 'mp4'
    if h[:4] == b'\x1a\x45\xdf\xa3':
        return 'webm'
    if h[:4] == b'OggS':
        return 'ogg'
    if h[:4] == b'fLaC':
        return 'flac'
    if h[:3] == b'ID3' or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0 and (h[1] & 0x06) != 0):
        return 'mp3'
    if len(h) > 1 and h[0] == 0xFF and (h[1] & 0xF6) == 0xF0:
        return 'aac'
    if h[:4] in (b'\x00\x01\x00\x00', b'true'):
        return 'ttf'
    if h[:4] == b'OTTO':
        return 'otf'
    if h[:4] == b'wOFF':
        return 'woff'
    if h[:4] == b'wOF2':
        return 'woff2'
    text = h.lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    if text.startswith((b'<?xml', b'<svg')) or b'<svg' in h.lower():
        return 'svg'
    if text.startswith((b'<!doctype html', b'<html', b'<head', b'<body')):
        return 'html'
    if text.startswith((b'{', b'[')):
        return 'json'
    return None


# which real types are fine for each extension
COMPATIBLE = {
    'png': {'png'}, 'jpg': {'jpg'}, 'jpeg': {'jpg'}, 'jfif': {'jpg'}, 'gif': {'gif'}, 'webp': {'webp'},
    'bmp': {'bmp'}, 'ico': {'ico', 'png'}, 'svg': {'svg'}, 'avif': {'avif'},
    'mp3': {'mp3'}, 'ogg': {'ogg'}, 'oga': {'ogg'}, 'opus': {'ogg'}, 'wav': {'wav'}, 'flac': {'flac'},
    'm4a': {'m4a', 'mp4'}, 'aac': {'aac', 'm4a', 'mp4'}, 'weba': {'webm'},
    'mp4': {'mp4', 'm4a', 'mov'}, 'm4v': {'mp4', 'mov'}, 'mov': {'mov', 'mp4'}, 'webm': {'webm'}, 'mkv': {'webm'},
    'ogv': {'ogg'}, 'avi': {'avi'},
    'ttf': {'ttf'}, 'otf': {'otf', 'ttf'}, 'woff': {'woff'}, 'woff2': {'woff2'},
}
NAMES = {'png': 'PNG image', 'jpg': 'JPEG image', 'gif': 'GIF image', 'webp': 'WebP image', 'bmp': 'BMP image',
         'ico': 'icon', 'svg': 'SVG image', 'avif': 'AVIF image', 'mp3': 'MP3 sound', 'ogg': 'Ogg sound/video',
         'wav': 'WAV sound', 'flac': 'FLAC sound', 'm4a': 'M4A sound', 'aac': 'AAC sound', 'mp4': 'MP4 video',
         'mov': 'QuickTime video', 'webm': 'WebM video/sound', 'avi': 'AVI video', 'ttf': 'TrueType font',
         'otf': 'OpenType font', 'woff': 'WOFF font', 'woff2': 'WOFF2 font', 'html': 'web page', 'json': 'data file'}


def truncated(kind, head, tail, size):
    """True if the file is clearly cut off."""
    if kind == 'png':
        return b'IEND' not in tail
    if kind == 'jpg':
        return b'\xff\xd9' not in tail
    if kind == 'gif':
        return not tail.rstrip(b'\x00').endswith(b';')
    if kind in ('webp', 'wav', 'avi'):
        declared = struct.unpack('<I', head[4:8])[0] + 8
        return size + 1 < declared
    return False


def check_file(path):
    """Return (problem, detail) or None. problem in: cut, wrong, html, unknown."""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return None  # reported separately as an empty file
        with open(path, 'rb') as f:
            head = f.read(HEAD)
            if size > TAIL:
                f.seek(size - TAIL)
            else:
                f.seek(0)
            tail = f.read(TAIL)
    except OSError as e:
        return ('unreadable', str(e))
    ext = path.rsplit('.', 1)[-1].lower() if '.' in os.path.basename(path) else ''
    kind = identify(head)
    if kind in ('html', 'json'):
        return ('html', kind)
    if kind is None:
        if ext == 'eot':
            return None
        return ('unknown', ext)
    if ext in COMPATIBLE and kind not in COMPATIBLE[ext]:
        if truncated(kind, head, tail, size):
            return ('cut', kind)
        return ('wrong', kind)
    if truncated(kind, head, tail, size):
        if kind in ('png', 'jpg') and size > TAIL and _end_mark_anywhere(path, kind):
            return None   # extra bytes after the end (e.g. a web page glued on by a download site): still shows
        return ('cut', kind)
    return None


def _end_mark_anywhere(path, kind):
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return False
    return (b'IEND' if kind == 'png' else b'\xff\xd9') in data[HEAD:]


def check_files(sc, all_files, referenced):
    """Add issues to scanner `sc` for damaged media in `all_files` [(rel, full)]."""
    from concurrent.futures import ThreadPoolExecutor
    from scanner import ERROR, WARNING, INFO
    wrong = []
    todo = [(rel, full) for rel, full in all_files
            if ('.' in rel.rsplit('/', 1)[-1] and rel.rsplit('.', 1)[-1].lower() in K.MEDIA_EXT)]
    with ThreadPoolExecutor(max_workers=16) as pool:   # reading many small bits of files is faster in parallel
        results = list(pool.map(lambda rf: check_file(rf[1]), todo))
    for (rel, full), r in zip(todo, results):
        ext = rel.rsplit('.', 1)[-1].lower()
        if not r:
            continue
        used = rel.lower() in referenced
        problem, detail = r
        kind = K.asset_kind(ext).lower()
        if problem == 'cut':
            sc.add(ERROR if used else WARNING, 'Broken files', f'Damaged {kind} (cut off): {rel}', line=0, snippet=rel,
                   what=f'The file starts like a {NAMES.get(detail, detail)} but its end is missing - usually an incomplete download, copy or unzip.'
                        + ('' if used else ' The game does not seem to use it, though.'),
                   fix='Copy or download this file again from the original game files.')
        elif problem == 'html':
            sc.add(ERROR if used else WARNING, 'Broken files', f'Not a real {kind}: {rel}', line=0, snippet=rel,
                   what=f'This file is actually a {NAMES[detail]} (often an error page saved by a download site), not a {ext.upper()} file.',
                   fix='Download the real file again and replace this one.')
        elif problem == 'unknown':
            sc.add(WARNING if used else INFO, 'Broken files', f'Unrecognised or damaged {kind}: {rel}', line=0, snippet=rel,
                   what=f'The start of this file does not look like any known {ext.upper()} file, so it is probably damaged.',
                   fix='Open it in an image viewer / media player to check. Replace it if it does not open.')
        elif problem == 'unreadable':
            sc.add(WARNING, 'Broken files', f'File cannot be read: {rel}', line=0, snippet=rel,
                   what=f'Windows could not read this file ({detail}).', fix='Check that the file is not locked or damaged.')
        elif problem == 'wrong':
            wrong.append((rel, ext, detail))
    if wrong:
        sc.add(INFO, 'Broken files', f'{len(wrong)} file(s) have the wrong file extension', line=0,
               snippet=', '.join(r.rsplit('/', 1)[-1] for r, _, _ in wrong[:8]) + (' …' if len(wrong) > 8 else ''),
               what='These files contain a different format than their name says (for example a JPEG picture named .png). '
                    'Browsers usually still show them, but some programs, phones and websites may not.',
               fix='Optional: rename or convert them so the extension matches the real format. Files:\n'
                   + '\n'.join(f'  {r}  (really a {NAMES.get(k, k)})' for r, _, k in wrong))
