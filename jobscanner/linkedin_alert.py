"""LinkedInAlertParser - a saved LinkedIn job alert e-mail -> ParsedJob list.

The user saves the alert mail out of their mail client as ``.txt`` (or
``.eml``) and hands the file to the Jobs screen.  Nothing here reaches out to
LinkedIn, no mailbox is opened and no session is used: the input is text the
user already has on disk.

The parser is deliberately transport-agnostic::

    text / .txt / .eml bytes  ->  LinkedInAlertParser  ->  ParsedJob[]

so a future mail transport can feed exactly the same parser without any of the
parsing logic moving.  It is also the privacy boundary: only the job-related
fields below leave this module.  The recipient address, the LinkedIn footer,
the profile tagline and every tracking/authentication parameter are dropped
here and never reach the database.

An alert block looks like this (German wording; the English variants are
recognised too)::

    Medical Device Technical Lead
    Roche
    Basel, Basel, Schweiz

    Dieses Unternehmen ist aktiv auf Personalsuche.
    Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4466403318?<tracking>

    ---------------------------------------------------------

One alert carries several of those.  The job-view URL is the anchor: blocks are
cut on the separator lines when the mail has them, and on the URLs themselves
when it does not, so an alert that was saved without the rules still parses.
"""

import html
import re
from email import message_from_bytes, policy
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SOURCE = 'linkedin'
#: Where a LinkedIn job lives once the tracking wrapper is gone.
CANONICAL_URL = 'https://www.linkedin.com/jobs/view/{0}'

#: Tracking and authentication parameters LinkedIn appends to a mailed link.
#: They identify the recipient and the alert, not the job, so they are dropped
#: before anything is stored.  Matching is case-insensitive.
TRACKING_PARAMS = {
    'savedsearchid', 'savedsearchauthtoken', 'trackingid', 'refid', 'lipi',
    'midtoken', 'midsig', 'trk', 'trkemail', 'eid', 'otptoken', 'licu', 'lici',
    'originaltoken', 'originalsubdomain', 'recommendedflavor', 'position',
    'pagenum', 'alerttype', 'notificationid', 'urlhash',
}

#: Any LinkedIn job link, with or without the ``/comm/`` mail wrapper.
JOB_URL = re.compile(
    r'https?://(?:[\w-]+\.)*linkedin\.com/(?:comm/)?jobs/view/(\d+)[^\s<>"\']*',
    re.IGNORECASE)

#: Mail headers at the top of a saved ``.txt``.  Dropped wholesale - the To:
#: line carries the user's address and nothing here is job data.
HEADER_LINE = re.compile(
    r'^(subject|sent|from|to|cc|bcc|date|reply-to|betreff|gesendet|von|an|datum)\s*:',
    re.IGNORECASE)

#: A rule between two alert entries.
SEPARATOR = re.compile(r'^[\s]*[-_=~*—–─]{3,}[\s]*$')

#: Everything from here on is LinkedIn's footer: unsubscribe links, the
#: recipient's address, the app store badges, the company address.
FOOTER_MARKERS = (
    'diese e-mail wurde an', 'sie erhalten diese e-mail', 'diese nachricht wurde an',
    'sie erhalten benachrichtigungen', 'abmelden', 'abbestellen',
    'einstellungen verwalten', 'e-mail-einstellungen', 'benachrichtigungen verwalten',
    'this email was intended for', 'you are receiving', "you're receiving",
    'unsubscribe', 'manage your email', 'email preferences', 'notification settings',
    'linkedin corporation', 'linkedin ireland', '© 20', 'copyright 20',
    'hilfe erhalten', 'get help', 'datenschutzrichtlinie', 'privacy policy',
    'nutzervereinbarung', 'user agreement',
)

#: The label in front of the link, on its own line or in front of the URL.
LINK_LABELS = (
    'jobangebot ansehen', 'job ansehen', 'stelle ansehen', 'stellenangebot ansehen',
    'alle jobs ansehen', 'view job', 'view jobs', 'see job', 'see all jobs',
    'apply now', 'jetzt bewerben',
)

#: Informational lines LinkedIn puts inside a block.  They are not the
#: location, and the first one is kept as the optional snippet.
INFO_PATTERNS = (
    re.compile(r'aktiv auf personalsuche', re.IGNORECASE),
    re.compile(r'actively (?:recruiting|hiring)', re.IGNORECASE),
    re.compile(r'\bbe an early applicant\b', re.IGNORECASE),
    re.compile(r'gehören sie zu den ersten', re.IGNORECASE),
    re.compile(r'^\s*(promoted|gesponsert|anzeige)\s*$', re.IGNORECASE),
    re.compile(r'^\s*(vor \d+|\d+\s*(tag|tage|stunde|stunden|woche|wochen|minute|minuten)\b)',
               re.IGNORECASE),
    re.compile(r'^\s*\d+\s*(day|days|hour|hours|week|weeks|minute|minutes)\s+ago\b',
               re.IGNORECASE),
    re.compile(r'\b(\d+|über \d+|over \d+)\s*(bewerber|applicants?)\b', re.IGNORECASE),
    re.compile(r'\b(easy apply|einfache bewerbung)\b', re.IGNORECASE),
    re.compile(r'\b(ihre|your)\s+(jobbenachrichtigung|job alert)\b', re.IGNORECASE),
    re.compile(r'^\s*\d+\s+(neue\s+)?(jobs?|stellen)\b', re.IGNORECASE),
)

#: Lines that are pure chrome and are dropped without being offered as a
#: snippet either.
NOISE_PATTERNS = (
    re.compile(r'^\s*(linkedin|jobbenachrichtigungen|job alerts?)\s*$', re.IGNORECASE),
    re.compile(r'^\s*$'),
)

_LOCATION_MAX = 80
#: A title or a company name; longer than that and the line is prose.
_FIELD_MAX = 140


class ParsedJob(dict):
    """One job read out of an alert.

    A plain dict so it travels through FastAPI and the tests unchanged; the
    keys are the contract:

    ``title`` ``company`` ``location`` ``source_job_id`` ``url`` ``snippet``
    ``source``
    """

    @property
    def key(self):
        """Stable identity of this entry inside one alert."""
        return self.get('source_job_id') or self.get('url') or '{0}|{1}'.format(
            self.get('company', ''), self.get('title', ''))


def strip_tracking(url):
    """Drop LinkedIn's tracking/authentication query parameters from a URL.

    Used for any link in an alert.  A LinkedIn *job* link is additionally
    rewritten to its canonical form by :func:`canonical_job_url`.
    """
    text = str(url or '').strip().rstrip('.,;)>”"\'')
    if not text:
        return ''
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    kept = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=False)
            if key.casefold() not in TRACKING_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ''))


def job_id_of(url):
    """The LinkedIn job id inside a job link, or ''."""
    match = JOB_URL.search(str(url or ''))
    return match.group(1) if match else ''


def canonical_job_url(url_or_id):
    """``https://www.linkedin.com/jobs/view/<id>`` - no wrapper, no tracking.

    The mailed link goes through ``/comm/`` and carries the alert id and an
    auth token; neither says anything about the job and neither is stored.
    """
    text = str(url_or_id or '').strip()
    job_id = job_id_of(text) or (text if text.isdigit() else '')
    if job_id:
        return CANONICAL_URL.format(job_id)
    return strip_tracking(text)


# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------
class _LineTextExtractor(HTMLParser):
    """HTML -> text that keeps the line structure the parser needs.

    ``sources.html_text.strip_html`` collapses everything onto one line, which
    is right for a job description and wrong here: the alert's meaning is in
    its line breaks.
    """

    BLOCKS = {'p', 'div', 'br', 'tr', 'td', 'th', 'table', 'li', 'ul', 'ol',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'hr'}
    SKIP = {'script', 'style', 'head'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skipping += 1
        if tag in self.BLOCKS:
            self.parts.append('\n')
        if tag == 'a':
            href = dict(attrs).get('href') or ''
            if JOB_URL.search(href):
                self.parts.append('\n' + href + '\n')

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skipping:
            self._skipping -= 1
        if tag in self.BLOCKS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self._skipping and data.strip():
            self.parts.append(data.strip())
            self.parts.append('\n')


def html_to_lines(markup):
    parser = _LineTextExtractor()
    try:
        parser.feed(html.unescape(str(markup or '')))
    except Exception:  # noqa: BLE001 - a broken mail must still yield its text
        return re.sub(r'<[^>]+>', '\n', str(markup or ''))
    return ''.join(parser.parts)


def text_from_email(data):
    """Plain text out of a ``.eml`` payload.

    ``text/plain`` is preferred; an HTML-only alert is converted with the
    line-preserving extractor above.  Attachments are ignored.
    """
    if isinstance(data, str):
        data = data.encode('utf-8', errors='replace')
    message = message_from_bytes(data, policy=policy.default)
    plain, markup = [], []
    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_maintype() == 'multipart':
            continue
        if (part.get_content_disposition() or '') == 'attachment':
            continue
        try:
            body = part.get_content()
        except (LookupError, ValueError):
            payload = part.get_payload(decode=True) or b''
            body = payload.decode('utf-8', errors='replace')
        if part.get_content_type() == 'text/plain':
            plain.append(body)
        elif part.get_content_type() == 'text/html':
            markup.append(body)
    if plain:
        return '\n'.join(plain)
    if markup:
        return html_to_lines('\n'.join(markup))
    return ''


def decode_upload(filename, data):
    """Bytes from an upload -> the alert text, whatever the user saved.

    ``.eml`` goes through the mail parser; everything else is treated as text
    and, if it clearly still is markup, converted.  The encoding is guessed the
    forgiving way, because a mail client's export is not always UTF-8.
    """
    name = str(filename or '').casefold()
    if isinstance(data, str):
        raw = data.encode('utf-8', errors='replace')
    else:
        raw = bytes(data or b'')
    if name.endswith('.eml') or name.endswith('.msg') or _looks_like_email(raw):
        text = text_from_email(raw)
        if text.strip():
            return text
    text = _decode_text(raw)
    if '<html' in text[:2000].casefold() or '<table' in text[:2000].casefold():
        return html_to_lines(text)
    return text


def _looks_like_email(raw):
    head = _decode_text(raw[:400]).lstrip().casefold()
    return head.startswith(('return-path:', 'received:', 'mime-version:',
                            'content-type: multipart', 'message-id:', 'delivered-to:'))


def _decode_text(raw):
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', errors='replace')


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------
class LinkedInAlertParser:
    """Alert text -> :class:`ParsedJob` list.

    Stateless and offline.  ``parse`` is the only entry point the rest of the
    application uses; ``parse_upload`` is the same thing for an uploaded file.
    """

    def parse(self, text):
        body = self._body(text)
        jobs, seen = [], set()
        for chunk in self._chunks(body):
            job = self._parse_chunk(chunk)
            if job is None:
                continue
            if job['source_job_id'] and job['source_job_id'] in seen:
                continue          # the same posting linked twice in one mail
            if job['source_job_id']:
                seen.add(job['source_job_id'])
            jobs.append(job)
        return jobs

    def parse_upload(self, filename, data):
        return self.parse(decode_upload(filename, data))

    # -- text preparation --------------------------------------------------
    def _body(self, text):
        """Drop the mail headers and everything from the footer onwards."""
        lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
        start = 0
        for index, line in enumerate(lines):
            if HEADER_LINE.match(line.strip()):
                start = index + 1
            elif line.strip() and index > 0 and start:
                break
        kept = []
        for line in lines[start:]:
            if _is_footer(line):
                break
            kept.append(line)
        return '\n'.join(kept)

    def _chunks(self, body):
        """Cut the body into one piece per job.

        Separator rules first, because that is what the real alert uses.  A
        piece that still contains more than one job link is cut again at the
        links themselves, which is what a mail saved without the rules needs.
        """
        blocks = []
        current = []
        for line in body.split('\n'):
            if SEPARATOR.match(line):
                blocks.append('\n'.join(current))
                current = []
            else:
                current.append(line)
        blocks.append('\n'.join(current))

        chunks = []
        for block in blocks:
            if len(JOB_URL.findall(block)) > 1:
                chunks.extend(_split_on_links(block))
            else:
                chunks.append(block)
        return [c for c in chunks if JOB_URL.search(c)]

    # -- one job -----------------------------------------------------------
    def _parse_chunk(self, chunk):
        match = JOB_URL.search(chunk)
        if match is None:
            return None
        job_id = match.group(1)
        before = chunk[:match.start()]

        lines, snippet = [], ''
        for raw_line in before.split('\n'):
            line = _clean_line(raw_line)
            if not line or _is_noise(line):
                continue
            if _is_info(line):
                snippet = snippet or line
                continue
            lines.append(line)

        # The job stands directly in front of its link, so the fields are read
        # backwards from the link.  Anything further up is the alert's own
        # preamble ("Sebastian, hier sind Ihre neuen Jobs") and is ignored.
        while lines and not _looks_like_field(lines[-1]):
            snippet = snippet or lines[-1]
            lines.pop()
        location = ''
        if len(lines) >= 3 and _looks_like_location(lines[-1]):
            title, company, location = lines[-3], lines[-2], lines[-1]
        elif len(lines) >= 2:
            title, company = lines[-2], lines[-1]
        else:
            return None

        if not title or not company:
            return None
        return ParsedJob({
            'source': SOURCE,
            'title': title,
            'company': company,
            'location': location,
            'source_job_id': job_id,
            'url': CANONICAL_URL.format(job_id),
            'snippet': _safe_snippet(snippet),
        })


def parse(text):
    """Module-level convenience: alert text -> ParsedJob list."""
    return LinkedInAlertParser().parse(text)


def parse_upload(filename, data):
    """Module-level convenience: an uploaded .txt / .eml -> ParsedJob list."""
    return LinkedInAlertParser().parse_upload(filename, data)


# --------------------------------------------------------------------------
# Line classification
# --------------------------------------------------------------------------
def _split_on_links(block):
    """One piece per job link, each piece ending with its own link."""
    pieces, start = [], 0
    for match in JOB_URL.finditer(block):
        pieces.append(block[start:match.end()])
        start = match.end()
    return pieces


def _clean_line(line):
    text = str(line or '').strip().strip('‌​﻿')
    text = re.sub(r'^[\-\*•·>]+\s*', '', text)
    if HEADER_LINE.match(text):
        return ''
    lowered = text.casefold()
    for label in LINK_LABELS:
        if lowered.startswith(label):
            rest = text[len(label):].lstrip(' :–-')
            return '' if not rest or rest.startswith('http') else rest
    if text.startswith('http'):
        return ''
    return re.sub(r'\s+', ' ', text).strip()


def _is_footer(line):
    lowered = str(line or '').casefold()
    return any(marker in lowered for marker in FOOTER_MARKERS)


def _is_noise(line):
    return any(pattern.match(line) for pattern in NOISE_PATTERNS)


def _is_info(line):
    return any(pattern.search(line) for pattern in INFO_PATTERNS)


def _looks_like_field(line):
    """A short label (title / company / place), not a sentence of prose."""
    text = str(line or '').strip()
    return bool(text) and len(text) <= _FIELD_MAX and not text.endswith(('.', '!', '?', ':'))


def _looks_like_location(line):
    """A place, not a sentence.

    LinkedIn writes "Basel, Basel, Schweiz" or "Zürich, Zürich, Schweiz"; an
    informational line is a sentence and ends like one.  Length and final
    punctuation separate the two reliably, and the informational lines that do
    not are already caught by :data:`INFO_PATTERNS`.
    """
    text = str(line or '').strip()
    if not text or len(text) > _LOCATION_MAX:
        return False
    if text.endswith(('.', '!', '?', ':')):
        return False
    return True


def _safe_snippet(text):
    """Never let an address or a link out of the parser."""
    value = str(text or '').strip()
    if not value or '@' in value or 'http' in value.casefold():
        return ''
    return value[:200]
