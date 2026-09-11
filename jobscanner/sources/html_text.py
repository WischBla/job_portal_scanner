"""Shared HTML -> plain text helper for adapters that return markup."""

import html
import re
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)


def strip_html(value):
    if not value:
        return ''
    parser = _TextExtractor()
    try:
        parser.feed(html.unescape(str(value)))
        return re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()
    except Exception:
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(value))).strip()
