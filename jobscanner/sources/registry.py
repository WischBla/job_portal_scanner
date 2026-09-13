"""Source resolution: watchlist entry -> adapter + adapter config.

One table, ``SOURCE_KINDS``, is the single place that knows

* what a watchlist source *kind* is called in the UI,
* what its identifier means ("board token", "careers base URL", ...),
* how to turn that identifier into the config the adapter expects,
* and what the human-facing careers URL for it is.

Everything else - watchlist, migrations, API, scan pipeline - goes through
here, so adding a provider is a single entry rather than a grep.

Discovery only.  No adapter and nothing in this module decides whether a job is
relevant; that stays with HardFilter and MatchScorer after normalisation.
"""

from .base import SourceError, get_adapter


class SourceKind:
    def __init__(self, key, label, identifier_label, build_config, identity_field='',
                 public_url=None, automated=True, identifier_required=True, notes=''):
        self.key = key
        self.label = label
        self.identifier_label = identifier_label
        self._build_config = build_config
        #: The config key that identifies *which* employer a source points at.
        #: Two sources of one kind with the same value are the same board.
        self.identity_field = identity_field
        self._public_url = public_url
        self.automated = automated
        self.identifier_required = identifier_required
        self.notes = notes

    def config(self, identifier, company):
        return self._build_config(str(identifier or '').strip(), str(company or '').strip())

    def public_url(self, identifier):
        identifier = str(identifier or '').strip()
        if not identifier or self._public_url is None:
            return ''
        return self._public_url(identifier)


SOURCE_KINDS = {
    'manual': SourceKind(
        'manual', 'Manual', 'Careers page URL',
        lambda ident, company: {'company': company},
        automated=False, identifier_required=False,
        notes='No verified public endpoint; the UI only links to the careers page.'),

    'greenhouse': SourceKind(
        'greenhouse', 'Greenhouse', 'Board token',
        lambda ident, company: {'board_token': ident, 'company': company},
        identity_field='board_token',
        public_url=lambda ident: 'https://job-boards.greenhouse.io/{0}'.format(ident)),

    'lever': SourceKind(
        'lever', 'Lever', 'Lever site',
        lambda ident, company: {'site': ident, 'company': company, 'region': 'global'},
        identity_field='site',
        public_url=lambda ident: 'https://jobs.lever.co/{0}'.format(ident)),

    'smartrecruiters': SourceKind(
        'smartrecruiters', 'SmartRecruiters', 'Company identifier',
        lambda ident, company: {'company_id': ident, 'company': company},
        identity_field='company_id',
        public_url=lambda ident: 'https://jobs.smartrecruiters.com/{0}'.format(ident)),

    'successfactors': SourceKind(
        'successfactors', 'SuccessFactors career site', 'Career site base URL',
        lambda ident, company: {'base_url': ident, 'company': company,
                                'location_query': 'Switzerland'},
        identity_field='base_url',
        public_url=lambda ident: '{0}/search/'.format(ident.rstrip('/'))),

    'phenom': SourceKind(
        'phenom', 'Phenom career site', 'Careers base URL',
        lambda ident, company: {'base_url': ident, 'company': company,
                                'countries': ['Switzerland']},
        identity_field='base_url',
        public_url=lambda ident: '{0}/global/en/search-results'.format(ident.rstrip('/'))),

    'amazon_jobs': SourceKind(
        'amazon_jobs', 'Amazon Jobs (public)', 'Country code',
        lambda ident, company: {'country_code': ident or 'CHE', 'company': company,
                                'location_query': 'Switzerland'},
        identity_field='country_code',
        public_url=lambda ident: 'https://www.amazon.jobs/en/search?loc_query=Switzerland'),

    'jsonld': SourceKind(
        'jsonld', 'schema.org JobPosting', 'Career page URL',
        lambda ident, company: {'url': ident, 'company': company},
        identity_field='url',
        public_url=lambda ident: ident),

    'rss': SourceKind(
        'rss', 'RSS / Atom feed', 'Feed URL',
        lambda ident, company: {'url': ident, 'company': company},
        identity_field='url',
        public_url=lambda ident: ident),
}

#: Source kinds a watchlist entry may use.  'manual' is always first.
WATCHLIST_SOURCE_TYPES = tuple(['manual'] + sorted(k for k in SOURCE_KINDS if k != 'manual'))

#: Status vocabulary shared by the watchlist, the API and the UI.
ACTIVE = 'ACTIVE'
MANUAL = 'MANUAL'
UNAVAILABLE = 'UNAVAILABLE'
ERROR = 'ERROR'
SOURCE_STATUSES = (ACTIVE, MANUAL, UNAVAILABLE, ERROR)


def get_kind(source_type):
    kind = SOURCE_KINDS.get(str(source_type or '').strip().lower())
    if kind is None:
        raise SourceError('Unknown source kind: {0}'.format(source_type))
    return kind


def is_automated(source_type):
    return str(source_type or '').strip().lower() in SOURCE_KINDS and \
        SOURCE_KINDS[str(source_type).strip().lower()].automated


def source_label(source_type):
    """Human name for a source kind.

    Watchlist kinds are named here; the broad aggregators are not watchlist
    kinds at all, so their own adapter supplies the label rather than the UI
    falling back to a raw type name like "arbeitnow".
    """
    try:
        return get_kind(source_type).label
    except SourceError:
        pass
    try:
        return get_adapter(source_type).label or str(source_type or '')
    except SourceError:
        return str(source_type or '')


def resolve(source_type, identifier, company):
    """Everything the pipeline and the UI need about one configured source."""
    kind = get_kind(source_type)
    return {
        'source_type': kind.key,
        'label': kind.label,
        'identity_field': kind.identity_field,
        'automated': kind.automated,
        'identifier': str(identifier or '').strip(),
        'identifier_label': kind.identifier_label,
        'config': kind.config(identifier, company),
        'source_url': kind.public_url(identifier),
    }


def verify(source_type, identifier, company):
    """Hit the real endpoint and report whether it answers for this company.

    Returns ``(status, job_count, detail)``.  A source only becomes ACTIVE when
    a live request came back with postings - which is exactly what stops a
    guessed Greenhouse token or a 404 from being saved as if it worked.
    """
    kind = get_kind(source_type)
    if not kind.automated:
        return (MANUAL, 0, 'Manual entry - no endpoint is contacted.')
    if kind.identifier_required and not str(identifier or '').strip():
        return (UNAVAILABLE, 0, '{0} is missing.'.format(kind.identifier_label))
    try:
        count, detail = get_adapter(kind.key).verify(kind.config(identifier, company))
    except SourceError as exc:
        return (ERROR, 0, str(exc)[:400])
    except Exception as exc:  # noqa: BLE001 - the message is the useful part
        return (ERROR, 0, '{0}: {1}'.format(type(exc).__name__, exc)[:400])
    if not count:
        return (UNAVAILABLE, 0, 'The endpoint answered but returned no postings.')
    return (ACTIVE, count, detail)


def kind_catalogue():
    """What the Config screen offers when attaching a source to a company."""
    out = []
    for key in WATCHLIST_SOURCE_TYPES:
        kind = SOURCE_KINDS[key]
        out.append({
            'type': kind.key,
            'label': kind.label,
            'identifier_label': kind.identifier_label,
            'automated': kind.automated,
            'identifier_required': kind.identifier_required,
            'notes': kind.notes,
        })
    return out
