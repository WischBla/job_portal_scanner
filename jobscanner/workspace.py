"""Portable workspace: export and import everything the user owns.

The workspace is exactly two things:

``data/app.db``
    profile, search settings, geography, target roles, matching, compensation,
    company watchlist, job sources, discovered jobs, applications, the
    application timeline, personal feedback and document *metadata*.

``documents/``
    the CVs, motivation letters, references and certificates themselves.

Nothing else travels.  ``.env``, API keys, virtual environments, caches, the
git checkout and the Playwright profile are not part of the workspace and are
never written into an archive - the export collects the two paths above by
name rather than sweeping a directory, so a secret cannot be picked up by
accident.  Credentials are configured again on the destination machine.

Portability rests on one rule enforced elsewhere in the package: document
paths are stored relative to the workspace root (``documents/cv/x.pdf``) and
resolved at runtime.  That is what lets a database written under
``/Users/a/scanner`` work unchanged under ``/home/b/job_portal_scanner``.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from . import db as jsdb

#: Bump only on a breaking change to the archive layout.
EXPORT_FORMAT_VERSION = 1
#: Older layouts this build still understands.
SUPPORTED_FORMAT_VERSIONS = (1,)

MANIFEST_NAME = 'manifest.json'
DB_MEMBER = 'data/app.db'
DOCUMENTS_PREFIX = 'documents/'
ARCHIVE_PREFIX = 'job-assistant-workspace'

#: Tables the archive must contain to be a workspace at all.
REQUIRED_TABLES = ('search_profile', 'person_profile', 'applications',
                   'discovered_jobs', 'company_watchlist', 'job_sources', 'documents')

#: Document subdirectories that are user content.  ``_removed`` is the local
#: trash of replaced files and is deliberately left behind.
SKIPPED_DOCUMENT_DIRS = {'_removed', '__pycache__'}
SKIPPED_DOCUMENT_FILES = {'.DS_Store', '.gitkeep'}

PRIVACY_NOTICE = (
    'This workspace export contains your personal profile, application history '
    'and documents. Store and transfer it securely.'
)


class WorkspaceError(RuntimeError):
    """Anything that makes an export or an import refuse to continue."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _sha256(path):
    digest = hashlib.sha256()
    with open(str(path), 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(root):
    """The checkout's commit, when there is one.  Informational only."""
    try:
        result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                                cwd=str(root), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout.strip() if result.returncode == 0 else ''


def _schema_version_of(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return int(row[0]) if row and str(row[0]).isdigit() else 0
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _consistent_copy(source, target):
    """Copy a live SQLite file safely, falling back to a plain file copy."""
    try:
        src = sqlite3.connect(str(source))
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except sqlite3.Error:
        shutil.copy2(str(source), str(target))
    return target


def _document_files(documents_root):
    """Every real user document, as (relative posix path, absolute path)."""
    out = []
    if not documents_root.exists():
        return out
    for path in sorted(documents_root.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(documents_root)
        if set(relative.parts[:-1]) & SKIPPED_DOCUMENT_DIRS:
            continue
        if relative.name in SKIPPED_DOCUMENT_FILES:
            continue
        out.append(('{0}{1}'.format(DOCUMENTS_PREFIX, relative.as_posix()), path))
    return out


def _counts(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        def one(table):
            try:
                return conn.execute('SELECT COUNT(*) FROM {0}'.format(table)).fetchone()[0]
            except sqlite3.Error:
                return 0
        return {table: one(table) for table in
                ('discovered_jobs', 'applications', 'company_watchlist',
                 'job_sources', 'documents', 'events')}
    finally:
        conn.close()


def default_archive_name(now=None):
    stamp = (now or datetime.now()).strftime('%Y-%m-%d')
    return '{0}-{1}.zip'.format(ARCHIVE_PREFIX, stamp)


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------
def export_workspace(destination=None, root=None, db_path=None, checksums=True):
    """Write the portable archive and return a short report.

    ``destination`` may be a file or a directory; a directory gets a dated
    default name.  Only ``data/app.db`` and ``documents/`` are collected, by
    name - never by sweeping the project folder - so no secret, cache or
    virtual environment can be swept in with them.
    """
    root = Path(root) if root else jsdb.workspace_root()
    db_path = Path(db_path) if db_path else jsdb.get_db_path()
    if not db_path.exists():
        raise WorkspaceError('There is no database at {0} to export.'.format(db_path))

    destination = Path(destination) if destination else Path.cwd()
    if destination.is_dir() or str(destination).endswith(os.sep):
        destination = destination / default_archive_name()
    if destination.suffix.lower() != '.zip':
        destination = destination.with_suffix('.zip')
    destination.parent.mkdir(parents=True, exist_ok=True)

    documents = _document_files(root / 'documents')
    staging = Path(tempfile.mkdtemp(prefix='workspace-export-'))
    try:
        snapshot = _consistent_copy(db_path, staging / 'app.db')
        manifest = {
            'format_version': EXPORT_FORMAT_VERSION,
            'schema_version': _schema_version_of(snapshot),
            'exported_at': datetime.now().replace(microsecond=0).isoformat(),
            'application': 'Job Assistant',
            'application_version': '2.0',
            'git_revision': _git_revision(root),
            'privacy_notice': PRIVACY_NOTICE,
            'database': {
                'path': DB_MEMBER,
                'size_bytes': snapshot.stat().st_size,
                'sha256': _sha256(snapshot) if checksums else '',
            },
            'documents': [
                {'path': member, 'size_bytes': path.stat().st_size,
                 'sha256': _sha256(path) if checksums else ''}
                for member, path in documents
            ],
            'counts': _counts(snapshot),
        }
        # A partially written archive must never look like a finished one, so
        # the file only appears at its final name once it is complete.
        temporary = destination.with_name(destination.name + '.part')
        with zipfile.ZipFile(str(temporary), 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
            archive.write(str(snapshot), DB_MEMBER)
            for member, path in documents:
                archive.write(str(path), member)
        os.replace(str(temporary), str(destination))
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)

    return {
        'archive': str(destination),
        'size_bytes': destination.stat().st_size,
        'manifest': manifest,
        'document_count': len(documents),
        'privacy_notice': PRIVACY_NOTICE,
    }


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------
def _safe_members(archive):
    """Reject anything that is not part of a workspace, before extracting it.

    Absolute paths, ``..`` segments and members outside ``data/app.db`` /
    ``documents/`` are refused rather than skipped: an archive containing them
    is not a workspace export and should not be trusted at all.
    """
    members = []
    for info in archive.infolist():
        name = info.filename.replace('\\', '/')
        if name.endswith('/'):
            continue
        if name.startswith('/') or '..' in name.split('/'):
            raise WorkspaceError('Archive contains an unsafe path: {0}'.format(name))
        if name == MANIFEST_NAME or name == DB_MEMBER or name.startswith(DOCUMENTS_PREFIX):
            members.append(name)
            continue
        raise WorkspaceError('Archive contains an unexpected entry: {0}'.format(name))
    return members


def inspect_archive(archive_path):
    """Validate an archive and report what it holds, changing nothing."""
    path = Path(archive_path)
    if not path.exists():
        raise WorkspaceError('Archive not found: {0}'.format(path))
    if not zipfile.is_zipfile(str(path)):
        raise WorkspaceError('{0} is not a zip archive.'.format(path.name))

    with zipfile.ZipFile(str(path)) as archive:
        members = _safe_members(archive)
        if MANIFEST_NAME not in members:
            raise WorkspaceError('Archive has no {0}; it is not a workspace export.'
                                 .format(MANIFEST_NAME))
        if DB_MEMBER not in members:
            raise WorkspaceError('Archive has no {0}.'.format(DB_MEMBER))
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise WorkspaceError('{0} is not readable JSON.'.format(MANIFEST_NAME))

    if not isinstance(manifest, dict):
        raise WorkspaceError('{0} does not describe a workspace.'.format(MANIFEST_NAME))
    version = manifest.get('format_version')
    if version not in SUPPORTED_FORMAT_VERSIONS:
        raise WorkspaceError(
            'Unsupported export format version {0}. This application understands {1}.'
            .format(version, ', '.join(str(v) for v in SUPPORTED_FORMAT_VERSIONS)))

    schema = int(manifest.get('schema_version') or 0)
    if schema > jsdb.SCHEMA_VERSION:
        raise WorkspaceError(
            'This workspace was written by a newer version of the application '
            '(database schema {0}, this build supports {1}). Update the application '
            'first - a newer database is never downgraded.'
            .format(schema, jsdb.SCHEMA_VERSION))
    return {
        'manifest': manifest,
        'members': members,
        'documents': [m for m in members if m.startswith(DOCUMENTS_PREFIX)],
        'schema_version': schema,
        'needs_migration': schema < jsdb.SCHEMA_VERSION,
    }


def _validate_database(db_path):
    if not db_path.exists() or db_path.stat().st_size == 0:
        raise WorkspaceError('The archive contains no usable database.')
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        raise WorkspaceError('The database in the archive cannot be opened: {0}'.format(exc))
    try:
        result = conn.execute('PRAGMA integrity_check').fetchone()
        if not result or str(result[0]).lower() != 'ok':
            raise WorkspaceError('The database in the archive failed its integrity check.')
        present = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = [t for t in REQUIRED_TABLES if t not in present]
        if missing:
            raise WorkspaceError('The database in the archive is missing: {0}.'
                                 .format(', '.join(missing)))
    except sqlite3.DatabaseError as exc:
        raise WorkspaceError('The database in the archive is corrupted: {0}'.format(exc))
    finally:
        conn.close()


def backup_current_workspace(root=None, db_path=None, tag='pre-import'):
    """Copy the live database and documents aside before anything is replaced."""
    root = Path(root) if root else jsdb.workspace_root()
    db_path = Path(db_path) if db_path else jsdb.get_db_path()
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target_dir = db_path.parent / 'backups'
    target_dir.mkdir(parents=True, exist_ok=True)

    saved = {'database': '', 'documents': '', 'created_at': stamp}
    if db_path.exists():
        copy = target_dir / '{0}-{1}-{2}.db'.format(db_path.stem, tag, stamp)
        _consistent_copy(db_path, copy)
        saved['database'] = str(copy)
    documents = root / 'documents'
    if documents.exists() and any(documents.rglob('*')):
        copy = target_dir / 'documents-{0}-{1}'.format(tag, stamp)
        shutil.copytree(str(documents), str(copy), dirs_exist_ok=True)
        saved['documents'] = str(copy)
    return saved


def import_workspace(archive_path, root=None, db_path=None):
    """Replace the local workspace with the contents of ``archive_path``.

    The order is deliberate: everything is validated and staged in a temporary
    directory first, the current workspace is copied aside second, and only
    then is anything replaced.  If the replacement itself fails, the backup is
    restored, so a failed import leaves a usable workspace behind either way.
    """
    root = Path(root) if root else jsdb.workspace_root()
    db_path = Path(db_path) if db_path else jsdb.get_db_path()

    # 1-5: validate the archive, its manifest and its database.
    report = inspect_archive(archive_path)
    staging = Path(tempfile.mkdtemp(prefix='workspace-import-'))
    try:
        with zipfile.ZipFile(str(archive_path)) as archive:
            for member in report['members']:
                archive.extract(member, str(staging))
        incoming_db = staging / DB_MEMBER
        _validate_database(incoming_db)

        # 10: bring an older database up to the current schema *before* it is
        # installed, so a failed migration never touches the live workspace.
        migrated = _migrate_staged(incoming_db, staging / 'documents')

        # 6: the current workspace is copied aside before anything moves.
        backup = backup_current_workspace(root=root, db_path=db_path)

        # 7 + 8: install.  The database lands via os.replace, which is atomic
        # on every platform this runs on.
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            landing = db_path.with_name(db_path.name + '.incoming')
            shutil.copy2(str(incoming_db), str(landing))
            os.replace(str(landing), str(db_path))
            _install_documents(staging / 'documents', root / 'documents')
        except Exception as exc:            # noqa: BLE001 - restore, then report
            _restore(backup, root=root, db_path=db_path)
            raise WorkspaceError(
                'Import failed and the previous workspace was restored: {0}'.format(exc))
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)

    # 9 + 11: re-resolve document paths against this installation and reload.
    conn = jsdb.connect()
    try:
        repaired = jsdb._portabilise_document_paths(conn)   # noqa: SLF001 - same package
        conn.commit()
        documents = conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
    finally:
        conn.close()

    return {
        'imported': True,
        'archive': str(archive_path),
        'manifest': report['manifest'],
        'schema_version': report['schema_version'],
        'migrated': migrated,
        'paths_repaired': repaired,
        'documents': documents,
        'document_files': len(report['documents']),
        'backup': backup,
        'database_path': str(db_path),
        'workspace_root': str(root),
    }


def _migrate_staged(incoming_db, incoming_documents):
    """Run the normal additive migrations against the staged copy."""
    before = _schema_version_of(incoming_db)
    if before >= jsdb.SCHEMA_VERSION:
        return {'from': before, 'to': before, 'ran': False}
    previous_db, previous_root = jsdb.get_db_path(), jsdb.workspace_root()
    try:
        jsdb.set_db_path(incoming_db)
        jsdb.set_workspace_root(incoming_documents.parent)
        jsdb.init_db(backup=False)
    except Exception as exc:                # noqa: BLE001 - the message is the point
        raise WorkspaceError('The workspace could not be migrated: {0}'.format(exc))
    finally:
        jsdb.set_db_path(previous_db)
        jsdb.set_workspace_root(previous_root)
    return {'from': before, 'to': _schema_version_of(incoming_db), 'ran': True}


def _install_documents(source, target):
    """Copy the archive's documents in, keeping whatever else is already there."""
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return 0
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(source.rglob('*')):
        if not path.is_file():
            continue
        landing = target / path.relative_to(source)
        landing.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(path), str(landing))
        copied += 1
    return copied


def _restore(backup, root, db_path):
    """Put the pre-import copies back after a failed install."""
    saved_db = backup.get('database') or ''
    if saved_db and Path(saved_db).exists():
        shutil.copy2(saved_db, str(db_path))
    saved_documents = backup.get('documents') or ''
    if saved_documents and Path(saved_documents).exists():
        shutil.copytree(saved_documents, str(root / 'documents'), dirs_exist_ok=True)
