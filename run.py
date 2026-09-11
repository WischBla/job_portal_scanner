#!/usr/bin/env python3
"""Launcher for the local Job Portal Scanner + Application Tracker.

Usage:
    python3 run.py
    python3 run.py --port 9000

This file only orchestrates startup (configuration, directories, database
init, backend server, browser). All application logic lives in app.py.
"""

# --- Python version gate -----------------------------------------------------
# Kept deliberately free of modern syntax so that an old interpreter still
# reaches this message instead of dying with a SyntaxError.
import sys

MIN_PYTHON = (3, 8)

if sys.version_info < MIN_PYTHON:
    sys.stderr.write(
        "Python {0}.{1} or newer is required, but this is Python {2}.\n"
        "On macOS try:  python3 run.py\n".format(
            MIN_PYTHON[0], MIN_PYTHON[1], sys.version.split()[0]
        )
    )
    raise SystemExit(1)

import argparse
import errno
import os
import signal
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# Always resolve relative to this file, never to the terminal's cwd.
BASE_DIR = Path(__file__).resolve().parent

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 8765
STARTUP_TIMEOUT = 20.0  # seconds to wait for the server to answer HTTP


def say(message=''):
    """Print a startup line immediately, even when output is piped to a file."""
    sys.stdout.write(message + '\n')
    sys.stdout.flush()


def fail(message, hint=None):
    """Print a readable error (no stack trace) and exit."""
    sys.stdout.flush()
    sys.stderr.write('\nStartup failed: ' + message + '\n')
    if hint:
        sys.stderr.write('\n' + hint + '\n')
    raise SystemExit(1)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='run.py',
        description='Start the local Job Portal Scanner + Application Tracker.',
    )
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help='TCP port to listen on (default: %(default)s)')
    parser.add_argument('--host', default=DEFAULT_HOST,
                        help='Address to bind to (default: %(default)s, local only)')
    parser.add_argument('--no-browser', action='store_true',
                        help='Do not open a browser window automatically')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    return args


def ensure_directories():
    """Create local directories the app needs, and verify the frontend is present."""
    (BASE_DIR / 'data').mkdir(parents=True, exist_ok=True)

    static_dir = BASE_DIR / 'static'
    if not (static_dir / 'index.html').is_file():
        fail(
            'the frontend files are missing (expected {0}).'.format(static_dir / 'index.html'),
            'Make sure the whole project folder was unzipped, not just run.py.',
        )


def load_backend():
    """Import the existing backend module (app.py) with a readable error on failure."""
    if not (BASE_DIR / 'app.py').is_file():
        fail(
            'the backend is missing (expected {0}).'.format(BASE_DIR / 'app.py'),
            'Make sure the whole project folder was unzipped, not just run.py.',
        )

    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))
    try:
        import app  # noqa: F401  (the backend; imported for its server + db API)
    except ModuleNotFoundError as exc:
        fail(
            'a required Python package is missing: {0}'.format(exc.name),
            'Missing Python dependencies.\n\nRun:\n\n'
            '    python3 -m pip install -r requirements.txt',
        )
    except ImportError as exc:
        fail('the backend (app.py) could not be imported: {0}'.format(exc))
    except SyntaxError as exc:
        fail('app.py contains a syntax error (line {0}): {1}'.format(exc.lineno, exc.msg))

    for attr in ('create_server', 'init_db', 'DB_PATH'):
        if not hasattr(app, attr):
            fail(
                "the backend does not provide '{0}' - app.py looks outdated.".format(attr),
                'Update app.py to the version that ships with this launcher.',
            )
    return app


def port_in_use(host, port):
    """Best-effort check whether something already listens on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def start_server(app, host, port):
    """Ask the backend for a bound server, translating bind errors into advice."""
    if port_in_use(host, port):
        fail(
            'port {0} on {1} is already in use.'.format(port, host),
            'Another copy of the tracker is probably still running.\n'
            'Open http://{0}:{1} in your browser, or start on a free port:\n\n'
            '    python3 run.py --port {2}'.format(host, port, port + 1),
        )
    try:
        return app.create_server(host, port)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE,):
            fail(
                'port {0} on {1} is already in use.'.format(port, host),
                'Start on a different port:\n\n    python3 run.py --port {0}'.format(port + 1),
            )
        if exc.errno in (errno.EACCES, errno.EPERM):
            fail(
                'permission denied for port {0}.'.format(port),
                'Ports below 1024 need administrator rights. Pick a higher port:\n\n'
                '    python3 run.py --port 8765',
            )
        if exc.errno == errno.EADDRNOTAVAIL:
            fail('the address {0} is not available on this machine.'.format(host))
        fail('the server could not be started: {0}'.format(exc))
    except sqlite3.Error as exc:
        fail(
            'the database could not be opened or initialised: {0}'.format(exc),
            'Check that {0} exists and is writable.'.format(BASE_DIR / 'data'),
        )


def wait_until_ready(url, timeout=STARTUP_TIMEOUT):
    """Poll the server until it actually answers HTTP (or the timeout expires)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0):
                return True
        except urllib.error.HTTPError:
            return True  # answering with a status code means it is up
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


def install_stop_handlers():
    """Return an Event that is set on Ctrl+C or a termination request.

    Installing the handler explicitly also covers the case where the process was
    started in a context that had SIGINT set to "ignore".
    """
    stop = threading.Event()

    def _request_stop(signum, frame):
        stop.set()

    for sig_name in ('SIGINT', 'SIGTERM', 'SIGHUP'):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _request_stop)
        except (ValueError, OSError, RuntimeError):
            pass  # not supported on this platform / not the main thread
    return stop


def display_db_path(app):
    """Show the database path relative to the project when possible."""
    db_path = Path(app.DB_PATH)
    try:
        return os.path.join('.', str(db_path.relative_to(BASE_DIR)))
    except ValueError:
        return str(db_path)


def main(argv=None):
    args = parse_args(argv)
    url = 'http://{0}:{1}'.format(args.host, args.port)

    say('Job Tracker starting...')

    ensure_directories()
    app = load_backend()

    server = start_server(app, args.host, args.port)
    # Keep the backend's module globals consistent with what we actually bound to.
    app.HOST, app.PORT = args.host, args.port

    say('Database: ' + display_db_path(app))
    say('Server:   ' + url)
    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        say('Warning:  bound to ' + args.host + ' - reachable from other machines.')
    say()

    thread = threading.Thread(target=server.serve_forever, name='http-server', daemon=True)
    thread.start()
    stop = install_stop_handlers()

    if not wait_until_ready(url):
        server.shutdown()
        server.server_close()
        fail('the server did not respond within {0:.0f} seconds.'.format(STARTUP_TIMEOUT))

    if args.no_browser:
        say('Browser not opened (--no-browser). Open ' + url + ' manually.')
    elif webbrowser.open(url):
        say('Browser opened.')
    else:
        say('Could not open a browser automatically. Open ' + url + ' manually.')

    say()
    say('Press Ctrl+C to stop.')

    try:
        while thread.is_alive() and not stop.is_set():
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        say()
        say('Stopping...')
        server.shutdown()
        server.server_close()
        say('Job Tracker stopped.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
