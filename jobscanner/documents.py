"""Document store: files live on disk, SQLite only keeps metadata.

No blob ever reaches the database.  ``documents/`` is git-ignored so a CV or a
reference letter cannot be committed by accident.

Paths are stored **relative to the workspace root** (``documents/cv/x.pdf``)
and resolved at runtime.  That is what makes the workspace portable: the same
database works after the folder is moved to another machine, another user
account or another checkout.
"""

import re
import shutil
import unicodedata
from pathlib import Path

from .db import connect, now_iso, portable_document_path, row_to_dict, workspace_root


def documents_dir():
    """``documents/`` under whatever workspace root is currently active."""
    return workspace_root() / 'documents'


def resolve(path):
    """A stored (relative) document path -> the absolute file on this machine."""
    text = str(path or '').strip()
    if not text:
        return workspace_root()
    candidate = Path(text)
    return candidate if candidate.is_absolute() else workspace_root() / candidate


#: kind -> (subdirectory, human label, language)
KINDS = {
    'cv_de': ('cv', 'CV (German)', 'de'),
    'cv_en': ('cv', 'CV (English)', 'en'),
    'motivation_de': ('motivation', 'Motivation letter (German)', 'de'),
    'motivation_en': ('motivation', 'Motivation letter (English)', 'en'),
    'interview_de': ('interview', 'Interview guide (German)', 'de'),
    'interview_en': ('interview', 'Interview guide (English)', 'en'),
    'cheat_sheet': ('reference', 'Application cheat sheet', ''),
    'certificate': ('certificates', 'Certificate', ''),
    'reference': ('references', 'Employment reference', ''),
    'other': ('other', 'Other document', ''),
}
#: Kinds that only ever hold one current file.
SINGLE_KINDS = ('cv_de', 'cv_en', 'motivation_de', 'motivation_en')

#: The only kinds the apply assistant may ever attach to a job application.
#: Everything else - interview guides, the cheat sheet, certificates and
#: employment references - is private preparation material: it is stored,
#: listed and exported like any other document but is never offered to a form.
UPLOADABLE_KINDS = ('cv_de', 'cv_en', 'motivation_de', 'motivation_en')


def upload_allowed(kind):
    """May a document of this kind ever be attached to an application?"""
    return kind in UPLOADABLE_KINDS


ALLOWED_SUFFIXES = {'.pdf', '.doc', '.docx', '.odt', '.rtf', '.txt', '.md',
                    '.png', '.jpg', '.jpeg'}
MAX_BYTES = 25 * 1024 * 1024


class DocumentError(ValueError):
    pass


def ensure_dirs():
    for subdir, _, _ in KINDS.values():
        (documents_dir() / subdir).mkdir(parents=True, exist_ok=True)
    return documents_dir()


def safe_filename(name):
    text = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode()
    text = re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-.') or 'document'
    return text[:120]


def store(kind, filename, data, label='', notes='', conn=None):
    """Write the upload to disk and record its metadata."""
    if kind not in KINDS:
        raise DocumentError('Unknown document kind: {0}'.format(kind))
    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise DocumentError('File type {0} is not accepted.'.format(suffix or '(none)'))
    if len(data) > MAX_BYTES:
        raise DocumentError('File is larger than 25 MB.')
    if not data:
        raise DocumentError('File is empty.')

    subdir, default_label, language = KINDS[kind]
    ensure_dirs()
    target_dir = documents_dir() / subdir
    target = target_dir / '{0}__{1}'.format(kind, name)
    counter = 1
    while target.exists():
        target = target_dir / '{0}__{1}-{2}{3}'.format(kind, Path(name).stem, counter, suffix)
        counter += 1
    target.write_bytes(data)

    owns = conn is None
    conn = conn or connect()
    try:
        ts = now_iso()
        if kind in SINGLE_KINDS:
            conn.execute('UPDATE documents SET is_primary=0 WHERE kind=?', (kind,))
        cursor = conn.execute(
            '''INSERT INTO documents (kind, label, language, filename, path, mime_type,
                                      size_bytes, is_primary, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            (kind, label or default_label, language, target.name,
             _relative_path(target), _mime_for(suffix), len(data),
             1 if kind in SINGLE_KINDS else 0, notes, ts, ts))
        conn.commit()
        return get(cursor.lastrowid, conn)
    finally:
        if owns:
            conn.close()


def _relative_path(target):
    """Store ``documents/cv/x.pdf``, never an absolute machine-specific path."""
    try:
        return str(target.relative_to(workspace_root()))
    except ValueError:
        return portable_document_path(target) or str(target)


def _mime_for(suffix):
    return {
        '.pdf': 'application/pdf',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.odt': 'application/vnd.oasis.opendocument.text',
        '.rtf': 'application/rtf', '.txt': 'text/plain', '.md': 'text/markdown',
        '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    }.get(suffix, 'application/octet-stream')


def _decorate(data):
    if not data:
        return None
    path = resolve(data['path'])
    data['exists'] = path.exists()
    data['absolute_path'] = str(path)
    data['kind_label'] = KINDS.get(data['kind'], ('', data['kind'], ''))[1]
    data['upload_allowed'] = upload_allowed(data['kind'])
    return data


def get(document_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        return _decorate(row_to_dict(conn.execute(
            'SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()))
    finally:
        if owns:
            conn.close()


def list_documents(conn=None, kind=''):
    owns = conn is None
    conn = conn or connect()
    try:
        sql = 'SELECT * FROM documents'
        params = ()
        if kind:
            sql += ' WHERE kind=?'
            params = (kind,)
        sql += ' ORDER BY kind, is_primary DESC, created_at DESC'
        return [_decorate(row_to_dict(r)) for r in conn.execute(sql, params).fetchall()]
    finally:
        if owns:
            conn.close()


def primary(kind, conn=None):
    """The file the apply assistant should use for this kind, if any."""
    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            'SELECT * FROM documents WHERE kind=? ORDER BY is_primary DESC, created_at DESC LIMIT 1',
            (kind,)).fetchone()
        document = _decorate(row_to_dict(row))
        return document if document and document['exists'] else None
    finally:
        if owns:
            conn.close()


def set_primary(document_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT kind FROM documents WHERE id=?', (document_id,)).fetchone()
        if row is None:
            raise DocumentError('Document not found.')
        conn.execute('UPDATE documents SET is_primary=0 WHERE kind=?', (row['kind'],))
        conn.execute('UPDATE documents SET is_primary=1, updated_at=? WHERE id=?',
                     (now_iso(), document_id))
        conn.commit()
        return get(document_id, conn)
    finally:
        if owns:
            conn.close()


def delete(document_id, remove_file=True, conn=None):
    """Remove the metadata row; the file itself is moved aside, not shredded."""
    owns = conn is None
    conn = conn or connect()
    try:
        document = get(document_id, conn)
        if document is None:
            return False
        conn.execute('DELETE FROM documents WHERE id=?', (document_id,))
        conn.commit()
        if remove_file:
            path = Path(document['absolute_path'])
            if path.exists():
                trash = documents_dir() / '_removed'
                trash.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(trash / '{0}-{1}'.format(
                    now_iso().replace(':', ''), path.name)))
        return True
    finally:
        if owns:
            conn.close()


#: The categories the Config / Profile screen always lists, in display order.
#: A category with no file is reported as "Not configured" - it is never
#: invented, and no placeholder file is ever written to disk.
CATEGORY_ORDER = ('cv_de', 'cv_en', 'motivation_de', 'motivation_en',
                  'interview_de', 'interview_en', 'cheat_sheet',
                  'reference', 'certificate', 'other')


def categories(conn=None):
    """Every document category with its current status.

    ``configured`` means a metadata row exists *and* the file is really on
    disk; a row whose file has gone missing is reported as ``Missing file`` so
    a broken document is never silently treated as available.
    """
    owns = conn is None
    conn = conn or connect()
    try:
        stored = list_documents(conn)
        out = []
        for kind in CATEGORY_ORDER:
            subdir, label, language = KINDS[kind]
            files = [d for d in stored if d['kind'] == kind]
            present = [d for d in files if d['exists']]
            if present:
                status = 'Configured'
            elif files:
                status = 'Missing file'
            else:
                status = 'Not configured'
            out.append({
                'kind': kind,
                'label': label,
                'language': language,
                'directory': 'documents/{0}'.format(subdir),
                'status': status,
                'configured': bool(present),
                'count': len(files),
                'files': [d['filename'] for d in files],
                'upload_allowed': upload_allowed(kind),
            })
        return out
    finally:
        if owns:
            conn.close()
