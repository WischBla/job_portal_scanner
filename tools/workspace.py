#!/usr/bin/env python3
"""Move the portable workspace between machines from the command line.

    python3 tools/workspace.py export ~/Desktop/job-assistant-backup.zip
    python3 tools/workspace.py import ~/Desktop/job-assistant-backup.zip
    python3 tools/workspace.py inspect ~/Desktop/job-assistant-backup.zip

This is the fallback that works when the UI cannot start.  ``run.py
export-workspace`` / ``run.py import-workspace`` delegate here, so both
spellings do exactly the same thing.

The workspace is ``data/app.db`` plus ``documents/``.  API keys live in
``.env`` and in the environment; they are never part of an export and must be
configured again on the destination machine.
"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from jobscanner import db as jsdb          # noqa: E402
from jobscanner import workspace           # noqa: E402


def _say(message=''):
    sys.stdout.write(message + '\n')
    sys.stdout.flush()


def _human(size):
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return '{0:.1f} {1}'.format(value, unit)
        value /= 1024
    return str(size)


def do_export(args):
    jsdb.init_db()
    result = workspace.export_workspace(args.destination)
    manifest = result['manifest']
    _say('Workspace exported.')
    _say('  Archive:   {0}'.format(result['archive']))
    _say('  Size:      {0}'.format(_human(result['size_bytes'])))
    _say('  Schema:    {0}'.format(manifest['schema_version']))
    _say('  Documents: {0}'.format(result['document_count']))
    _say('  Contents:  ' + ', '.join(
        '{0} {1}'.format(count, table) for table, count in manifest['counts'].items() if count))
    _say()
    _say(workspace.PRIVACY_NOTICE)
    _say('API keys are NOT included - configure them in .env on the other machine.')
    return 0


def do_inspect(args):
    report = workspace.inspect_archive(args.archive)
    manifest = report['manifest']
    _say('Archive:   {0}'.format(args.archive))
    _say('Format:    {0}'.format(manifest.get('format_version')))
    _say('Schema:    {0} (this build: {1})'.format(report['schema_version'], jsdb.SCHEMA_VERSION))
    _say('Exported:  {0}'.format(manifest.get('exported_at', '')))
    _say('Revision:  {0}'.format(manifest.get('git_revision') or 'unknown'))
    _say('Documents: {0}'.format(len(report['documents'])))
    _say('Contents:  ' + ', '.join(
        '{0} {1}'.format(count, table)
        for table, count in (manifest.get('counts') or {}).items() if count))
    if report['needs_migration']:
        _say('This workspace is older than the application and will be migrated on import.')
    return 0


def do_import(args):
    report = workspace.inspect_archive(args.archive)
    if not args.yes:
        _say('About to replace the workspace at:')
        _say('  Database:  {0}'.format(jsdb.get_db_path()))
        _say('  Documents: {0}/documents'.format(jsdb.workspace_root()))
        _say('with the archive {0} (schema {1}, {2} document files).'.format(
            Path(args.archive).name, report['schema_version'], len(report['documents'])))
        _say('The current workspace is copied to data/backups/ first.')
        answer = input('Continue? [y/N] ').strip().lower()
        if answer not in ('y', 'yes'):
            _say('Cancelled. Nothing was changed.')
            return 1

    result = workspace.import_workspace(args.archive)
    _say('Workspace imported.')
    _say('  Database:  {0}'.format(result['database_path']))
    _say('  Documents: {0} files, {1} records'.format(
        result['document_files'], result['documents']))
    if result['migrated']['ran']:
        _say('  Migrated:  schema {0} -> {1}'.format(
            result['migrated']['from'], result['migrated']['to']))
    if result['paths_repaired']:
        _say('  Repaired:  {0} document path(s) made workspace-relative'.format(
            result['paths_repaired']))
    _say('  Backup:    {0}'.format(result['backup'].get('database') or 'none needed'))
    _say()
    _say('Set any API keys in .env on this machine; they are never part of an export.')
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog='tools/workspace.py',
        description='Export and import the portable Job Assistant workspace.')
    commands = parser.add_subparsers(dest='command', required=True)

    export = commands.add_parser('export', help='write a portable archive')
    export.add_argument('destination', nargs='?', default='.',
                        help='target .zip file or a directory (default: current directory)')
    export.set_defaults(handler=do_export)

    importer = commands.add_parser('import', help='replace this workspace with an archive')
    importer.add_argument('archive', help='the .zip file to import')
    importer.add_argument('-y', '--yes', action='store_true', help='do not ask for confirmation')
    importer.set_defaults(handler=do_import)

    inspect = commands.add_parser('inspect', help='validate an archive without importing it')
    inspect.add_argument('archive')
    inspect.set_defaults(handler=do_inspect)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except workspace.WorkspaceError as exc:
        sys.stderr.write('\n{0}\n'.format(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
