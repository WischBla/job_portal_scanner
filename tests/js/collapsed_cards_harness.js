'use strict';
/* Runs the real static/app.js against a small but genuine DOM, so the things
 * a collapsed Jobs list promises can actually be measured rather than grepped:
 *
 *   - a card is closed when it is built, and its body is not there yet
 *   - opening one builds the detail once and reveals it
 *   - closing it puts it away again without throwing the detail away
 *   - neither touches any other card, the order, or the network
 *   - a tracked application tints its card, and a closed one does not get the
 *     tint that means "still live"
 *   - opening and closing keep the card on the pixel row it was on, which is
 *     the same promise the Ignore fix makes
 *
 * The DOM is deliberately small: elements, parents, classes, attributes,
 * listeners - plus the one browser behaviour the Jobs list has always had to
 * reason about, which is that the scroll is clamped when the document becomes
 * too short for it. Results are printed as JSON for tests/test_job_card_ux.py.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = path.join(__dirname, '..', '..', 'static', 'app.js');
const HEADER = 100;          // everything above the list
const CLOSED = 120;          // a closed card
const BODY = 540;            // what opening one adds
const VIEWPORT = 800;

// --------------------------------------------------------------- mini DOM
function makeDom() {
  const world = {
    list: null,
    y: 0,
    innerHeight: VIEWPORT,
    fetches: 0,
    rows() { return world.list ? world.list.children : []; },
    heightOf(node) {
      if (node.classList.contains('card')) {
        const body = node.querySelector('.card-body');
        return CLOSED + (body && !body.hidden ? BODY : 0);
      }
      if (node.classList.contains('list-spacer')) return parseFloat(node.style.height) || 0;
      return 0;
    },
    height() {
      return HEADER + world.rows().reduce((n, node) => n + world.heightOf(node), 0);
    },
    max() { return Math.max(0, world.height() - VIEWPORT); },
    top(node) {
      let top = HEADER;
      for (const row of world.rows()) {
        if (row === node) return top;
        top += world.heightOf(row);
      }
      return top;
    },
    /* The browser does this, not the app: a document that no longer reaches
       the current scroll position drags the page up. */
    clamp() { world.y = Math.min(world.y, world.max()); },
  };

  function element(tag) {
    const node = {
      tagName: String(tag || '').toUpperCase(),
      type: '',
      className: '',
      textContent: '',
      innerHTML: '',
      value: '',
      selected: false,
      checked: false,
      isConnected: false,
      style: {},
      dataset: {},
      attrs: {},
      listeners: {},
      children: [],
      parentNode: null,
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
    Object.defineProperty(node, 'nextElementSibling', {
      get: () => {
        const kids = node.parentNode ? node.parentNode.children : [];
        return kids[kids.indexOf(node) + 1] || null;
      },
    });
    Object.defineProperty(node, 'previousElementSibling', {
      get: () => {
        const kids = node.parentNode ? node.parentNode.children : [];
        const i = kids.indexOf(node);
        return i > 0 ? kids[i - 1] : null;
      },
    });
    // Hiding or revealing a card body changes the height of the document, and
    // the browser reacts to that immediately.
    let hidden = false;
    Object.defineProperty(node, 'hidden', {
      get: () => hidden,
      set: (value) => { hidden = !!value; world.clamp(); },
    });
    node.setAttribute = (key, value) => {
      node.attrs[key] = String(value);
      if (key === 'class') node.className = String(value);
      if (key === 'id') node.id = String(value);
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
      child.isConnected = true;
      node.children.push(child);
      world.clamp();
      return child;
    };
    node.insertBefore = (child, before) => {
      if (child.parentNode) child.parentNode.removeChild(child);
      child.parentNode = node;
      child.isConnected = true;
      const at = before ? node.children.indexOf(before) : -1;
      if (at < 0) node.children.push(child);
      else node.children.splice(at, 0, child);
      world.clamp();
      return child;
    };
    node.removeChild = (child) => {
      const at = node.children.indexOf(child);
      if (at >= 0) node.children.splice(at, 1);
      child.parentNode = null;
      child.isConnected = false;
      world.clamp();
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
    node.getBoundingClientRect = () => {
      if (node.parentNode === world.list) {
        return { top: world.top(node) - world.y, height: world.heightOf(node) };
      }
      return { top: 0, left: 0, width: 0, height: 0 };
    };
    node.click = () => fire(node, 'click', { target: node });
    node.focus = () => {};
    return node;
  }

  /* One compound selector: tag, #id, .class and [attr="value"]. A descendant
     or child combinator is honoured by matching only its last step, which is
     enough here because every id in this app is unique. */
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
  root.isConnected = true;
  const list = element('div');
  list.setAttribute('id', 'job-list');
  list.className = 'list';
  root.appendChild(list);
  world.list = list;

  const document = {
    documentElement: { get scrollHeight() { return world.height(); } },
    body: root,
    createElement: element,
    addEventListener() {},
    querySelector: (selector) => (matches(list, selector) ? list : find(root, selector)[0] || null),
    querySelectorAll: (selector) => find(root, selector),
  };

  const window = {
    get scrollY() { return world.y; },
    innerHeight: VIEWPORT,
    scrollTo(x, y) { world.y = Math.min(Math.max(0, y), world.max()); },
    addEventListener() {},
    removeEventListener() {},
    open() {},
  };

  return { world, document, window, element, fire, find, matches };
}

function load(dom) {
  const context = {
    document: dom.document,
    window: dom.window,
    location: { hash: '#jobs' },
    console,
    setTimeout,
    clearTimeout,
    fetch: () => { dom.world.fetches += 1; return Promise.reject(new Error('offline')); },
  };
  context.globalThis = context;
  vm.createContext(context);
  const source = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { el, state, jobCard, renderJobs, toggleJob, cardNode,'
    + ' applicationBadge, isActionTarget, trackedClass, keepingPlace };\n';
  vm.runInContext(source, context, { filename: 'app.js' });
  return context.__t;
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
function makeJob(overrides) {
  counter += 1;
  return Object.assign({
    id: counter,
    title: 'Principal Technical Program Manager',
    company: 'AWS',
    location: 'Zurich',
    work_model: 'Hybrid',
    age: '3 days ago',
    score: 82,
    base_score: 86,
    classification: 'Strong',
    band: 'Strong fit',
    personal_fit: 82,
    operating_style: { classification: 'BALANCED', adjustment: -2, detail: '' },
    career_direction: { classification: 'ALIGNED', adjustment: -2, detail: '' },
    evidence: 'HIGH',
    evidence_detail: 'Full description available',
    confidence: 'HIGH',
    enrichment_state: 'ENRICHED',
    enrichment_source: '',
    enrichment_detail: '',
    has_description: true,
    provisional: false,
    high_potential: false,
    needs_details: false,
    reasons: ['Owns cloud platform engineering'],
    concerns: ['Heavy stakeholder exposure'],
    seniority: 'Principal',
    source: 'amazon_jobs',
    discovered_via: '',
    url: 'https://example.test/1',
    state: 'NEW',
    is_new: true,
    feedback: '',
    feedback_reason: '',
    compensation: null,
    application_id: null,
    has_application: false,
    application_status: '',
    application_active: false,
  }, overrides || {});
}

// --------------------------------------------------------------------------
const results = {};

/* 1. Built closed: no detail on screen, and the control says so. */
{
  const dom = makeDom();
  const app = load(dom);
  const job = makeJob({ state: 'SAVED', needs_details: true, high_potential: true });
  const card = dom.world.list.appendChild(app.jobCard(job));
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
    expandedSet: app.state.expanded.size,
    headText: textOf(head),
    cardText: textOf(card),
  };
}

/* 2. What a closed card is allowed to say - and what it must not. */
{
  const dom = makeDom();
  const app = load(dom);
  const job = makeJob({ state: 'SAVED', needs_details: true,
    has_application: true, application_status: 'Interview', application_active: true,
    reasons: ['Owns cloud platform engineering'], concerns: ['Heavy stakeholder exposure'] });
  const card = dom.world.list.appendChild(app.jobCard(job));
  results.collapsed_summary = { text: textOf(card) };
}

/* 3. Opening builds the detail once and reveals it; closing puts it away
      again and keeps it. Nothing else is rebuilt, re-ordered or fetched. */
{
  const dom = makeDom();
  const app = load(dom);
  app.state.jobs = [makeJob({ title: 'A' }), makeJob({ title: 'B' }), makeJob({ title: 'C' })];
  app.renderJobs();
  const cards = () => dom.world.list.children.filter((n) => n.classList.contains('card'));
  const order = () => cards().map((n) => n.dataset.jobId);
  const before = order();
  const nodes = cards();
  const job = app.state.jobs[1];

  app.toggleJob(job);
  const card = app.cardNode(job.id);
  const body = card.querySelector('.card-body');
  const toggle = card.querySelector('.card-toggle');
  const opened = {
    hidden: body.hidden,
    aria: toggle.getAttribute('aria-expanded'),
    children: body.children.length,
    cardClass: card.className,
    text: textOf(body),
    inExpandedSet: app.state.expanded.has(job.id),
  };
  const detailNode = body.children[0];

  app.toggleJob(job);
  const closed = {
    hidden: body.hidden,
    aria: toggle.getAttribute('aria-expanded'),
    children: body.children.length,
    cardClass: card.className,
    sameDetail: body.children[0] === detailNode,   // built once, not rebuilt
    inExpandedSet: app.state.expanded.has(job.id),
  };

  results.expand_and_collapse = {
    opened,
    closed,
    orderBefore: before,
    orderAfter: order(),
    sameNodes: cards().length === nodes.length && cards().every((n, i) => n === nodes[i]),
    fetches: dom.world.fetches,
  };
}

/* 4. The header toggles; a control inside it never does. */
{
  const dom = makeDom();
  const app = load(dom);
  app.state.jobs = [makeJob({})];
  app.renderJobs();
  const job = app.state.jobs[0];
  const card = app.cardNode(job.id);
  const head = card.querySelector('.card-head');
  const meta = card.querySelector('.card-meta');
  const chevron = card.querySelector('.chevron');

  dom.fire(meta, 'click', { target: meta });         // a safe part of the header
  const afterMeta = app.state.expanded.has(job.id);
  dom.fire(meta, 'click', { target: meta });         // and back
  const afterSecondMeta = app.state.expanded.has(job.id);

  // The chevron is inside the toggle button: its own handler opens the card,
  // and the header must not then close it again.
  dom.fire(chevron, 'click', { target: chevron });
  const afterChevron = app.state.expanded.has(job.id);

  // A button that is not the toggle must leave the card alone.
  const other = dom.element('button');
  head.appendChild(other);
  const beforeOther = app.state.expanded.has(job.id);
  dom.fire(other, 'click', { target: other });

  results.header_click = {
    afterMeta, afterSecondMeta, afterChevron,
    unchangedByOtherButton: app.state.expanded.has(job.id) === beforeOther,
    isActionForButton: app.isActionTarget(other),
    isActionForChevron: app.isActionTarget(chevron),
    isActionForMeta: app.isActionTarget(meta),
  };
}

/* 5. The application-tracking treatment, for every status the pipeline has. */
{
  const dom = makeDom();
  const app = load(dom);
  const statuses = ['Preparation', 'Applied', 'Screening', 'Interview', 'Final', 'Offer',
    'Rejected', 'Withdrawn'];
  const closed = ['Rejected', 'Withdrawn'];
  const cards = {};
  statuses.forEach((status) => {
    const job = makeJob({ has_application: true, application_status: status,
      application_active: closed.indexOf(status) < 0 });
    const card = dom.world.list.appendChild(app.jobCard(job));
    cards[status] = {
      className: card.className,
      active: card.classList.contains('tracked-active'),
      closed: card.classList.contains('tracked-closed'),
      tracked: card.classList.contains('tracked'),
      badge: textOf(card.querySelector('.badge.application')),
      bodyHidden: card.querySelector('.card-body').hidden,
    };
  });
  const plain = dom.world.list.appendChild(app.jobCard(makeJob({})));
  results.application_treatment = {
    cards,
    untracked: {
      className: plain.className,
      tracked: plain.classList.contains('tracked'),
      badge: plain.querySelector('.badge.application'),
    },
  };
}

/* 6. Opening a card keeps it on the pixel row it was on. */
{
  const dom = makeDom();
  const app = load(dom);
  app.state.jobs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(() => makeJob({}));
  app.renderJobs();
  dom.window.scrollTo(0, 400);
  const job = app.state.jobs[5];
  const card = app.cardNode(job.id);
  const topBefore = card.getBoundingClientRect().top;
  app.toggleJob(job);
  results.expand_keeps_the_card_still = {
    topBefore, topAfter: card.getBoundingClientRect().top,
  };
}

/* 7. Closing one at the bottom of the list is the case that used to walk the
      page to the top: the document gets shorter and the browser clamps. */
{
  const bare = makeDom();
  load(bare);
  const bareApp = load(bare);
  bareApp.state.jobs = [1, 2, 3].map(() => makeJob({}));
  bareApp.renderJobs();
  const bareJob = bareApp.state.jobs[2];
  bareApp.toggleJob(bareJob);                       // open it
  bare.window.scrollTo(0, bare.world.max());        // read it at the bottom
  const bareCard = bareApp.cardNode(bareJob.id);
  const bareTopBefore = bareCard.getBoundingClientRect().top;
  const bareScrollBefore = bare.world.y;
  bareCard.querySelector('.card-body').hidden = true;   // collapse, unanchored
  const unanchored = {
    scrollBefore: bareScrollBefore, scrollAfter: bare.world.y,
    drift: bareCard.getBoundingClientRect().top - bareTopBefore,
  };

  const dom = makeDom();
  const app = load(dom);
  app.state.jobs = [1, 2, 3].map(() => makeJob({}));
  app.renderJobs();
  const job = app.state.jobs[2];
  app.toggleJob(job);
  dom.window.scrollTo(0, dom.world.max());
  const card = app.cardNode(job.id);
  const topBefore = card.getBoundingClientRect().top;
  const scrollBefore = dom.world.y;
  app.toggleJob(job);                               // collapse, through the app
  results.collapse_at_the_bottom = {
    unanchored,
    anchored: {
      scrollBefore, scrollAfter: dom.world.y,
      drift: card.getBoundingClientRect().top - topBefore,
      heldBySpacer: (() => {
        const spacer = dom.world.list.querySelector('.list-spacer');
        return spacer ? parseFloat(spacer.style.height) || 0 : 0;
      })(),
    },
  };
}

/* 8. A closed card is the cheap one: opening ten of them is what costs DOM,
      and the list does not pay for it until the user asks. */
{
  const dom = makeDom();
  const app = load(dom);
  app.state.jobs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(() => makeJob({}));
  app.renderJobs();
  const count = (node) => node.children.reduce((n, c) => n + 1 + count(c), 0);
  const collapsed = count(dom.world.list);
  app.state.jobs.forEach((job) => app.toggleJob(job));
  results.dom_weight = { collapsed, expanded: count(dom.world.list),
    fetches: dom.world.fetches };
}

/* 9. An in-place card swap - what a verdict or a Save does - keeps an open
      card open and a closed card closed. */
{
  const dom = makeDom();
  const app = load(dom);
  app.state.jobs = [makeJob({}), makeJob({})];
  app.renderJobs();
  const open = app.state.jobs[0];
  const shut = app.state.jobs[1];
  app.toggleJob(open);
  const rebuiltOpen = app.jobCard(open);
  const rebuiltShut = app.jobCard(shut);
  results.rebuild_keeps_open_state = {
    open: !rebuiltOpen.querySelector('.card-body').hidden,
    openClass: rebuiltOpen.className,
    shut: rebuiltShut.querySelector('.card-body').hidden,
  };
}

process.stdout.write(JSON.stringify(results, null, 1));
