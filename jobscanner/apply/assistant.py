"""Application assistant.

Opens the real application page in a controlled Chromium window, identifies the
form, fills the objective fields from the personal profile, attaches the CV (and
the motivation letter when it is clearly requested) and then **stops**.

The assistant never clicks a submit control.  There is no code path in this
module that submits an application: the submit button is only located so that
it can be reported and deliberately left untouched.  The browser window stays
open so the user reviews everything and submits manually.
"""

import queue
import re
import threading

from .. import documents as documents_mod
from . import adapters as adapters_mod
from . import fields as fields_mod

#: Injected into the page to enumerate every form control with the text a human
#: would read next to it (label, aria-label, aria-labelledby, placeholder, ...).
COLLECT_SCRIPT = r"""
() => {
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' &&
           (rect.width > 0 || rect.height > 0 || el.type === 'file');
  };
  const labelText = (el) => {
    const bits = [];
    if (el.id) {
      const byFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (byFor) bits.push(byFor.innerText);
    }
    const wrapping = el.closest('label');
    if (wrapping) bits.push(wrapping.innerText);
    const described = el.getAttribute('aria-labelledby');
    if (described) {
      described.split(/\s+/).forEach((id) => {
        const node = document.getElementById(id);
        if (node) bits.push(node.innerText);
      });
    }
    if (!bits.length) {
      const group = el.closest('div,fieldset,section,li');
      if (group) {
        const heading = group.querySelector('label,legend,h1,h2,h3,h4,h5,p,span');
        if (heading) bits.push(heading.innerText);
      }
    }
    return bits.join(' ').replace(/\s+/g, ' ').trim().slice(0, 300);
  };
  const out = [];
  const nodes = document.querySelectorAll('input, textarea, select');
  let index = 0;
  nodes.forEach((el) => {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
    if (!visible(el) && type !== 'file') return;
    el.setAttribute('data-jsa-idx', String(index));
    out.push({
      idx: index,
      tag: el.tagName.toLowerCase(),
      type: type,
      name: el.getAttribute('name') || '',
      id: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria: el.getAttribute('aria-label') || '',
      autocomplete: el.getAttribute('autocomplete') || '',
      label: labelText(el),
      required: el.required || el.getAttribute('aria-required') === 'true',
      value: (el.value || '').slice(0, 200),
      options: el.tagName.toLowerCase() === 'select'
        ? Array.from(el.options).map((o) => o.textContent.trim()).slice(0, 40) : [],
    });
    index += 1;
  });
  return out;
}
"""

FIND_BUTTONS = r"""
() => Array.from(document.querySelectorAll('button, a[role="button"], input[type="submit"], a'))
  .filter((el) => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  })
  .slice(0, 250)
  .map((el, i) => {
    el.setAttribute('data-jsa-btn', String(i));
    return { idx: i, text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 120) };
  });
"""


class ApplyError(RuntimeError):
    pass


def _descriptor(control):
    return ' '.join(str(control.get(key) or '') for key in
                    ('label', 'aria', 'name', 'id', 'placeholder', 'autocomplete'))


def build_report(controls, person, adapter, settings=None):
    """Decide, without a browser, what would be filled and what needs review.

    Kept pure so the mapping logic is testable without Playwright.
    """
    settings = settings or {}
    values = fields_mod.plan(person)
    filled, review, files = [], [], []
    used_keys = set()
    for control in controls:
        descriptor = _descriptor(control)
        if control.get('type') == 'file':
            kind = adapter.file_kind(descriptor)
            files.append({'idx': control['idx'], 'kind': kind or 'unknown',
                          'label': (control.get('label') or descriptor)[:120]})
            continue
        key, reason = adapter.classify(descriptor)
        if control.get('type') in ('checkbox', 'radio'):
            review.append({'idx': control['idx'], 'label': (control.get('label') or descriptor)[:160],
                           'reason': 'Choice field - decide yourself',
                           'required': bool(control.get('required'))})
            continue
        if key and key in values and key not in used_keys:
            if control.get('tag') == 'select':
                review.append({'idx': control['idx'],
                               'label': (control.get('label') or descriptor)[:160],
                               'reason': 'Dropdown - select the right option yourself',
                               'required': bool(control.get('required'))})
                continue
            filled.append({'idx': control['idx'], 'field': key, 'value': values[key],
                           'label': (control.get('label') or descriptor)[:120]})
            used_keys.add(key)
        else:
            review.append({'idx': control['idx'], 'label': (control.get('label') or descriptor)[:160],
                           'reason': reason or 'No objective value available',
                           'required': bool(control.get('required'))})
    return {'filled': filled, 'review': review, 'files': files}


def document_choice(settings, conn=None):
    """Which CV / motivation letter the assistant should attach."""
    settings = settings or {}
    language = (settings.get('apply_cv_language') or 'en').lower()
    cv = None
    if settings.get('apply_upload_cv', True):
        cv = documents_mod.primary('cv_{0}'.format(language), conn=conn) or \
             documents_mod.primary('cv_en' if language != 'en' else 'cv_de', conn=conn)
    motivation = None
    if settings.get('apply_upload_motivation'):
        motivation = documents_mod.primary('motivation_{0}'.format(language), conn=conn) or \
                     documents_mod.primary('motivation_en' if language != 'en' else 'motivation_de',
                                           conn=conn)
    return {'cv': cv, 'motivation': motivation}


class _Worker(threading.Thread):
    """Owns the Playwright instance; the sync API needs its own thread."""

    def __init__(self):
        super().__init__(name='apply-assistant', daemon=True)
        self.commands = queue.Queue()
        self._playwright = None
        self._browser = None
        self._context = None

    def run(self):
        while True:
            command, payload, reply = self.commands.get()
            if command == 'stop':
                self._shutdown()
                reply.put(('ok', {'closed': True}))
                return
            try:
                if command == 'open':
                    reply.put(('ok', self._open(payload)))
                elif command == 'close':
                    self._shutdown()
                    reply.put(('ok', {'closed': True}))
                else:
                    reply.put(('error', 'Unknown command {0}'.format(command)))
            except Exception as exc:  # noqa: BLE001 - reported to the UI verbatim
                reply.put(('error', '{0}: {1}'.format(type(exc).__name__, exc)))

    # -- browser lifecycle -------------------------------------------------
    def _ensure_browser(self, headless):
        from playwright.sync_api import sync_playwright
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = self._playwright.chromium.launch(headless=bool(headless))
            self._context = self._browser.new_context(
                viewport={'width': 1380, 'height': 950},
                accept_downloads=True,
                locale='en-GB')
        return self._context

    def _shutdown(self):
        for closer in (getattr(self._context, 'close', None),
                       getattr(self._browser, 'close', None),
                       getattr(self._playwright, 'stop', None)):
            try:
                if closer:
                    closer()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
        self._playwright = self._browser = self._context = None

    # -- the actual assist -------------------------------------------------
    def _open(self, payload):
        url = payload['url']
        person = payload['person']
        settings = payload.get('settings') or {}
        docs = payload.get('documents') or {}
        autofill = settings.get('apply_autofill', True)

        context = self._ensure_browser(settings.get('apply_headless'))
        page = context.new_page()
        page.set_default_timeout(20000)
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        try:
            page.wait_for_load_state('networkidle', timeout=8000)
        except Exception:  # noqa: BLE001 - a busy page is fine
            pass

        adapter = adapters_mod.detect(url, page.content()[:20000])
        notes = []

        controls = page.evaluate(COLLECT_SCRIPT)
        if not controls:
            # The form is often behind an "Apply" button.
            if self._click_apply(page, adapter):
                notes.append('Opened the application form from the "Apply" control.')
                page.wait_for_timeout(1500)
                controls = page.evaluate(COLLECT_SCRIPT)

        report = build_report(controls, person, adapter, settings)

        filled_ok = []
        if autofill:
            for item in report['filled']:
                selector = '[data-jsa-idx="{0}"]'.format(item['idx'])
                try:
                    page.fill(selector, item['value'], timeout=4000)
                    filled_ok.append(item)
                except Exception as exc:  # noqa: BLE001 - a field that resists is not fatal
                    report['review'].append({
                        'idx': item['idx'], 'label': item['label'],
                        'reason': 'Could not be filled automatically ({0})'.format(
                            type(exc).__name__), 'required': False})
        else:
            notes.append('Autofill is switched off in Config; nothing was typed.')

        uploads = []
        for target in report['files']:
            document = docs.get('cv') if target['kind'] == 'cv' else (
                docs.get('motivation') if target['kind'] == 'motivation' else None)
            if not document:
                report['review'].append({
                    'idx': target['idx'], 'label': target['label'],
                    'reason': 'File upload - attach it yourself' if target['kind'] == 'unknown'
                              else 'No {0} stored in Profile'.format(target['kind']),
                    'required': False})
                continue
            try:
                page.set_input_files('[data-jsa-idx="{0}"]'.format(target['idx']),
                                     document['absolute_path'], timeout=6000)
                uploads.append({'kind': target['kind'], 'filename': document['filename'],
                                'label': target['label']})
            except Exception as exc:  # noqa: BLE001
                report['review'].append({
                    'idx': target['idx'], 'label': target['label'],
                    'reason': 'Upload failed ({0}) - attach it yourself'.format(type(exc).__name__),
                    'required': False})

        submit_label = self._find_submit(page)
        if submit_label:
            notes.append('Submit control found ("{0}"). It was deliberately NOT clicked.'
                         .format(submit_label))
        else:
            notes.append('No submit control identified - review the page before sending.')

        return {
            'url': page.url,
            'adapter': adapter.name,
            'adapter_label': adapter.label,
            'controls_found': len(controls),
            'filled': filled_ok,
            'review': report['review'],
            'uploads': uploads,
            'notes': notes,
            'submitted': False,
        }

    def _click_apply(self, page, adapter):
        try:
            buttons = page.evaluate(FIND_BUTTONS)
        except Exception:  # noqa: BLE001
            return False
        for button in buttons:
            text = fields_mod.normalize_label(button.get('text'))
            if not text or adapters_mod.is_submit(text):
                continue
            if any(re.search(pattern, text) for pattern in adapter.open_form_patterns):
                try:
                    page.click('[data-jsa-btn="{0}"]'.format(button['idx']), timeout=4000)
                    return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    def _find_submit(self, page):
        """Locate the submit control purely so it can be reported, never clicked."""
        try:
            buttons = page.evaluate(FIND_BUTTONS)
        except Exception:  # noqa: BLE001
            return ''
        for button in buttons:
            if adapters_mod.is_submit(button.get('text')):
                return button.get('text', '')[:60]
        return ''


class ApplyService:
    """Thin, thread-safe front end for the worker."""

    def __init__(self):
        self._worker = None
        self._lock = threading.Lock()
        self.last_report = None

    def available(self):
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False, ('Playwright is not installed. Run: '
                           'pip install playwright && python -m playwright install chromium')
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return False, 'Playwright is installed but its sync API is unavailable.'
        return True, ''

    def _ensure_worker(self):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = _Worker()
                self._worker.start()
            return self._worker

    def _call(self, command, payload=None, timeout=180):
        worker = self._ensure_worker()
        reply = queue.Queue()
        worker.commands.put((command, payload or {}, reply))
        try:
            status, result = reply.get(timeout=timeout)
        except queue.Empty:
            raise ApplyError('The browser did not answer within {0} seconds.'.format(timeout))
        if status != 'ok':
            raise ApplyError(str(result))
        return result

    def open_application(self, url, person, settings, docs, timeout=180):
        ok, message = self.available()
        if not ok:
            raise ApplyError(message)
        if not url:
            raise ApplyError('This job has no application URL.')
        report = self._call('open', {'url': url, 'person': person,
                                     'settings': settings, 'documents': docs}, timeout=timeout)
        self.last_report = report
        return report

    def close(self):
        try:
            return self._call('close', timeout=30)
        except ApplyError:
            return {'closed': False}


#: One shared assistant for the process - a second Chromium would be confusing.
SERVICE = ApplyService()
