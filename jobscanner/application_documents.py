"""The documents that belong to one application - kept exactly as they were sent.

This is deliberately *not* a link table into ``documents``.  The document store
holds the current primary CV and the current cover letter: living files that are
replaced whenever a better version is written.  An application, on the other
hand, is a historical record of what was actually sent to one company on one
day, and that record has to stay true after the master CV moves on.

So attaching a document **copies the bytes** into::

    documents/applications/<application-id>/<kind>__<filename>

and records the metadata next to the copy.  Nothing in this module ever reads
the document store again afterwards: ``source_document_id`` is provenance, not a
pointer, and it carries no foreign key precisely so that replacing - or deleting
- the primary CV cannot reach back into an application that has already been
sent.

The stored path is workspace-relative (``documents/applications/6/cv__x.pdf``),
like every other path in this codebase, so the workspace stays portable.  It is
never handed to the browser: the frontend addresses a document by its row id and
:func:`resolve` is the only thing that turns an id into a file, under the
applications directory and nowhere else.
"""

import hashlib
import shutil
from pathlib import Path

from . import documents as documents_mod
from .db import connect, now_iso, row_to_dict, workspace_root

#: The kinds an application document can have, in display order.
#: One vocabulary, used by the API, the UI and the store-kind mapping below.
KINDS = (
    ('cv', 'CV / Resume'),
    ('cover_letter', 'Cover Letter'),
    ('additional', 'Additional Document'),
    ('certificate', 'Certificate / Reference'),
    ('other', 'Other'),
)
KIND_LABELS = dict(KINDS)
DEFAULT_KIND = 'other'

#: Where a document came from.  ``DOCUMENT_STORE`` was picked from the Profile
#: document store, ``UPLOADED_FOR_APPLICATION`` was uploaded straight onto this
#: application, ``APPLY_ASSISTANT`` was the file the assistant handed to the
#: company's own form.  All three end up as the same immutable copy; the source
#: only records how it got here.
SOURCES = ('DOCUMENT_STORE', 'UPLOADED_FOR_APPLICATION', 'APPLY_ASSISTANT')
DEFAULT_SOURCE = 'DOCUMENT_STORE'

#: Document-store kind -> application document kind.
STORE_KIND_MAP = {
    'cv_de': 'cv', 'cv_en': 'cv',
    'motivation_de': 'cover_letter', 'motivation_en': 'cover_letter',
    'certificate': 'certificate', 'reference': 'certificate',
    'interview_de': 'additional', 'interview_en': 'additional',
    'cheat_sheet': 'additional', 'other': 'other',
}

#: What may be uploaded directly onto an application.  Deliberately narrower
#: than the document store: an application package is a PDF or a Word file, and
#: accepting an image here would only produce an attachment no employer wants.
ALLOWED_SUFFIXES = ('.pdf', '.docx')
MAX_BYTES = 25 * 1024 * 1024


class ApplicationDocumentError(ValueError):
    pass


def canonical_kind(value):
    """Any spelling of a kind -> one of :data:`KINDS`, never an invented one."""
    text = str(value or '').strip().lower().replace('-', '_').replace(' ', '_')
    if text in KIND_LABELS:
        return text
    if text in STORE_KIND_MAP:
        return STORE_KIND_MAP[text]
    if text in ('resume', 'cv_resume'):
        return 'cv'
    if text in ('motivation', 'motivation_letter', 'cover', 'coverletter'):
        return 'cover_letter'
    if text == 'reference':
        return 'certificate'
    return DEFAULT_KIND


def canonical_source(value):
    text = str(value or '').strip().upper()
    return text if text in SOURCES else DEFAULT_SOURCE


def applications_root():
    """``documents/applications`` under whatever workspace root is active."""
    return documents_mod.documents_dir() / 'applications'


def application_dir(application_id):
    return applications_root() / str(int(application_id))


def _relative(path):
    try:
        return str(Path(path).relative_to(workspace_root()))
    except ValueError:
        return str(path)


def _copy_into(application_id, kind, filename, data=None, source_path=None):
    """Write the immutable copy and return (absolute path, size, sha256)."""
    name = documents_mod.safe_filename(filename)
    target_dir = application_dir(application_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem, suffix = Path(name).stem, Path(name).suffix
    target = target_dir / '{0}__{1}'.format(kind, name)
    counter = 1
    while target.exists():
        target = target_dir / '{0}__{1}-{2}{3}'.format(kind, stem, counter, suffix)
        counter += 1
    if data is None:
        shutil.copyfile(str(source_path), str(target))
        data = target.read_bytes()
    else:
        target.write_bytes(data)
    return target, len(data), hashlib.sha256(data).hexdigest()


def _decorate(row):
    """What the API returns.  No absolute path ever leaves this function."""
    if not row:
        return None
    data = dict(row)
    data['kind_label'] = KIND_LABELS.get(data.get('document_kind'), 'Other')
    data['exists'] = bool(resolve(data))
    data.pop('stored_path', None)
    return data


# -- reading ---------------------------------------------------------------
def list_for(application_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            'SELECT * FROM application_documents WHERE application_id=? '
            'ORDER BY id', (application_id,)).fetchall()
        return [_decorate(row_to_dict(r)) for r in rows]
    finally:
        if owns:
            conn.close()


def list_many(application_ids, conn=None):
    """``{application_id: [document, ...]}`` for a whole list of applications."""
    ids = [int(i) for i in application_ids]
    out = {i: [] for i in ids}
    if not ids:
        return out
    owns = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            'SELECT * FROM application_documents WHERE application_id IN ({0}) '
            'ORDER BY application_id, id'.format(','.join('?' for _ in ids)), ids).fetchall()
        for row in rows:
            out[row['application_id']].append(_decorate(row_to_dict(row)))
        return out
    finally:
        if owns:
            conn.close()


def get(application_id, link_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        return row_to_dict(conn.execute(
            'SELECT * FROM application_documents WHERE id=? AND application_id=?',
            (link_id, application_id)).fetchone())
    finally:
        if owns:
            conn.close()


def resolve(row):
    """The row's file on this machine, or ``None``.

    The one place a stored path becomes a filesystem path, and it refuses
    anything that does not resolve to a real file inside
    ``documents/applications/``.  A row is only ever written by this module, so
    this is a belt-and-braces check - but it is the check that makes ``..`` in a
    stored path, however it got there, unreadable rather than served.
    """
    stored = str((row or {}).get('stored_path') or '').strip()
    if not stored:
        return None
    candidate = Path(stored)
    if not candidate.is_absolute():
        candidate = workspace_root() / candidate
    try:
        candidate = candidate.resolve()
        root = applications_root().resolve()
    except OSError:
        return None
    if not (candidate == root or root in candidate.parents):
        return None
    return candidate if candidate.is_file() else None


def summary(documents):
    """``[{kind, kind_label, count, filename}]`` for the kinds that are there.

    What the collapsed card and the Preparation block say: whether the package
    is ready.  Never invents a kind that has no file.
    """
    out = []
    for kind, label in KINDS:
        files = [d for d in documents if d.get('document_kind') == kind]
        if files:
            out.append({'kind': kind, 'kind_label': label, 'count': len(files),
                        'filename': files[0].get('original_filename', '')})
    return out


# -- writing ---------------------------------------------------------------
def _insert(conn, application_id, kind, label, original_filename, target,
            size_bytes, digest, mime_type, source, source_document_id, notes,
            role='', created_at=''):
    cursor = conn.execute(
        '''INSERT INTO application_documents
             (application_id, document_kind, label, original_filename, stored_path,
              mime_type, size_bytes, content_hash, source, source_document_id,
              role, notes, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (application_id, kind, label, original_filename,
         _relative(target) if target else '', mime_type, size_bytes, digest,
         source, source_document_id, role, notes, created_at or now_iso()))
    return cursor.lastrowid


def attach_stored(application_id, document_id, kind='', label='', conn=None,
                  source=DEFAULT_SOURCE, notes=''):
    """Copy a document-store file onto this application, as it is right now.

    The copy is the point.  After this call the application owns its own bytes,
    so setting a new primary CV - or deleting the old one - changes nothing
    here.
    """
    owns = conn is None
    conn = conn or connect()
    try:
        if conn.execute('SELECT 1 FROM applications WHERE id=?',
                        (application_id,)).fetchone() is None:
            raise ApplicationDocumentError('Application not found.')
        document = documents_mod.get(document_id, conn)
        if document is None:
            raise ApplicationDocumentError('Document not found.')
        source_path = Path(document['absolute_path'])
        if not source_path.is_file():
            raise ApplicationDocumentError(
                'The stored file is missing on disk: {0}'.format(document['filename']))
        resolved_kind = canonical_kind(kind or document['kind'])
        # The name the file had for the user, not the ``<kind>__`` name the
        # document store gave it on disk.
        original = documents_mod.original_name(document['filename'])
        target, size, digest = _copy_into(application_id, resolved_kind, original,
                                          source_path=source_path)
        link_id = _insert(conn, application_id, resolved_kind,
                          label or document.get('label') or '', original,
                          target, size, digest, document.get('mime_type') or '',
                          canonical_source(source), document_id, notes)
        conn.commit()
        return _decorate(get(application_id, link_id, conn))
    finally:
        if owns:
            conn.close()


def attach_upload(application_id, kind, filename, data, label='', conn=None,
                  source='UPLOADED_FOR_APPLICATION', notes=''):
    """Store a PDF or DOCX that was uploaded straight onto this application."""
    owns = conn is None
    conn = conn or connect()
    try:
        if conn.execute('SELECT 1 FROM applications WHERE id=?',
                        (application_id,)).fetchone() is None:
            raise ApplicationDocumentError('Application not found.')
        name = documents_mod.safe_filename(filename)
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ApplicationDocumentError(
                'Only PDF and DOCX files can be attached to an application '
                '({0} was offered).'.format(suffix or '(no extension)'))
        if not data:
            raise ApplicationDocumentError('File is empty.')
        if len(data) > MAX_BYTES:
            raise ApplicationDocumentError('File is larger than 25 MB.')
        resolved_kind = canonical_kind(kind)
        target, size, digest = _copy_into(application_id, resolved_kind, filename, data=data)
        link_id = _insert(conn, application_id, resolved_kind, label, name, target,
                          size, digest, documents_mod.mime_for(suffix),
                          canonical_source(source), None, notes)
        conn.commit()
        return _decorate(get(application_id, link_id, conn))
    finally:
        if owns:
            conn.close()


def remove(application_id, link_id, conn=None):
    """Drop the association and move the copy into the local trash.

    Only ever reached because the user asked for it on this one application.
    The file is moved, not shredded, exactly like a replaced profile document.
    """
    owns = conn is None
    conn = conn or connect()
    try:
        row = get(application_id, link_id, conn)
        if row is None:
            return False
        conn.execute('DELETE FROM application_documents WHERE id=? AND application_id=?',
                     (link_id, application_id))
        conn.commit()
        path = resolve(row)
        if path:
            trash = documents_mod.documents_dir() / '_removed'
            trash.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(trash / '{0}-{1}'.format(
                now_iso().replace(':', ''), path.name)))
        return True
    finally:
        if owns:
            conn.close()


def adopt_legacy_link(conn, application_id, document_id, role='', created_at=''):
    """Turn one V1 pointer row into a snapshot.  Used by the migration only.

    Never raises and never commits: a migration that stops halfway because one
    CV was moved out of the folder last month would be worse than the pointer
    it is replacing.  A document whose file is gone keeps its association, its
    kind and its filename, and is recorded with no stored file - which is what
    the UI then reports.  Nothing is invented.
    """
    document = documents_mod.get(document_id, conn)
    if document is None:
        return None
    kind = canonical_kind(document['kind'])
    original = documents_mod.original_name(document['filename'])
    source_path = Path(document['absolute_path'])
    target, size, digest, notes = None, 0, '', ''
    if source_path.is_file():
        target, size, digest = _copy_into(application_id, kind, original,
                                          source_path=source_path)
    else:
        notes = 'The document store file was already missing when this record was migrated.'
    return _insert(conn, application_id, kind, document.get('label') or '',
                   original, target, size, digest,
                   document.get('mime_type') or '', DEFAULT_SOURCE, document_id,
                   notes, role=role, created_at=created_at)


def has_stored_document(application_id, document_id, source='', conn=None):
    """Was this exact document-store file already snapshotted here?"""
    owns = conn is None
    conn = conn or connect()
    try:
        sql = ('SELECT 1 FROM application_documents WHERE application_id=? '
               'AND source_document_id=?')
        params = [application_id, document_id]
        if source:
            sql += ' AND source=?'
            params.append(canonical_source(source))
        return conn.execute(sql, params).fetchone() is not None
    finally:
        if owns:
            conn.close()
