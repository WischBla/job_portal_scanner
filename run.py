#!/usr/bin/env python3
"""Launcher for the personal job assistant.

    python3 run.py                 # start and open the browser
    python3 run.py --no-browser
    python3 run.py --port 9000

    python3 run.py export-workspace ~/Desktop/job-assistant-backup.zip
    python3 run.py import-workspace ~/Desktop/job-assistant-backup.zip

This file only orchestrates startup: dependency check, directories, database
migration (with a backup), the server and the browser.  All application logic
lives in the ``jobscanner`` package.
"""

import sys

MIN_PYTHON = (3, 9)
if sys.version_info < MIN_PYTHON:
    sys.stderr.write('Python {0}.{1} or newer is required, but this is Python {2}.\n'.format(
        MIN_PYTHON[0], MIN_PYTHON[1], sys.version.split()[0]))
    raise SystemExit(1)

import argparse  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import webbrowser  # noqa: E402
from pathlib import Path  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8765
STARTUP_TIMEOUT = 25.0

REQUIREMENTS_HINT = (
    'Install the dependencies first:\n\n'
    '    python3 -m venv .venv\n'
    '    .venv/bin/pip install -r requirements.txt\n'
    '    .venv/bin/python -m playwright install chromium\n\n'
    'and then start the app with:\n\n'
    '    .venv/bin/python run.py\n'
)


def say(message=''):
    sys.stdout.write(message + '\n')
    sys.stdout.flush()


def fail(message, hint=None):
    sys.stdout.flush()
    sys.stderr.write('\nStartup failed: ' + message + '\n')
    if hint:
        sys.stderr.write('\n' + hint + '\n')
    raise SystemExit(1)


def check_dependencies():
    missing = []
    for module, package in (('fastapi', 'fastapi'), ('uvicorn', 'uvicorn[standard]'),
                            ('multipart', 'python-multipart')):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        fail('missing Python packages: {0}.'.format(', '.join(missing)), REQUIREMENTS_HINT)


def port_is_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def wait_until_ready(url, timeout=STARTUP_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + '/api/health', timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def prepare_data():
    """Migrate the database (backing it up first) and create the document folders."""
    from jobscanner import db as jsdb
    from jobscanner import documents as documents_mod

    adopted = False
    if not jsdb.get_db_path().exists() and jsdb.LEGACY_DB_PATH.exists():
        adopted = True
    jsdb.init_db()
    documents_mod.ensure_dirs()
    if adopted:
        say('Existing database carried over from {0} (the original file is untouched).'.format(
            jsdb.LEGACY_DB_PATH.name))
    say('Database:  {0}'.format(jsdb.get_db_path()))
    say('Documents: {0}'.format(documents_mod.documents_dir()))


#: Verbs handled by tools/workspace.py rather than by the server.  They are
#: recognised before argparse so the launcher's own flags stay unambiguous.
WORKSPACE_VERBS = {'export-workspace': 'export', 'import-workspace': 'import',
                   'inspect-workspace': 'inspect'}


def run_workspace_command(argv):
    """Delegate ``run.py export-workspace <path>`` to the workspace CLI."""
    sys.path.insert(0, str(BASE_DIR))
    from tools import workspace as workspace_cli

    return workspace_cli.main([WORKSPACE_VERBS[argv[0]]] + list(argv[1:]))


def main():
    parser = argparse.ArgumentParser(description='Personal job discovery and application assistant.')
    parser.add_argument('--host', default=os.environ.get('JOB_ASSISTANT_HOST', DEFAULT_HOST))
    parser.add_argument('--port', type=int,
                        default=int(os.environ.get('JOB_ASSISTANT_PORT', DEFAULT_PORT)))
    parser.add_argument('--no-browser', action='store_true',
                        default=os.environ.get('JOB_ASSISTANT_NO_BROWSER') == '1')
    parser.add_argument('--reload', action='store_true', help='development auto-reload')
    args = parser.parse_args()

    os.chdir(str(BASE_DIR))
    sys.path.insert(0, str(BASE_DIR))

    say()
    say('Job Assistant')
    say('=============')
    check_dependencies()
    prepare_data()

    if not port_is_free(args.host, args.port):
        fail('port {0} is already in use.'.format(args.port),
             'Another copy may already be running. Open http://{0}:{1} or use --port.'.format(
                 args.host, args.port))

    url = 'http://{0}:{1}'.format(args.host, args.port)
    say('URL:       {0}'.format(url))
    say()

    import uvicorn

    if args.reload:
        uvicorn.run('app:app', host=args.host, port=args.port, reload=True, log_level='info')
        return 0

    config = uvicorn.Config('app:app', host=args.host, port=args.port, log_level='warning')
    server = uvicorn.Server(config)

    def open_browser():
        if wait_until_ready(url) and not args.no_browser:
            webbrowser.open(url)

    threading.Thread(target=open_browser, name='open-browser', daemon=True).start()
    say('Starting... press Ctrl+C to stop.')
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            from jobscanner.apply import SERVICE
            SERVICE.close()
        except Exception:  # noqa: BLE001 - shutdown must stay quiet
            pass
        say('Job Assistant stopped.')
    return 0


if __name__ == '__main__':
    try:
        if len(sys.argv) > 1 and sys.argv[1] in WORKSPACE_VERBS:
            raise SystemExit(run_workspace_command(sys.argv[1:]))
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
