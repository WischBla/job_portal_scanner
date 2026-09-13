'use strict';
/* Runs the real static/app.js against a deliberately small model of the two
 * browser behaviours the Jobs list depends on:
 *
 *   1. a card's position is its position in the document minus the scroll
 *   2. when the document gets shorter than the current scroll position, the
 *      browser clamps the scroll - which is exactly what used to walk the
 *      Jobs list back to the top, one Ignore at a time
 *
 * The model is not a DOM. It is the geometry, which is the part the fix
 * reasons about. Results are printed as JSON for tests/test_job_card_ux.py.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = path.join(__dirname, '..', '..', 'static', 'app.js');
const HEADER = 100;          // everything above the list
const CARD = 660;            // a job card, roughly life-size
const VIEWPORT = 800;

function makeWorld() {
  const world = {
    cards: [],
    spacer: null,
    scrollY: 0,
    scrollListeners: [],
    spacerHeight() { return this.spacer ? (parseFloat(this.spacer.style.height) || 0) : 0; },
    height() {
      return HEADER + this.cards.reduce((n, c) => n + CARD + c.extra, 0) + this.spacerHeight();
    },
    max() { return Math.max(0, this.height() - VIEWPORT); },
    clamp() { this.scrollY = Math.min(this.scrollY, this.max()); },
    documentTop(card) {
      let top = HEADER;
      for (const other of this.cards) {
        if (other === card) return top;
        top += CARD + other.extra;
      }
      return top;
    },
    scroll(to) {                       // what the user does
      this.scrollY = Math.min(Math.max(0, to), this.max());
      this.scrollListeners.slice().forEach((fn) => fn());
    },
  };

  const card = (id) => ({
    id,
    extra: 0,
    className: 'card',
    dataset: { jobId: String(id) },
    classList: { contains: (name) => name === 'card', toggle() {}, add() {}, remove() {} },
    getBoundingClientRect() {
      return { top: world.documentTop(this) - world.scrollY, height: CARD + this.extra };
    },
    get nextElementSibling() {
      const i = world.cards.indexOf(this);
      if (i < 0) return null;
      if (i + 1 < world.cards.length) return world.cards[i + 1];
      return world.spacer;             // the spacer really is the last child
    },
    get previousElementSibling() {
      const i = world.cards.indexOf(this);
      return i > 0 ? world.cards[i - 1] : null;
    },
    remove() {
      const i = world.cards.indexOf(this);
      if (i >= 0) world.cards.splice(i, 1);
      world.clamp();                   // the browser does this, not the app
    },
    replaceWith() { /* height changes are modelled through `extra` */ },
  });

  world.seed = (count) => {
    world.cards = [];
    for (let i = 1; i <= count; i += 1) world.cards.push(card(i));
    return world;
  };
  return world;
}

function makeContext(world) {
  const node = (tag) => ({
    tagName: String(tag || '').toUpperCase(),
    type: '',
    className: '',
    textContent: '',
    hidden: false,
    isConnected: true,
    style: {},
    firstChild: null,
    children: [],
    dataset: {},
    classList: {
      contains(name) { return String(this.owner || '').split(' ').indexOf(name) >= 0; },
      toggle() {}, add() {}, remove() {},
    },
    addEventListener() {},
    setAttribute(key, value) { this[key] = value; },
    appendChild(child) { this.children.push(child); return child; },
    removeChild() {},
    insertBefore(child) { this.children.push(child); return child; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    replaceWith() {},
    remove() { this.isConnected = false; },
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
  });

  const list = node('div');
  list.appendChild = (child) => {
    if (child.className === 'list-spacer') { world.spacer = child; child.isConnected = true; }
    return child;
  };

  const CARD_SELECTOR = /data-job-id="(\d+)"/;
  const document = {
    documentElement: { get scrollHeight() { return world.height(); } },
    createElement: (tag) => {
      const made = node(tag);
      made.remove = () => { made.isConnected = false; if (world.spacer === made) world.spacer = null; };
      return made;
    },
    addEventListener() {},
    querySelector(selector) {
      const match = CARD_SELECTOR.exec(selector || '');
      if (match) return world.cards.find((c) => c.dataset.jobId === match[1]) || null;
      if (selector === '#job-list > .list-spacer') return world.spacer;
      if (selector === '#job-list') return list;
      return node('div');
    },
    querySelectorAll() { return []; },
  };

  const window = {
    get scrollY() { return world.scrollY; },
    innerHeight: VIEWPORT,
    scrollTo(x, y) { world.scrollY = Math.min(Math.max(0, y), world.max()); },
    addEventListener(type, fn) { if (type === 'scroll') world.scrollListeners.push(fn); },
    removeEventListener(type, fn) {
      if (type !== 'scroll') return;
      const i = world.scrollListeners.indexOf(fn);
      if (i >= 0) world.scrollListeners.splice(i, 1);
    },
  };

  const context = { document, window, location: { hash: '#jobs' }, console,
    setTimeout, clearTimeout, fetch: () => Promise.reject(new Error('offline')) };
  context.globalThis = context;
  vm.createContext(context);

  const source = fs.readFileSync(APP_JS, 'utf8')
    + '\n;globalThis.__t = { el, state, cardNode, anchor, releaseAnchor, keepingPosition,'
    + ' neighbourId, adjustCounts, maxScroll, removeCard, holdPageHeight, releasePageHeight };\n';
  vm.runInContext(source, context, { filename: 'app.js' });
  return context.__t;
}

// --------------------------------------------------------------------------
const results = {};

// 1. The regression itself: removing a card at the bottom moves the page,
//    because the document got shorter and the browser clamped the scroll.
{
  const world = makeWorld().seed(5);
  makeContext(world);
  world.scrollY = world.max();
  const before = world.scrollY;
  world.cards[4].remove();
  results.unanchored_removal_moves_the_page = { before, after: world.scrollY };
}

// 2. Anchored on the neighbouring card, the same removal leaves the page
//    visually still: the neighbour keeps its exact pixel row.
{
  const world = makeWorld().seed(5);
  const app = makeContext(world);
  world.scrollY = world.max();
  const victim = world.cards[3];
  const neighbour = app.neighbourId(victim);
  const topBefore = world.cards[4].getBoundingClientRect().top;
  app.keepingPosition(neighbour, () => victim.remove());
  results.anchored_removal_keeps_the_neighbour_still = {
    neighbourId: neighbour, topBefore,
    topAfter: world.cards[world.cards.length - 1].getBoundingClientRect().top,
  };
}

// 3a. Four Ignores in a row at the bottom of a short list. Unanchored this is
//     the 2626 -> 1971 -> 1315 -> 660 -> 0 walk to the top measured in the
//     real browser. Through removeCard the page does not move at all: the
//     spacer holds the height that the removed cards used to provide.
{
  const bare = makeWorld().seed(5);
  makeContext(bare);
  bare.scrollY = bare.max();
  const bareTrail = [bare.scrollY];
  for (let i = 0; i < 4; i += 1) {
    const cards = bare.cards;
    const target = cards.find((c) => c.getBoundingClientRect().top > 0) || cards[cards.length - 1];
    target.remove();
    bareTrail.push(bare.scrollY);
  }

  const world = makeWorld().seed(5);
  const app = makeContext(world);
  world.scrollY = world.max();
  const trail = [world.scrollY];
  const neighbourDrift = [];
  for (let i = 0; i < 4; i += 1) {
    const cards = world.cards;
    const target = cards.find((c) => c.getBoundingClientRect().top > 0) || cards[cards.length - 1];
    const keep = app.neighbourId(target);
    const keepNode = app.cardNode(keep);
    const topBefore = keepNode ? keepNode.getBoundingClientRect().top : null;
    app.removeCard(target, keep);
    const after = app.cardNode(keep);
    if (topBefore !== null && after) {
      neighbourDrift.push(after.getBoundingClientRect().top - topBefore);
    }
    trail.push(world.scrollY);
  }
  results.repeated_ignores_at_the_bottom = {
    unanchored: bareTrail, anchored: trail, neighbourDrift,
  };
}

// 3b. The accident: ignoring a card in the middle of the list used to pull the
//     next card - and its Ignore button - straight up under the cursor that
//     had just clicked. Anchored, the next card does not move at all.
{
  const bare = makeWorld().seed(12);
  makeContext(bare);
  bare.scrollY = 2000;
  const bareVictim = bare.cards.find((c) => c.getBoundingClientRect().top > 0);
  const bareNext = bareVictim.nextElementSibling;
  const bareBefore = bareNext.getBoundingClientRect().top;
  bareVictim.remove();
  const unanchoredJump = bareNext.getBoundingClientRect().top - bareBefore;

  const world = makeWorld().seed(12);
  const app = makeContext(world);
  world.scrollY = 2000;
  const victim = world.cards.find((c) => c.getBoundingClientRect().top > 0);
  const keep = app.neighbourId(victim);
  const keepBefore = app.cardNode(keep).getBoundingClientRect().top;
  app.removeCard(victim, keep);
  results.ignore_in_the_middle = {
    unanchoredJump,
    anchoredJump: app.cardNode(keep).getBoundingClientRect().top - keepBefore,
  };
}

// 3c. The spacer is temporary: it lets go as soon as it can do so without
//     moving anything, which is the moment the user scrolls away.
{
  const world = makeWorld().seed(5);
  const app = makeContext(world);
  world.scrollY = world.max();
  const target = world.cards[4];
  app.removeCard(target, app.neighbourId(target));
  const held = world.spacerHeight();
  world.scroll(0);                       // the user scrolls up
  results.spacer_lets_go = { heldAfterIgnore: held, heldAfterScrolling: world.spacerHeight() };
}

// 4. A verdict makes its own card taller (the reason picker appears). The card
//    the user is reading must not move; only what is below it may.
{
  const world = makeWorld().seed(6);
  const app = makeContext(world);
  world.scrollY = 1500;
  const target = world.cards[2];
  const topBefore = target.getBoundingClientRect().top;
  app.keepingPosition(3, () => { target.extra = 61; });
  results.card_growth_keeps_the_card_still = {
    topBefore, topAfter: target.getBoundingClientRect().top,
  };
}

// 5. With no card to anchor on, the plain scroll position is restored.
{
  const world = makeWorld().seed(6);
  const app = makeContext(world);
  world.scrollY = 1200;
  app.keepingPosition(null, () => { world.scrollY = 0; });
  results.scroll_fallback = { scrollY: world.scrollY };
}

// 6. Buttons never submit unless something explicitly asks them to.
{
  const app = makeContext(makeWorld().seed(1));
  results.button_types = {
    plain: app.el('button', { text: 'YES' }).type,
    withClass: app.el('button', { class: 'small ghost danger', text: 'Ignore' }).type,
    explicit: app.el('button', { type: 'submit' }).type,
    nonButton: app.el('div', {}).type,
  };
}

// 7. Count bookkeeping for an in-place ignore / undo.
{
  const app = makeContext(makeWorld().seed(1));
  app.state.counts = { total: 10, ignored: 2, saved: 1, excellent: 1, strong: 3 };
  const job = { state: 'SAVED', classification: 'Exceptional' };
  app.adjustCounts(job, -1);
  const afterIgnore = Object.assign({}, app.state.counts);
  app.adjustCounts(job, 1);
  results.counts = { afterIgnore, afterUndo: Object.assign({}, app.state.counts) };
}

process.stdout.write(JSON.stringify(results, null, 1));
