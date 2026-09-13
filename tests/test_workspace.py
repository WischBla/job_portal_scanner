"""Portable workspace: export, import and a real cross-directory round trip.

The point of these tests is a single claim: a workspace written under one
directory works unchanged under a *different* one.  Every round trip below
therefore uses two temporary roots that share no path component beyond the
system temp directory, which is what proves no absolute path survived.
"""

import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from jobscanner import db as jsdb
from jobscanner import documents as documents_mod
from jobscanner import workspace


class Installation:
    """One complete installation: its own workspace root and its own database.

    Entering it points the whole package at this installation, which is how a
    test can build a workspace in one place and import it in another without
    the two ever sharing state.
    """

    def __init__(self, name):
        self.name = name
        self._dir = None
        self.root = None
        self.db_path = None
        self._previous = None

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory(prefix='ws-{0}-'.format(self.name))
        self.root = Path(self._dir.name).resolve()
        self.db_path = self.root / 'data' / 'app.db'
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.activate()
        jsdb.init_db(backup=False)
        documents_mod.ensure_dirs()
        return self

    def activate(self):
        self._previous = (jsdb.get_db_path(), jsdb.workspace_root())
        jsdb.set_db_path(self.db_path)
        jsdb.set_workspace_root(self.root)
        return self

    def deactivate(self):
        if self._previous:
            jsdb.set_db_path(self._previous[0])
            jsdb.set_workspace_root(self._previous[1])
            self._previous = None

    def __exit__(self, *exc):
        self.deactivate()
        self._dir.cleanup()
        return False

    def connect(self):
        return jsdb.connect()


def _previous_paths():
    return jsdb.get_db_path(), jsdb.workspace_root()


def _restore(paths):
    jsdb.set_db_path(paths[0])
    jsdb.set_workspace_root(paths[1])


def seed(install, marker='round-trip'):
    """Put one of everything into an installation so a loss would be visible."""
    with install.connect() as conn:
        conn.execute(
            "UPDATE person_profile SET first_name=?, last_name=?, email=?, phone=?, "
            "notice_period=?, travel_willingness=?, achievements=?, strengths=? WHERE id=1",
            ('Test', 'Person', 'person@example.test', '+41 00 000 00 00', '3 months',
             'A travel preference',
             json.dumps(['An achievement worth keeping']), json.dumps(['Strategic'])))
        conn.execute(
            "UPDATE search_profile SET minimum_match_score=?, allowed_locations=?, "
            "deprioritized_keywords=?, preferred_radius_km=? WHERE id=1",
            (57, json.dumps(['Zurich']), json.dumps(['gtm']), 50))
        conn.execute(
            "INSERT INTO applications (company, position, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)", (marker + ' AG', 'Head of Platform', 'Applied', 'x', 'x'))
        conn.execute(
            "INSERT INTO company_watchlist (company_name, priority, career_source_type, "
            "created_at, updated_at) VALUES (?,?,?,?,?)", (marker + ' Co', 'A', 'manual', 'x', 'x'))
        conn.commit()
    document = documents_mod.store('cv_en', 'cv.pdf', b'%PDF-1.4 ' + marker.encode(),
                                   label='CV (English)')
    return document


class ExportTests(unittest.TestCase):
    def test_archive_layout_and_manifest(self):
        with Installation('src') as install:
            seed(install)
            target = install.root / 'out'
            result = workspace.export_workspace(target / 'ws.zip')

            with zipfile.ZipFile(result['archive']) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read(workspace.MANIFEST_NAME))
            self.assertIn('manifest.json', names)
            self.assertIn('data/app.db', names)
            self.assertTrue(any(n.startswith('documents/') for n in names))

            self.assertEqual(manifest['format_version'], workspace.EXPORT_FORMAT_VERSION)
            self.assertEqual(manifest['schema_version'], jsdb.SCHEMA_VERSION)
            self.assertTrue(manifest['exported_at'])
            self.assertEqual(len(manifest['database']['sha256']), 64)
            self.assertEqual(manifest['counts']['applications'], 1)
            self.assertEqual(len(manifest['documents']), 1)
            self.assertTrue(manifest['documents'][0]['path'].startswith('documents/'))

    def test_a_directory_destination_gets_a_dated_name(self):
        with Installation('src') as install:
            seed(install)
            out = install.root / 'exports'
            out.mkdir()
            result = workspace.export_workspace(out)
            self.assertTrue(Path(result['archive']).name.startswith(workspace.ARCHIVE_PREFIX))
            self.assertTrue(Path(result['archive']).name.endswith('.zip'))

    def test_secrets_and_caches_are_never_exported(self):
        with Installation('src') as install:
            seed(install)
            # Everything a careless sweep of the folder would pick up.
            (install.root / '.env').write_text('ANTHROPIC_API_KEY=sk-ant-secret\n')
            (install.root / '.git').mkdir()
            (install.root / '.git' / 'config').write_text('[remote]\n')
            (install.root / '.venv').mkdir()
            (install.root / '.venv' / 'pyvenv.cfg').write_text('home = /usr\n')
            (install.root / 'documents' / '.DS_Store').write_bytes(b'junk')
            cookies = install.root / 'documents' / '_removed'
            cookies.mkdir(parents=True, exist_ok=True)
            (cookies / 'old-cv.pdf').write_bytes(b'replaced')

            result = workspace.export_workspace(install.root / 'out' / 'ws.zip')
            with zipfile.ZipFile(result['archive']) as archive:
                names = archive.namelist()
                blob = b''.join(archive.read(name) for name in names)

            for forbidden in ('.env', '.git', '.venv', '_removed', '.DS_Store'):
                self.assertFalse([n for n in names if forbidden in n],
                                 '{0} must never be exported, found in {1}'.format(forbidden, names))
            self.assertNotIn(b'sk-ant-secret', blob)

    def test_export_refuses_when_there_is_no_database(self):
        previous = _previous_paths()
        try:
            with tempfile.TemporaryDirectory() as folder:
                jsdb.set_workspace_root(folder)
                jsdb.set_db_path(Path(folder) / 'data' / 'missing.db')
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.export_workspace(Path(folder) / 'ws.zip')
        finally:
            _restore(previous)


class InspectTests(unittest.TestCase):
    def _archive(self, install):
        return workspace.export_workspace(install.root / 'out' / 'ws.zip')['archive']

    def test_a_valid_archive_reports_its_contents(self):
        with Installation('src') as install:
            seed(install)
            report = workspace.inspect_archive(self._archive(install))
            self.assertEqual(report['schema_version'], jsdb.SCHEMA_VERSION)
            self.assertFalse(report['needs_migration'])
            self.assertEqual(len(report['documents']), 1)

    def test_a_file_that_is_not_a_zip_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            bogus = Path(folder) / 'not.zip'
            bogus.write_text('this is not an archive')
            with self.assertRaises(workspace.WorkspaceError):
                workspace.inspect_archive(bogus)

    def test_a_missing_archive_is_rejected(self):
        with self.assertRaises(workspace.WorkspaceError):
            workspace.inspect_archive('/nonexistent/nope.zip')

    def test_an_archive_without_a_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ws.zip'
            with zipfile.ZipFile(str(path), 'w') as archive:
                archive.writestr('data/app.db', b'SQLite format 3\x00')
            with self.assertRaisesRegex(workspace.WorkspaceError, 'manifest'):
                workspace.inspect_archive(path)

    def test_an_unsupported_format_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ws.zip'
            with zipfile.ZipFile(str(path), 'w') as archive:
                archive.writestr(workspace.MANIFEST_NAME,
                                 json.dumps({'format_version': 99, 'schema_version': 1}))
                archive.writestr('data/app.db', b'SQLite format 3\x00')
            with self.assertRaisesRegex(workspace.WorkspaceError, 'format version'):
                workspace.inspect_archive(path)

    def test_a_newer_schema_is_refused_with_a_clear_message(self):
        with Installation('src') as install:
            seed(install)
            archive = self._archive(install)
            rewritten = install.root / 'newer.zip'
            _rewrite_manifest(archive, rewritten,
                              {'schema_version': jsdb.SCHEMA_VERSION + 5})
            with self.assertRaises(workspace.WorkspaceError) as caught:
                workspace.inspect_archive(rewritten)
            message = str(caught.exception)
            self.assertIn('newer version', message)
            self.assertIn('never downgraded', message)

    def test_an_archive_with_a_path_outside_the_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'evil.zip'
            with zipfile.ZipFile(str(path), 'w') as archive:
                archive.writestr(workspace.MANIFEST_NAME,
                                 json.dumps({'format_version': 1, 'schema_version': 1}))
                archive.writestr('data/app.db', b'SQLite format 3\x00')
                archive.writestr('../../.ssh/id_rsa', b'key')
            with self.assertRaises(workspace.WorkspaceError):
                workspace.inspect_archive(path)


def _rewrite_manifest(source, target, changes):
    """Copy an archive with a patched manifest - used to fake other versions."""
    with zipfile.ZipFile(str(source)) as original:
        manifest = json.loads(original.read(workspace.MANIFEST_NAME))
        manifest.update(changes)
        with zipfile.ZipFile(str(target), 'w') as rewritten:
            for info in original.infolist():
                if info.filename == workspace.MANIFEST_NAME:
                    rewritten.writestr(workspace.MANIFEST_NAME, json.dumps(manifest))
                else:
                    rewritten.writestr(info.filename, original.read(info.filename))
    return target


class RoundTripTests(unittest.TestCase):
    """Export here, import *there* - two different directories, every time."""

    def test_everything_survives_a_move_to_a_different_directory(self):
        with Installation('source') as source:
            document = seed(source, marker='moved')
            archive_holder = tempfile.mkdtemp(prefix='ws-carrier-')
            archive = workspace.export_workspace(Path(archive_holder) / 'ws.zip')['archive']
            source_root = str(source.root)
            source.deactivate()

            with Installation('destination') as destination:
                self.assertNotEqual(str(destination.root), source_root)
                result = workspace.import_workspace(archive)
                self.assertTrue(result['imported'])

                with destination.connect() as conn:
                    person = dict(conn.execute(
                        'SELECT * FROM person_profile WHERE id=1').fetchone())
                    profile = dict(conn.execute(
                        'SELECT * FROM search_profile WHERE id=1').fetchone())
                    application = dict(conn.execute(
                        'SELECT * FROM applications ORDER BY id DESC LIMIT 1').fetchone())
                    watch = conn.execute(
                        'SELECT COUNT(*) FROM company_watchlist WHERE company_name=?',
                        ('moved Co',)).fetchone()[0]
                    stored = dict(conn.execute('SELECT * FROM documents LIMIT 1').fetchone())

                # profile
                self.assertEqual(person['email'], 'person@example.test')
                self.assertEqual(person['phone'], '+41 00 000 00 00')
                self.assertEqual(json.loads(person['achievements']), ['An achievement worth keeping'])
                # search settings
                self.assertEqual(profile['minimum_match_score'], 57)
                self.assertEqual(json.loads(profile['deprioritized_keywords']), ['gtm'])
                self.assertEqual(profile['preferred_radius_km'], 50)
                # applications and watchlist
                self.assertEqual(application['company'], 'moved AG')
                self.assertEqual(watch, 1)

                # documents: the stored path is relative, resolves under the
                # NEW root, and the bytes really made the journey.
                self.assertFalse(Path(stored['path']).is_absolute())
                self.assertTrue(stored['path'].startswith('documents/'))
                self.assertNotIn(source_root, stored['path'])
                landed = documents_mod.get(stored['id'])
                self.assertTrue(landed['exists'])
                self.assertTrue(landed['absolute_path'].startswith(str(destination.root)))
                self.assertEqual(Path(landed['absolute_path']).read_bytes(),
                                 b'%PDF-1.4 moved')
                self.assertEqual(landed['filename'], document['filename'])
            shutil.rmtree(archive_holder, ignore_errors=True)

    def test_no_stored_path_mentions_the_machine_it_came_from(self):
        with Installation('source') as source:
            seed(source)
            carrier = tempfile.mkdtemp(prefix='ws-carrier-')
            archive = workspace.export_workspace(Path(carrier) / 'ws.zip')['archive']
            source_root = str(source.root)
            source.deactivate()

            with Installation('destination') as destination:
                workspace.import_workspace(archive)
                with destination.connect() as conn:
                    paths = [r[0] for r in conn.execute('SELECT path FROM documents').fetchall()]
                self.assertTrue(paths)
                for path in paths:
                    self.assertFalse(Path(path).is_absolute(), path)
                    self.assertNotIn(source_root, path)
                    self.assertNotIn('/Users/', path)
                    self.assertNotIn('C:\\\\', path)
            shutil.rmtree(carrier, ignore_errors=True)

    def test_the_current_workspace_is_backed_up_before_an_import(self):
        with Installation('source') as source:
            seed(source, marker='incoming')
            carrier = tempfile.mkdtemp(prefix='ws-carrier-')
            archive = workspace.export_workspace(Path(carrier) / 'ws.zip')['archive']
            source.deactivate()

            with Installation('destination') as destination:
                seed(destination, marker='existing')
                result = workspace.import_workspace(archive)

                backup = Path(result['backup']['database'])
                self.assertTrue(backup.exists())
                conn = sqlite3.connect(str(backup))
                try:
                    kept = conn.execute('SELECT company FROM applications '
                                        'ORDER BY id DESC LIMIT 1').fetchone()[0]
                finally:
                    conn.close()
                self.assertEqual(kept, 'existing AG')
                self.assertTrue(Path(result['backup']['documents']).exists())
            shutil.rmtree(carrier, ignore_errors=True)

    def test_an_older_workspace_is_migrated_on_import(self):
        with Installation('source') as source:
            seed(source, marker='old')
            carrier = tempfile.mkdtemp(prefix='ws-carrier-')
            archive = workspace.export_workspace(Path(carrier) / 'ws.zip')['archive']
            source.deactivate()

            # Pretend the archive came from an older build.
            aged = Path(carrier) / 'old.zip'
            _rewrite_manifest(archive, aged, {'schema_version': jsdb.SCHEMA_VERSION - 1})
            _downgrade_archive_schema(aged, jsdb.SCHEMA_VERSION - 1)

            with Installation('destination') as destination:
                result = workspace.import_workspace(aged)
                self.assertTrue(result['migrated']['ran'])
                self.assertEqual(result['migrated']['to'], jsdb.SCHEMA_VERSION)
                with destination.connect() as conn:
                    self.assertEqual(
                        conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'")
                        .fetchone()[0], str(jsdb.SCHEMA_VERSION))
                    self.assertEqual(conn.execute(
                        'SELECT company FROM applications ORDER BY id DESC LIMIT 1'
                    ).fetchone()[0], 'old AG')
            shutil.rmtree(carrier, ignore_errors=True)


def _downgrade_archive_schema(archive_path, version):
    """Rewrite the archive's database so it really claims an older schema."""
    staging = Path(tempfile.mkdtemp(prefix='ws-downgrade-'))
    try:
        with zipfile.ZipFile(str(archive_path)) as archive:
            archive.extractall(str(staging))
        db_path = staging / workspace.DB_MEMBER
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'",
                         (str(version),))
            conn.commit()
        finally:
            conn.close()
        with zipfile.ZipFile(str(archive_path), 'w') as archive:
            for path in sorted(staging.rglob('*')):
                if path.is_file():
                    archive.write(str(path), path.relative_to(staging).as_posix())
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)
    return archive_path


class FailedImportTests(unittest.TestCase):
    def test_a_corrupted_database_is_rejected_and_nothing_is_touched(self):
        with Installation('destination') as destination:
            seed(destination, marker='keep')
            with tempfile.TemporaryDirectory() as folder:
                broken = Path(folder) / 'broken.zip'
                with zipfile.ZipFile(str(broken), 'w') as archive:
                    archive.writestr(workspace.MANIFEST_NAME, json.dumps(
                        {'format_version': 1, 'schema_version': jsdb.SCHEMA_VERSION}))
                    archive.writestr('data/app.db', b'SQLite format 3\x00 but the rest is garbage')
                with self.assertRaises(workspace.WorkspaceError):
                    workspace.import_workspace(broken)

            # Everything is exactly as it was.
            with destination.connect() as conn:
                self.assertEqual(conn.execute(
                    'SELECT company FROM applications ORDER BY id DESC LIMIT 1'
                ).fetchone()[0], 'keep AG')
                document = dict(conn.execute('SELECT * FROM documents LIMIT 1').fetchone())
            self.assertTrue(documents_mod.get(document['id'])['exists'])

    def test_a_database_missing_workspace_tables_is_rejected(self):
        with Installation('destination') as destination:
            seed(destination, marker='keep')
            with tempfile.TemporaryDirectory() as folder:
                empty_db = Path(folder) / 'empty.db'
                conn = sqlite3.connect(str(empty_db))
                conn.execute('CREATE TABLE unrelated (id INTEGER)')
                conn.commit()
                conn.close()
                archive_path = Path(folder) / 'wrong.zip'
                with zipfile.ZipFile(str(archive_path), 'w') as archive:
                    archive.writestr(workspace.MANIFEST_NAME, json.dumps(
                        {'format_version': 1, 'schema_version': jsdb.SCHEMA_VERSION}))
                    archive.write(str(empty_db), 'data/app.db')
                with self.assertRaisesRegex(workspace.WorkspaceError, 'missing'):
                    workspace.import_workspace(archive_path)

            with destination.connect() as conn:
                self.assertEqual(conn.execute(
                    'SELECT company FROM applications ORDER BY id DESC LIMIT 1'
                ).fetchone()[0], 'keep AG')


    def test_a_failure_while_installing_restores_the_previous_workspace(self):
        """The one case validation cannot catch: a failure mid-install.

        The archive is valid, so the import gets as far as replacing files.
        Document installation is then made to fail, which must leave the
        destination on its previous database and documents, not half-written.
        """
        with Installation('source') as source:
            seed(source, marker='incoming')
            carrier = tempfile.mkdtemp(prefix='ws-carrier-')
            archive = workspace.export_workspace(Path(carrier) / 'ws.zip')['archive']
            source.deactivate()

            with Installation('destination') as destination:
                document = seed(destination, marker='existing')
                original_bytes = Path(documents_mod.get(document['id'])['absolute_path']).read_bytes()

                def explode(*_args, **_kwargs):
                    raise OSError('disk went away')

                installer = workspace._install_documents
                workspace._install_documents = explode
                try:
                    with self.assertRaisesRegex(workspace.WorkspaceError, 'restored'):
                        workspace.import_workspace(archive)
                finally:
                    workspace._install_documents = installer

                with destination.connect() as conn:
                    kept = conn.execute('SELECT company FROM applications '
                                        'ORDER BY id DESC LIMIT 1').fetchone()[0]
                    kept_document = dict(conn.execute(
                        'SELECT * FROM documents LIMIT 1').fetchone())
                self.assertEqual(kept, 'existing AG')
                landed = documents_mod.get(kept_document['id'])
                self.assertTrue(landed['exists'])
                self.assertEqual(Path(landed['absolute_path']).read_bytes(), original_bytes)
            shutil.rmtree(carrier, ignore_errors=True)


class PortablePathTests(unittest.TestCase):
    def test_an_absolute_path_is_converted_on_migration(self):
        with Installation('install') as install:
            document = seed(install)
            absolute = str(install.root / document['path'])
            with install.connect() as conn:
                conn.execute('UPDATE documents SET path=? WHERE id=?', (absolute, document['id']))
                conn.commit()

            jsdb.init_db(backup=False)          # the normal additive migration

            with install.connect() as conn:
                stored = conn.execute('SELECT path FROM documents WHERE id=?',
                                      (document['id'],)).fetchone()[0]
            self.assertEqual(stored, document['path'])
            self.assertFalse(Path(stored).is_absolute())
            self.assertTrue(documents_mod.get(document['id'])['exists'])

    def test_a_windows_style_absolute_path_is_converted(self):
        self.assertEqual(
            jsdb.portable_document_path(r'C:\Users\sebastian\app\documents\cv\CV.pdf'),
            'documents/cv/CV.pdf')

    def test_a_path_with_no_documents_segment_is_left_alone(self):
        self.assertEqual(jsdb.portable_document_path('/somewhere/else/file.pdf'), '')

    def test_new_uploads_are_always_stored_relative(self):
        with Installation('install') as install:
            document = documents_mod.store('cv_de', 'lebenslauf.pdf', b'%PDF-1.4 de')
            self.assertFalse(Path(document['path']).is_absolute())
            self.assertTrue(document['path'].startswith('documents/'))
            self.assertNotIn(str(install.root), document['path'])


if __name__ == '__main__':
    unittest.main()
