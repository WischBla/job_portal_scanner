'use strict';
/* Runs the real static/app.js against a small but genuine DOM, so what an
 * Applications card actually does can be measured rather than grepped:
 *
 *   - a card is built closed, and its body is not there yet
 *   - a closed card carries the summary and the status control, and none of
 *     the maintenance UI - documents, notes, history
 *   - opening one builds the detail once and reveals it
 *   - closing it puts it away again without throwing the detail away
 *   - neither touches any other card or the network
 *   - "Mark as Applied" is offered while - and only while - the record is in
 *     Preparation
 *   - the Preparation block says whether the package is ready, and an
 *     application with no documents says so instead of inventing one
 *
 * Results are printed as JSON for tests/test_application_tracking.py.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = path.join(__dirname, '..', '..', 'static', 'app.js');

const STATUSES = ['Preparation', 'Applied', 'Screening', 'Interview',
  'Final', 'Offer', 'Rejected', 'Withdrawn'];
const CLOSED = ['Rejected', 'Withdrawn'];
const DOCUMENT_KINDS = [
  { kind: 'cv', label: 'CV / Resume' },
  { kind: 'cover_letter', label: 'Cover Letter' },
  { kind: 'additional', label: 'Additional Document' },
  { kind: 'certificate', label: 'Certificate / Reference' },
  { kind: 'other', label: 'Other' },
];

// --------------------------------------------------------------- mini DOM
function makeDom() {
  const world = { fetches: 0, byId: {} };

  function element(tag) {
    const node = {
      tagName: String(tag || '').toUpperCase(),
      type: '', className: '', textContent: '', innerHTML: '', value: '',
      selected: false, checked: false, rows: 0, title: '', id: '',
      style: {}, dataset: {}, attrs: {}, listeners: {}, children: [], parentNode: null,
    };
    node.classList = {
      contains: (name) => String(node.className || '').split(/\s+/).indexOf(name) >= 0,
      add(name) { if (!node.classList.contains(name)) node.className = (node.className + ' ' + name).trim(); },
      remove(name) {
        node.className = String(node.className || '').split(/\s+/)
          .filter((part) => part && part !== name).join(' ');
      },
      toggle(name, on) { if (on) node.classList.add(name); else node.classList.remove(name); },
    };
    Object.defineProperty(node, 'firstChild', { get: () => node.children[0] || null });
    let hidden = false;
    Object.defineProperty(node, 'hidden', {
      get: () => hidden, set: (value) => { hidden = !!value; },
    });
    node.setAttribute = (key, value) => {
      node.attrs[key] = String(value);
      if (key === 'class') node.className = String(value);
      if (key === 'id') { node.id = String(value); world.byId[node.id] = node; }
      if (key === 'hidden') node.hidden = true;
      if (key.indexOf('data-') === 0) {
        node.dataset[key.slice(5).replace(/-([a-z])/g, (m, c) => c.toUpperCase())] = String(value);
      }
    };
    node.getAttribute = (key) => (key in node.attrs ? node.attrs[key] : null);
    node.addEventListener = (type, fn) => {
      (node.listeners[type] || (node.listeners[type] = [])).push(fn);
    };
    node.removeEventListener = () => {};
    node.appendChild = (child) => {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = node;
      node.children.push(child);
      return child;
    };
    node.insertBefore = (child, before) => {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = node;
      const at = before ? node.children.indexOf(before) : -1;
      if (at < 0) node.children.push(child);
      else node.children.splice(at, 0, child);
      return child;
    };
    node.removeChild = (child) => {
      const at = node.children.indexOf(child);
      if (at >= 0) node.children.splice(at, 1);
      child.parentNode = null;
      return child;
    };
    node.remove = () => { if (node.parentNode) node.parentNode.removeChild(node); };
    node.replaceWith = (other) => {
      if (!node.parentNode) return;
      const parent = node.parentNode;
      const at = parent.children.indexOf(node);
      parent.removeChild(node);
      if (at < 0) parent.appendChild(other);
      else parent.insertBefore(other, parent.children[at] || null);
    };
    node.querySelector = (selector) => find(node, selector)[0] || null;
    node.querySelectorAll = (selector) => find(node, selector);
    node.getBoundingClientRect = () => ({ top: 0, left: 0, width: 0, height: 0 });
    node.click = () => fire(node, 'click', { target: node });
    node.focus = () => {};
    return node;
  }

  /* One compound selector: tag, #id, .class and [attr="value"]. */
  function matches(node, selector) {
    const compound = selector.split('>').pop().trim().split(/\s+/).pop();
    const parts = compound.match(/^[a-zA-Z]+|[.#][\w-]+|\[[^\]]+\]/g) || [];
    return parts.every((part) => {
      if (part[0] === '.') return node.classList.contains(part.slice(1));
      if (part[0] === '#') return node.id === part.slice(1);
      if (part[0] === '[') {
        const bits = /^\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]$/.exec(part);
        if (!bits) return false;
        const actual = node.getAttribute(bits[1]);
        return bits[2] === undefined ? actual !== null : String(actual) === bits[2];
      }
      return node.tagName === part.toUpperCase();
    });
  }

  function find(root, selector) {
    const out = [];
    (selector || '').split(',').forEach((one) => {
      const walk = (node) => {
        node.children.forEach((child) => {
          if (matches(child, one)) out.push(child);
          walk(child);
        });
      };
      walk(root);
    });
    return out;
  }

  function fire(node, type, event) {
    const seen = Object.assign({ target: node, stopPropagation() {}, preventDefault() {} }, event);
    for (let cursor = node; cursor; cursor = cursor.parentNode) {
      (cursor.listeners[type] || []).forEach((fn) => fn(seen));
    }
  }

  const root = element('body');
  ['application-list', 'pipeline', 'job-list', 'toast', 'dialog', 'dialog-title',
   'dialog-body', 'application-status'].forEach((id) => {
    const node = element('div');
    node.setAttribute('id', id);
    if (id === 'application-list' || id === 'job-list') node.className = 'list';
    root.appendChild(node);
  });

  const document = {
    documentElement: { scrollHeight: 1000 },
    body: root,
    createElement: element,
    addEventListener() {},
    querySelector: (selector) => find(root, selector)[0] || null,
    querySelectorAll: (selector) => find(root, selector),
  };

  const window = {
    scrollY: 0, innerHeight: 800,
    scrollTo() {}, addEventListener() {}, removeEventListener() {}, open() {},
  };

  return { world, document, window, element, fire, find, matches, root };
}

function load(dom) {
  const context = {
    document: dom.document,
    window: dom.window,
    location: { hash: '#applications' },
    console,
    setTimeout,
    clearTimeout,
    FormData: function FormData() { this.append = () => {}; },
    fetch: () => { dom.world.fetches += 1; return Promise.reject(new Error('offline')); },
  };
  context.globalThis = context;
  vm.createContext(context);
  const source = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { el, applicationCard, toggleApplication, openApplications,'
    + ' renderPipeline, isActionTarget, statusControl, applicationDetail,'
    + ' preparationBlock, prettyDate, appliedLine,'
    + ' setMeta: (m) => { applicationMeta = m; },'
    + ' setApplications: (list) => { applicationsById = new Map(list.map((a) => [a.id, a])); } };\n';
  vm.runInContext(source, context, { filename: 'app.js' });
  const api = context.__t;
  api.setMeta({ statuses: STATUSES, event_types: ['Note'], closed_statuses: CLOSED,
    document_kinds: DOCUMENT_KINDS, document_formats: ['PDF', 'DOCX'] });
  return api;
}

/* Every word a node and its descendants put on the screen. */
function textOf(node) {
  if (!node) return '';
  let out = String(node.textContent || '') + ' '
    + String(node.innerHTML || '').replace(/<[^>]*>/g, ' ') + ' ';
  node.children.forEach((child) => { out += textOf(child); });
  return out.replace(/\s+/g, ' ').trim();
}

let counter = 0;
function makeApplication(overrides) {
  counter += 1;
  const application = Object.assign({
    id: counter,
    company: 'AWS EMEA SARL (Switzerland Branch)',
    position: 'Principal Technical Program Manager',
    location: 'Zurich',
    work_model: 'Hybrid',
    status: 'Preparation',
    salary_estimate: 'CHF 200k-250k',
    applied_at: '',
    applied_date: '',
    created_at: '2026-09-13T22:07:37',
    updated_at: '2026-09-14T09:00:00',
    notes: 'English CV used',
    job_url: 'https://example.test/job',
    next_action: '',
    follow_up_date: '',
    is_active: true,
    is_applied: false,
    documents: [],
    document_summary: [],
    status_history: [{ from_status: '', to_status: 'Preparation',
      changed_at: '2026-09-13T22:07:37' }],
  }, overrides || {});
  return application;
}

function makeDocument(overrides) {
  return Object.assign({
    id: 11, document_kind: 'cv', kind_label: 'CV / Resume',
    original_filename: 'Sebastian_Bierwisch_CV_EN.pdf',
    mime_type: 'application/pdf', size_bytes: 53700,
    source: 'DOCUMENT_STORE', source_document_id: 8, exists: true,
    created_at: '2026-09-14T10:00:00',
  }, overrides || {});
}

// --------------------------------------------------------------------------
const results = {};

/* 1. Built closed: no detail on screen, and the control says so. */
{
  const dom = makeDom();
  const app = load(dom);
  const application = makeApplication({ applied_at: '2026-09-13T22:07:37', status: 'Applied' });
  const list = dom.document.querySelector('#application-list');
  const card = list.appendChild(app.applicationCard(application));
  const body = card.querySelector('.card-body');
  const toggle = card.querySelector('.card-toggle');
  const head = card.querySelector('.card-head');
  results.collapsed_by_default = {
    bodyHidden: body.hidden,
    bodyChildren: body.children.length,
    ariaExpanded: toggle.getAttribute('aria-expanded'),
    ariaControls: toggle.getAttribute('aria-controls'),
    bodyId: body.id,
    cardClass: card.className,
    openSet: app.openApplications.size,
    headText: textOf(head),
    cardText: textOf(card),
  };
}

/* 2. What a closed card says - summary and status, and nothing that maintains
      the record. */
{
  const dom = makeDom();
  const app = load(dom);
  const application = makeApplication({
    documents: [makeDocument({})],
    document_summary: [{ kind: 'cv', kind_label: 'CV / Resume', count: 1,
      filename: 'Sebastian_Bierwisch_CV_EN.pdf' }],
  });
  const card = dom.document.querySelector('#application-list')
    .appendChild(app.applicationCard(application));
  const picker = card.querySelector('.status-picker');
  results.collapsed_summary = {
    text: textOf(card),
    statusOptions: picker.children.map((o) => o.getAttribute('value')),
    selected: picker.children.filter((o) => o.selected).map((o) => o.getAttribute('value')),
    attachButtons: card.querySelectorAll('.doc-attach').length,
    documentRows: card.querySelectorAll('.app-document').length,
    textareas: card.querySelectorAll('textarea').length,
  };
}

/* 3. Opening builds the detail once and reveals it; closing puts it away and
      keeps it. Nothing else is rebuilt and nothing is fetched. */
{
  const dom = makeDom();
  const app = load(dom);
  const applications = [makeApplication({ position: 'A' }), makeApplication({ position: 'B' }),
    makeApplication({ position: 'C' })];
  app.setApplications(applications);
  const list = dom.document.querySelector('#application-list');
  applications.forEach((a) => list.appendChild(app.applicationCard(a)));
  const cards = () => list.children.filter((n) => n.classList.contains('card'));
  const order = () => cards().map((n) => n.dataset.applicationId);
  const before = order();
  const nodes = cards();
  const target = applications[1];

  app.toggleApplication(target.id);
  const card = list.children.filter((n) => n.dataset.applicationId === String(target.id))[0];
  const body = card.querySelector('.card-body');
  const toggle = card.querySelector('.card-toggle');
  const opened = {
    hidden: body.hidden,
    aria: toggle.getAttribute('aria-expanded'),
    children: body.children.length,
    cardClass: card.className,
    text: textOf(body),
    inOpenSet: app.openApplications.has(target.id),
  };
  const detailNode = body.children[0];

  app.toggleApplication(target.id);
  const closed = {
    hidden: body.hidden,
    aria: toggle.getAttribute('aria-expanded'),
    children: body.children.length,
    cardClass: card.className,
    sameDetail: body.children[0] === detailNode,
    inOpenSet: app.openApplications.has(target.id),
  };

  results.expand_and_collapse = {
    opened,
    closed,
    orderBefore: before,
    orderAfter: order(),
    sameNodes: cards().length === nodes.length && cards().every((n, i) => n === nodes[i]),
    otherBodiesHidden: cards().filter((n) => n !== card)
      .every((n) => n.querySelector('.card-body').hidden),
    fetches: dom.world.fetches,
  };
}

/* 4. "Mark as Applied" is offered while - and only while - it is Preparation,
      on the closed card as well as inside the open one. */
{
  const dom = makeDom();
  const app = load(dom);
  const list = dom.document.querySelector('#application-list');
  const offered = {};
  STATUSES.forEach((status) => {
    const application = makeApplication({ status,
      is_active: CLOSED.indexOf(status) < 0 });
    const card = list.appendChild(app.applicationCard(application));
    offered[status] = card.querySelectorAll('.mark-applied').length;
    card.remove();
  });
  const preparation = makeApplication({ status: 'Preparation' });
  app.setApplications([preparation]);
  const card = list.appendChild(app.applicationCard(preparation));
  app.toggleApplication(preparation.id);
  results.mark_as_applied = {
    collapsedByStatus: offered,
    inExpandedBody: textOf(card.querySelector('.card-body')).indexOf('Mark as Applied') >= 0,
    neverSubmits: textOf(card).toLowerCase().indexOf('nothing is sent') >= 0,
  };
}

/* 5. The Preparation block reports the package honestly. */
{
  const dom = makeDom();
  const app = load(dom);
  const ready = makeApplication({ status: 'Preparation', document_summary: [
    { kind: 'cv', kind_label: 'CV / Resume', count: 1, filename: 'CV_EN.pdf' },
    { kind: 'cover_letter', kind_label: 'Cover Letter', count: 1, filename: 'Motivation_EN.pdf' },
  ] });
  const bare = makeApplication({ status: 'Preparation' });
  results.preparation_block = {
    ready: textOf(app.preparationBlock(ready)),
    bare: textOf(app.preparationBlock(bare)),
  };
}

/* 6. The documents on an open card: one row per file, the three actions, and
      an honest empty state for a record that never had any. */
{
  const dom = makeDom();
  const app = load(dom);
  const list = dom.document.querySelector('#application-list');

  const withDocs = makeApplication({ status: 'Applied', applied_at: '2026-09-14T08:30:00',
    documents: [
      makeDocument({ id: 11 }),
      makeDocument({ id: 12, document_kind: 'cover_letter', kind_label: 'Cover Letter',
        original_filename: 'Motivation_EN.pdf', source: 'UPLOADED_FOR_APPLICATION' }),
      makeDocument({ id: 13, document_kind: 'certificate', kind_label: 'Certificate / Reference',
        original_filename: 'Reference.pdf', exists: false }),
    ] });
  app.setApplications([withDocs]);
  const card = list.appendChild(app.applicationCard(withDocs));
  app.toggleApplication(withDocs.id);
  const rows = card.querySelectorAll('.app-document');
  results.documents_on_open_card = {
    rows: rows.length,
    kinds: rows.map((r) => textOf(r.querySelector('.doc-kind'))),
    names: rows.map((r) => textOf(r.querySelector('.doc-name'))),
    actions: rows.map((r) => r.querySelector('.doc-actions').children.map((b) => b.textContent)),
    attachButtons: card.querySelectorAll('.doc-attach').length,
    bodyText: textOf(card.querySelector('.card-body')),
  };

  const empty = makeApplication({ status: 'Applied', applied_at: '2026-09-01T08:00:00' });
  app.setApplications([empty]);
  const emptyCard = list.appendChild(app.applicationCard(empty));
  app.toggleApplication(empty.id);
  results.documents_empty_state = {
    text: textOf(emptyCard.querySelector('.documents-block')),
    rows: emptyCard.querySelectorAll('.app-document').length,
    attachButtons: emptyCard.querySelectorAll('.doc-attach').length,
  };
}

/* 7. A closed application is still tracked and still badged, but must not read
      as a live thread. */
{
  const dom = makeDom();
  const app = load(dom);
  const list = dom.document.querySelector('#application-list');
  const tint = {};
  STATUSES.forEach((status) => {
    const application = makeApplication({ status, is_active: CLOSED.indexOf(status) < 0 });
    const card = list.appendChild(app.applicationCard(application));
    tint[status] = { className: card.className,
      badge: textOf(card.querySelector('.badge')) };
    card.remove();
  });
  results.tracked_tint = tint;
}

/* 8. The header toggles the card; a control inside it never does. */
{
  const dom = makeDom();
  const app = load(dom);
  const application = makeApplication({ status: 'Preparation' });
  app.setApplications([application]);
  const list = dom.document.querySelector('#application-list');
  const card = list.appendChild(app.applicationCard(application));
  const head = card.querySelector('.card-head');
  const picker = card.querySelector('.status-picker');
  const title = card.querySelector('.card-title');

  dom.fire(head, 'click', { target: picker });
  const afterControl = app.openApplications.has(application.id);
  dom.fire(head, 'click', { target: title });
  const afterTitle = app.openApplications.has(application.id);
  results.header_toggle = { afterControl, afterTitle,
    controlIsAction: app.isActionTarget(picker) };
}

/* 9. The stage counters are painted from whatever the backend last said. */
{
  const dom = makeDom();
  const app = load(dom);
  app.renderPipeline(STATUSES.map((status, i) => ({ status, count: i === 1 ? 4 : 0 })));
  const pipeline = dom.document.querySelector('#pipeline');
  const first = pipeline.children.map((n) => ({
    status: n.getAttribute('data-stage'),
    count: textOf(n.querySelector('.n')),
  }));
  app.renderPipeline(STATUSES.map((status, i) => ({ status, count: i === 2 ? 9 : 0 })));
  const second = dom.document.querySelector('#pipeline').children.map((n) => ({
    status: n.getAttribute('data-stage'),
    count: textOf(n.querySelector('.n')),
  }));
  results.pipeline_counters = { first, second, fetches: dom.world.fetches };
}

/* 10. A record that was never sent must not show a date that says it was. */
{
  const dom = makeDom();
  const app = load(dom);
  results.applied_line = {
    never: app.appliedLine(makeApplication({ status: 'Preparation' })),
    sent: app.appliedLine(makeApplication({ status: 'Applied',
      applied_at: '2026-09-13T22:07:37' })),
  };
}

process.stdout.write(JSON.stringify(results, null, 2));
