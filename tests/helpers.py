"""Shared test helpers: a throwaway database and a fake job source."""

import tempfile
from pathlib import Path

from jobscanner import db as jsdb
from jobscanner.normalizer import JobNormalizer
from jobscanner.profile import sanitize

_NORMALIZER = JobNormalizer()


def make_job(title='Director Platform Engineering', location='Zurich, Switzerland',
             company='Example AG', description='', url=None, source='Test',
             source_type='test', external_id='1', **extra):
    raw = {
        'source': source, 'source_type': source_type, 'external_id': external_id,
        'company': company, 'title': title, 'location': location,
        'job_url': url if url is not None else 'https://example.test/{0}'.format(external_id),
        'description': description, 'remote': extra.pop('remote', None),
    }
    raw.update(extra)
    return _NORMALIZER.normalize(raw)


def make_profile(**overrides):
    return sanitize(overrides)


class TempDatabase:
    """Points the whole package at a fresh database file for one test case."""

    def __init__(self):
        self._dir = None
        self._previous = None

    def __enter__(self):
        self._previous = jsdb.get_db_path()
        self._dir = tempfile.TemporaryDirectory()
        jsdb.set_db_path(Path(self._dir.name) / 'applications.db')
        jsdb.init_db()
        return self

    def __exit__(self, *exc):
        jsdb.set_db_path(self._previous)
        self._dir.cleanup()
        return False

    def connect(self):
        return jsdb.connect()


STRONG_DESCRIPTION = (
    'You will lead a team of platform engineers, own the AWS cloud infrastructure, '
    'SRE practices, CI/CD pipelines, observability and drive technology transformation. '
    'Stakeholder management and cross-functional leadership across the organisation. '
    'AI and automation initiatives are part of the roadmap.'
)
