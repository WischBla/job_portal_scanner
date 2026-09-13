/* Job Assistant - vanilla JS, four views, no framework.
   The backend decides what a job card says; this file only renders it. */

'use strict';

const state = { view: 'jobs', jobs: [], counts: {}, config: null, profile: null,
  feedbackReasons: [] };

/* Why a job was a "no". Recorded for later calibration; nothing retrains. */
const FEEDBACK_REASONS = ['Too operational', 'Too junior', 'Too commercial',
  'Too much consulting', 'Wrong location', 'Insufficient technical responsibility',
  'Insufficient leadership scope', 'Compensation concern', 'Other'];

/* ------------------------------------------------------------------ util */
const $ = (sel, root) => (root || document).querySelector(sel);
const el = (tag, attrs, children) => {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([key, value]) => {
    if (value === null || value === undefined || value === false) return;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  });
  (children || []).forEach((child) => child && node.appendChild(child));
  return node;
};
const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); return node; };

async function api(path, options) {
  const opts = Object.assign({ headers: {} }, options || {});
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const response = await fetch(path, opts);
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (err) { data = { detail: text }; }
  if (!response.ok) throw new Error((data && data.detail) || response.statusText);
  return data;
}

let toastTimer = null;
function toast(message, isError) {
  const node = $('#toast');
  node.textContent = message;
  node.className = 'toast' + (isError ? ' error' : '');
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, isError ? 7000 : 3500);
}

function dialog(title, body) {
  $('#dialog-title').textContent = title;
  clear($('#dialog-body')).appendChild(body);
  $('#dialog').hidden = false;
}
function closeDialog() { $('#dialog').hidden = true; }

function field(label, control, hint) {
  return el('div', { class: 'field' }, [
    el('label', { text: label }),
    control,
    hint ? el('div', { class: 'hint', text: hint }) : null,
  ]);
}
function input(name, value, type) {
  return el('input', { type: type || 'text', name, value: value === null || value === undefined ? '' : value });
}
function textarea(name, value) { return el('textarea', { name, text: value || '' }); }
function checkbox(name, checked, label) {
  const box = el('input', { type: 'checkbox', name });
  box.checked = !!checked;
  return el('label', { class: 'check' }, [box, el('span', { text: label })]);
}
function selectBox(name, value, options) {
  const node = el('select', { name });
  options.forEach((option) => {
    const [val, text] = Array.isArray(option) ? option : [option, option];
    const opt = el('option', { value: val, text });
    if (String(val) === String(value)) opt.selected = true;
    node.appendChild(opt);
  });
  return node;
}
function readForm(root) {
  const out = {};
  root.querySelectorAll('input, textarea, select').forEach((node) => {
    if (!node.name) return;
    out[node.name] = node.type === 'checkbox' ? node.checked : node.value;
  });
  return out;
}

/* ============================== JOBS ============================== */
function compBlock(comp) {
  if (!comp) return el('div', { class: 'comp muted', text: 'Estimate switched off in Config.' });
  const chf = (n) => "CHF " + Math.round(n / 1000) + 'k';
  const rows = [el('span', { class: 'amount', text: comp.display })];
  rows.push(el('span', { class: 'row', text: 'Base ' + chf(comp.base_min) + '-' + chf(comp.base_max) }));
  if (comp.bonus_max) rows.push(el('span', { class: 'row', text: 'Bonus ' + chf(comp.bonus_min) + '-' + chf(comp.bonus_max) }));
  if (comp.equity) rows.push(el('span', { class: 'row', text: 'Equity/LTI: ' + comp.equity }));
  rows.push(el('span', { class: 'conf', text: 'Confidence: ' + comp.confidence + ' (' + comp.basis + ')' }));
  return el('div', { class: 'comp' }, rows);
}

function jobCard(job) {
  const cls = job.classification.toLowerCase().replace(/\s+/g, '-');
  const meta = [job.company, job.location, job.work_model];
  if (job.age) meta.push(job.age);

  const head = el('div', { class: 'card-head' }, [
    el('div', { class: 'score ' + cls }, [
      el('div', { class: 'value', text: String(job.score) }),
      el('div', { class: 'class', text: job.classification }),
    ]),
    el('div', { class: 'card-title' }, [
      el('h3', {}, [
        el('span', { text: job.title }),
        job.is_new ? el('span', { class: 'badge new', text: 'new' }) : null,
        job.state === 'SAVED' ? el('span', { class: 'badge', text: 'saved' }) : null,
        job.state === 'APPLIED' ? el('span', { class: 'badge', text: 'applied' }) : null,
      ]),
      el('div', { class: 'card-meta', html: meta.filter(Boolean).map(escapeHtml).join('<span class="sep">/</span>') }),
    ]),
  ]);

  const detail = el('div', { class: 'detail' }, [
    el('div', { class: 'block' }, [
      el('h4', { text: 'Why it matches' }),
      el('ul', {}, (job.reasons.length ? job.reasons : ['No explicit reasons recorded.'])
        .map((r) => el('li', { text: r }))),
    ]),
    el('div', { class: 'block' }, [
      el('h4', { text: 'Potential concerns' }),
      el('ul', {}, (job.concerns.length ? job.concerns : ['None recorded.'])
        .map((c) => el('li', { text: c }))),
    ]),
    el('div', { class: 'block' }, [
      el('h4', { text: 'Estimated compensation' }),
      compBlock(job.compensation),
    ]),
    el('div', { class: 'block' }, [
      el('h4', { text: 'Details' }),
      el('div', { class: 'kv', html: [
        job.seniority ? 'Seniority: <b>' + escapeHtml(job.seniority) + '</b>' : '',
        job.source ? 'Source: <b>' + escapeHtml(job.source) + '</b>' : '',
        job.office_days ? 'Office days: <b>' + job.office_days + '</b>' : '',
      ].filter(Boolean).join('<br>') }),
    ]),
  ]);

  const verdict = el('div', { class: 'verdict' }, [
    el('span', { class: 'verdict-label', text: 'Your verdict' }),
    ...['YES', 'MAYBE', 'NO'].map((value) => el('button', {
      class: 'small verdict-btn' + (job.feedback === value ? ' on' : ''),
      text: value,
      onclick: () => setFeedback(job, job.feedback === value ? '' : value),
    })),
    job.feedback === 'NO'
      ? selectBox('feedback_reason_' + job.id, job.feedback_reason,
          [['', 'Reason (optional)']].concat(FEEDBACK_REASONS.map((r) => [r, r])))
      : null,
    job.feedback && job.feedback !== 'NO'
      ? el('span', { class: 'muted', text: 'Saved for later calibration.' }) : null,
  ]);
  if (job.feedback === 'NO') {
    const picker = verdict.querySelector('select');
    picker.addEventListener('change', () => setFeedback(job, 'NO', picker.value));
  }

  const actions = el('div', { class: 'card-actions' }, [
    el('button', { class: 'small', onclick: () => window.open(job.url, '_blank', 'noopener') , text: 'Open job' }),
    el('button', { class: 'small', onclick: () => setJobState(job.id, job.state === 'SAVED' ? 'SEEN' : 'SAVED'),
      text: job.state === 'SAVED' ? 'Unsave' : 'Save' }),
    el('button', { class: 'small primary', onclick: () => applyToJob(job), text: 'Apply' }),
    el('button', { class: 'small', onclick: () => showAnalysis(job), text: 'Analysis' }),
    el('span', { class: 'spacer' }),
    el('button', { class: 'small ghost', onclick: () => addToApplications(job), text: 'Track application' }),
    el('button', { class: 'small ghost danger', onclick: () => setJobState(job.id, 'IGNORED'), text: 'Ignore' }),
  ]);

  return el('div', { class: 'card' + (job.state === 'IGNORED' ? ' ignored' : '') },
    [head, detail, verdict, actions]);
}

function escapeHtml(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/* Timestamps in Config are for orientation, not forensics: "today" beats an
   ISO string nobody reads. */
function shortStamp(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  const stamp = new Date(text.replace(' ', 'T'));
  if (isNaN(stamp.getTime())) return text.slice(0, 16).replace('T', ' ');
  const days = Math.floor((Date.now() - stamp.getTime()) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return days + ' days ago';
  return stamp.toISOString().slice(0, 10);
}

/* A verdict is stored and nothing else: no rule is rewritten and no job is
   removed, so the list the user is looking at does not move under them. */
async function setFeedback(job, verdict, reason) {
  try {
    const updated = await api('/api/jobs/' + job.id + '/feedback', {
      method: 'POST', body: { verdict, reason: reason || '' } });
    Object.assign(job, { feedback: updated.feedback, feedback_reason: updated.feedback_reason });
    renderJobs();
    toast(verdict ? 'Noted: ' + verdict + '. Stored for later calibration only.' : 'Verdict cleared.');
  } catch (err) { toast(err.message, true); }
}

function renderJobs() {
  const list = clear($('#job-list'));
  if (!state.jobs.length) {
    list.appendChild(el('div', { class: 'empty', text: 'No jobs yet. Click "Scan for new jobs".' }));
    return;
  }
  state.jobs.forEach((job) => list.appendChild(jobCard(job)));
}

async function loadJobs() {
  const data = await api('/api/jobs');
  state.jobs = data.jobs;
  state.counts = data.counts;
  renderJobs();
  const counts = data.counts;
  const scan = data.last_scan;
  const parts = [counts.total + ' opportunities', counts.excellent + ' excellent',
    counts.strong + ' strong', counts.saved + ' saved'];
  if (scan && scan.finished_at) parts.push('last scan ' + scan.finished_at.replace('T', ' ').replace('Z', ''));
  $('#job-summary').textContent = parts.join('  ·  ');
}

async function setJobState(jobId, newState) {
  try {
    await api('/api/jobs/' + jobId + '/state', { method: 'POST', body: { state: newState } });
    await loadJobs();
  } catch (err) { toast(err.message, true); }
}

async function scan() {
  const button = $('#scan-btn');
  button.disabled = true;
  $('#scan-status').textContent = 'Scanning Swiss sources...';
  try {
    const result = await api('/api/scan', { method: 'POST' });
    $('#scan-status').textContent = result.matched_count + ' relevant, ' +
      result.new_count + ' new' +
      (result.source_failure_count ? ', ' + result.source_failure_count + ' source failure(s)' : '');
    await loadJobs();
    renderScanSummary(result);
  } catch (err) {
    $('#scan-status').textContent = '';
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}

/* What the last scan actually did. Seven numbers, not a dashboard. */
function renderScanSummary(result) {
  const rows = [
    ['Sources scanned', result.sources_scanned],
    ['Companies scanned', result.companies_scanned],
    ['Jobs fetched', result.fetched_count],
    ['Swiss eligible', result.swiss_eligible_count],
    ['Relevant', result.matched_count],
    ['New', result.new_count],
    ['Source failures', result.source_failure_count],
  ];
  const node = clear($('#scan-summary'));
  node.hidden = false;
  node.appendChild(el('table', {}, [el('tbody', {}, rows.map(([label, value]) =>
    el('tr', {}, [el('td', { text: label + ':' }), el('td', { text: String(value === undefined ? 0 : value) })])))]));
  (result.errors || []).forEach((error) => {
    node.appendChild(el('div', { class: 'scan-failure',
      text: error.source + ' - ' + error.error }));
  });
}

async function showAnalysis(job) {
  const body = el('div', {}, [el('p', { class: 'muted', text: 'Analysing...' })]);
  dialog(job.title + ' - ' + job.company, body);
  try {
    const data = await api('/api/jobs/' + job.id + '/analyse', { method: 'POST', body: { force: false } });
    const a = data.analysis;
    clear(body);
    const source = a._source === 'template'
      ? 'Deterministic analysis (AI is off or unavailable).'
      : 'AI analysis via ' + (a._provider || a._source) + (a._model ? ' / ' + a._model : '') + '.';
    body.appendChild(el('div', { class: 'notice', text: source + (a._error ? ' ' + a._error : '') }));
    const section = (title, content) => {
      if (!content || (Array.isArray(content) && !content.length)) return;
      body.appendChild(el('h2', { text: title }));
      if (Array.isArray(content)) body.appendChild(el('ul', {}, content.map((c) => el('li', { text: c }))));
      else body.appendChild(el('p', { text: content }));
    };
    section('Fit summary', a.fit_summary);
    section('Strongest matches', a.strongest_matches);
    section('Real gaps', a.gaps);
    section('Seniority fit', a.seniority_fit);
    section('Recommended application angle', a.application_angle);
    section('Salary commentary', a.salary_commentary);
    body.appendChild(el('div', { class: 'card-actions' }, [
      el('button', { class: 'small', text: 'Re-run analysis', onclick: async () => {
        try {
          await api('/api/jobs/' + job.id + '/analyse', { method: 'POST', body: { force: true } });
          showAnalysis(job);
        } catch (err) { toast(err.message, true); }
      } }),
      el('button', { class: 'small', text: 'Open job', onclick: () => window.open(job.url, '_blank', 'noopener') }),
    ]));
  } catch (err) {
    clear(body).appendChild(el('p', { text: err.message }));
  }
}

async function applyToJob(job) {
  const body = el('div', {}, [
    el('p', { text: 'Opening the application page in a controlled Chromium window and filling the objective fields.' }),
    el('p', { class: 'muted', text: 'Nothing will be submitted. You review and click Submit yourself.' }),
  ]);
  dialog('Apply - ' + job.title, body);
  try {
    const report = await api('/api/jobs/' + job.id + '/apply', { method: 'POST', body: { url: '' } });
    clear(body);
    body.appendChild(el('div', { class: 'notice warn', html:
      '<strong>Nothing was submitted.</strong> Review every field in the browser window and click Submit yourself.' }));
    body.appendChild(el('div', { class: 'kv', html:
      'Form detected as <b>' + escapeHtml(report.adapter_label || report.adapter) + '</b>, ' +
      report.controls_found + ' fields found.' }));

    body.appendChild(el('h2', { text: 'Filled automatically (' + report.filled.length + ')' }));
    body.appendChild(report.filled.length
      ? el('ul', {}, report.filled.map((f) => el('li', { text: f.label + ' -> ' + f.value })))
      : el('p', { class: 'muted', text: 'Nothing could be filled automatically.' }));

    if (report.uploads.length) {
      body.appendChild(el('h2', { text: 'Documents attached' }));
      body.appendChild(el('ul', {}, report.uploads.map((u) => el('li', { text: u.kind + ': ' + u.filename }))));
    }

    body.appendChild(el('h2', { text: 'Review required (' + report.review.length + ')' }));
    body.appendChild(report.review.length
      ? el('ul', { class: 'review-list' }, report.review.map((r) => el('li', {}, [
          el('div', { text: r.label + (r.required ? ' *' : '') }),
          el('div', { class: 'reason', text: r.reason }),
        ])))
      : el('p', { class: 'muted', text: 'Nothing needs review.' }));

    if (report.notes && report.notes.length) {
      body.appendChild(el('h2', { text: 'Notes' }));
      body.appendChild(el('ul', {}, report.notes.map((n) => el('li', { text: n }))));
    }
    body.appendChild(el('div', { class: 'card-actions' }, [
      el('button', { class: 'small primary', text: 'Track this application',
        onclick: () => { closeDialog(); addToApplications(job); } }),
      el('button', { class: 'small', text: 'Close browser window',
        onclick: async () => { await api('/api/apply/close', { method: 'POST' }); toast('Browser closed.'); } }),
    ]));
    await loadJobs();
  } catch (err) {
    clear(body);
    body.appendChild(el('div', { class: 'notice warn', text: err.message }));
    body.appendChild(el('p', { class: 'muted', text:
      'You can always open the job manually and apply there. Playwright is installed with: ' }));
    body.appendChild(el('p', {}, [el('code', { text: 'pip install playwright && python -m playwright install chromium' })]));
  }
}

async function addToApplications(job) {
  try {
    const application = await api('/api/jobs/' + job.id + '/application', { method: 'POST' });
    toast('Added to Applications: ' + application.company + ' - ' + application.position);
    await loadJobs();
    switchView('applications');
    openApplication(application.id);
  } catch (err) { toast(err.message, true); }
}

/* ========================== APPLICATIONS ========================== */
let applicationMeta = { statuses: [], event_types: [] };
let pipelineFilter = '';

async function loadApplications() {
  const data = await api('/api/applications' + (pipelineFilter ? '?status=' + encodeURIComponent(pipelineFilter) : ''));
  applicationMeta = { statuses: data.statuses, event_types: data.event_types };

  const pipeline = clear($('#pipeline'));
  data.board.forEach((stage) => {
    pipeline.appendChild(el('button', {
      class: 'stage' + (pipelineFilter === stage.status ? ' active' : ''),
      onclick: () => { pipelineFilter = pipelineFilter === stage.status ? '' : stage.status; loadApplications(); },
    }, [
      el('span', { class: 'n', text: String(stage.count) }),
      el('span', { class: 's', text: stage.status }),
    ]));
  });

  const list = clear($('#application-list'));
  if (!data.applications.length) {
    list.appendChild(el('div', { class: 'empty', text: 'No applications yet. Track one from the Jobs screen.' }));
    return;
  }
  data.applications.forEach((application) => {
    list.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'card-head' }, [
        el('div', { class: 'card-title' }, [
          el('h3', {}, [
            el('span', { text: application.position }),
            el('span', { class: 'badge', text: application.status }),
          ]),
          el('div', { class: 'card-meta', text: [application.company, application.location,
            application.work_model].filter(Boolean).join(' / ') }),
        ]),
      ]),
      el('div', { class: 'kv', html: [
        application.next_action ? 'Next: <b>' + escapeHtml(application.next_action) + '</b>' : '',
        application.follow_up_date ? 'Due: <b>' + escapeHtml(application.follow_up_date) + '</b>' : '',
        application.salary_estimate ? 'Estimate: <b>' + escapeHtml(application.salary_estimate) + '</b>' : '',
      ].filter(Boolean).join('<span class="sep"> · </span>') }),
      el('div', { class: 'card-actions' }, [
        el('button', { class: 'small', text: 'Open', onclick: () => openApplication(application.id) }),
        application.job_url ? el('button', { class: 'small ghost', text: 'Job posting',
          onclick: () => window.open(application.job_url, '_blank', 'noopener') }) : null,
      ]),
    ]));
  });
}

async function openApplication(applicationId) {
  const application = await api('/api/applications/' + applicationId);
  const form = el('div', {});
  const body = el('div', {}, [form]);

  form.appendChild(el('div', { class: 'grid2' }, [
    field('Company', input('company', application.company)),
    field('Role', input('position', application.position)),
    field('Status', selectBox('status', application.status, applicationMeta.statuses)),
    field('Applied on', input('applied_date', application.applied_date, 'date')),
    field('Location', input('location', application.location)),
    field('Work model', input('work_model', application.work_model)),
    field('Contact', input('contact_name', application.contact_name)),
    field('Contact details', input('contact_details', application.contact_details)),
    field('Next action', input('next_action', application.next_action)),
    field('Next date', input('follow_up_date', application.follow_up_date, 'date')),
    field('Salary estimate', input('salary_estimate', application.salary_estimate)),
    field('CV version used', input('cv_version', application.cv_version)),
  ]));
  form.appendChild(field('Job URL', input('job_url', application.job_url)));
  form.appendChild(field('Match analysis', textarea('match_summary', application.match_summary)));
  form.appendChild(field('Notes', textarea('notes', application.notes)));

  form.appendChild(el('div', { class: 'card-actions' }, [
    el('button', { class: 'small primary', text: 'Save', onclick: async () => {
      try {
        await api('/api/applications/' + applicationId, { method: 'PUT', body: readForm(form) });
        toast('Saved.');
        await loadApplications();
        openApplication(applicationId);
      } catch (err) { toast(err.message, true); }
    } }),
    el('button', { class: 'small ghost danger', text: 'Delete', onclick: async () => {
      try {
        await api('/api/applications/' + applicationId, { method: 'DELETE' });
        closeDialog();
        toast('Deleted.');
        await loadApplications();
      } catch (err) { toast(err.message, true); }
    } }),
  ]));

  /* documents used */
  body.appendChild(el('h2', { text: 'Documents used' }));
  const docWrap = el('div', {});
  body.appendChild(docWrap);
  renderApplicationDocuments(docWrap, applicationId, application.documents);

  /* timeline */
  body.appendChild(el('h2', { text: 'Activity timeline' }));
  const eventForm = el('div', { class: 'grid3' }, [
    field('Date', input('event_date', new Date().toISOString().slice(0, 10), 'date')),
    field('Type', selectBox('event_type', 'Note', applicationMeta.event_types)),
    field('Person', input('person', '')),
  ]);
  const noteBox = field('What happened', textarea('note', ''));
  const nextRow = el('div', { class: 'grid2' }, [
    field('Next step', input('next_step', '')),
    field('Follow up on', input('follow_up_date', '', 'date')),
  ]);
  const eventBlock = el('div', {}, [eventForm, noteBox, nextRow,
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small', text: 'Add to timeline', onclick: async () => {
        const payload = Object.assign(readForm(eventForm), readForm(noteBox), readForm(nextRow));
        try {
          await api('/api/applications/' + applicationId + '/events', { method: 'POST', body: payload });
          toast('Timeline updated.');
          openApplication(applicationId);
        } catch (err) { toast(err.message, true); }
      } }),
    ])]);
  body.appendChild(eventBlock);
  body.appendChild(el('ul', { class: 'timeline' }, application.events.map((event) => el('li', {}, [
    el('div', { class: 'when', text: event.event_date }),
    el('div', { class: 'what', text: event.event_type + (event.person ? ' - ' + event.person : '') }),
    event.note ? el('div', { class: 'note', text: event.note }) : null,
    event.next_step ? el('div', { class: 'note', text: 'Next: ' + event.next_step +
      (event.follow_up_date ? ' (' + event.follow_up_date + ')' : '') }) : null,
  ]))));

  dialog(application.company + ' - ' + application.position, body);
}

async function renderApplicationDocuments(wrap, applicationId, attached) {
  const all = (await api('/api/profile/documents')).documents;
  clear(wrap);
  if (attached.length) {
    wrap.appendChild(el('table', {}, [el('tbody', {}, attached.map((doc) => el('tr', {}, [
      el('td', { text: doc.kind_label || doc.kind }),
      el('td', { text: doc.filename }),
      el('td', { class: 'actions' }, [el('button', { class: 'small ghost', text: 'Remove', onclick: async () => {
        await api('/api/applications/' + applicationId + '/documents/' + doc.id, { method: 'DELETE' });
        const refreshed = await api('/api/applications/' + applicationId);
        renderApplicationDocuments(wrap, applicationId, refreshed.documents);
      } })]),
    ])))]));
  } else {
    wrap.appendChild(el('p', { class: 'muted', text: 'No documents linked yet.' }));
  }
  const attachedIds = new Set(attached.map((d) => d.id));
  const options = all.filter((d) => !attachedIds.has(d.id))
    .map((d) => [d.id, (d.kind_label || d.kind) + ' - ' + d.filename]);
  if (options.length) {
    const picker = selectBox('document_id', '', options);
    wrap.appendChild(el('div', { class: 'card-actions' }, [
      picker,
      el('button', { class: 'small', text: 'Attach', onclick: async () => {
        await api('/api/applications/' + applicationId + '/documents',
          { method: 'POST', body: { document_id: Number(picker.value), role: '' } });
        const refreshed = await api('/api/applications/' + applicationId);
        renderApplicationDocuments(wrap, applicationId, refreshed.documents);
      } }),
    ]));
  }
}

function newApplicationDialog() {
  const form = el('div', {}, [
    el('div', { class: 'grid2' }, [
      field('Company', input('company', '')),
      field('Role', input('position', '')),
      field('Status', selectBox('status', 'Preparation', applicationMeta.statuses.length
        ? applicationMeta.statuses
        : ['Preparation', 'Applied', 'Screening', 'Interview', 'Final', 'Offer', 'Rejected', 'Withdrawn'])),
      field('Location', input('location', '')),
    ]),
    field('Job URL', input('job_url', '')),
    field('Notes', textarea('notes', '')),
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small primary', text: 'Create', onclick: async () => {
        try {
          const created = await api('/api/applications', { method: 'POST', body: readForm(form) });
          closeDialog();
          await loadApplications();
          openApplication(created.id);
        } catch (err) { toast(err.message, true); }
      } }),
    ]),
  ]);
  dialog('New application', form);
}

/* ============================= PROFILE ============================= */
function listArea(name, values) {
  return textarea(name, (values || []).join('\n'));
}

async function loadProfile() {
  const data = await api('/api/profile');
  state.profile = data;
  const person = data.person;
  const root = clear($('#profile-form'));
  const form = el('div', {});
  root.appendChild(form);

  const section = (title, content) => form.appendChild(
    el('fieldset', {}, [el('legend', { text: title })].concat(content)));

  section('Personal', [
    el('div', { class: 'grid2' }, [
      field('First name', input('first_name', person.first_name)),
      field('Last name', input('last_name', person.last_name)),
    ]),
    field('Headline', input('headline', person.headline)),
    field('Summary', textarea('summary', person.summary)),
  ]);

  section('Contact', [
    el('div', { class: 'grid2' }, [
      field('Email', input('email', person.email, 'email')),
      field('Phone', input('phone', person.phone)),
      field('Address', input('address', person.address)),
      field('Postal code', input('postal_code', person.postal_code)),
      field('City', input('city', person.city)),
      field('Country', input('country', person.country)),
    ]),
    el('div', { class: 'grid3' }, [
      field('LinkedIn', input('linkedin_url', person.linkedin_url, 'url')),
      field('GitHub', input('github_url', person.github_url, 'url')),
      field('Website', input('website_url', person.website_url, 'url')),
    ]),
  ]);

  section('Nationality and work authorisation', [
    el('div', { class: 'grid2' }, [
      field('Nationality', input('nationality', person.nationality)),
      field('Relocation', input('relocation', person.relocation)),
    ]),
    field('Work authorisation', textarea('work_authorization', person.work_authorization),
      'Used verbatim by the apply assistant when a form asks about authorisation.'),
    checkbox('needs_sponsorship', person.needs_sponsorship, 'Requires visa sponsorship'),
    checkbox('willing_to_relocate', person.willing_to_relocate, 'Willing to relocate within Switzerland'),
  ]);

  section('Languages', [
    field('Languages', textarea('languages_text',
      (person.languages || []).map((l) => l.language + ': ' + l.level).join('\n')),
      'One per line, "Language: level".'),
  ]);

  section('Availability', [
    el('div', { class: 'grid3' }, [
      field('Notice period', input('notice_period', person.notice_period)),
      field('Earliest start', input('earliest_start', person.earliest_start)),
      field('Availability', input('availability', person.availability)),
    ]),
    field('Travel willingness', textarea('travel_willingness', person.travel_willingness),
      'Used when a form or an application draft asks about travel.'),
  ]);

  section('Compensation expectations', [
    el('div', { class: 'grid3' }, [
      field('Minimum TC (CHF)', input('comp_minimum_chf', person.comp_minimum_chf, 'number')),
      field('Target TC (CHF)', input('comp_target_chf', person.comp_target_chf, 'number')),
      field('Stretch TC (CHF)', input('comp_stretch_chf', person.comp_stretch_chf, 'number')),
    ]),
    field('Notes', textarea('comp_notes', person.comp_notes)),
  ]);

  section('Career history', [
    field('Positions', textarea('career_history_text',
      (person.career_history || []).map((item) => [item.title, item.company, item.start,
        item.end, item.location, item.highlights].join(' | ')).join('\n')),
      'One per line: Title | Company | Start | End | Location | Highlights'),
  ]);

  section('Skills and leadership', [
    field('Technical skills', listArea('technical_skills_text', person.technical_skills),
      'One per line.'),
    field('Leadership profile', textarea('leadership_profile', person.leadership_profile)),
    field('Strengths', listArea('strengths_text', person.strengths),
      'One per line. Context for interviews and application drafts only - never a filter.'),
  ]);

  section('Achievements', [
    field('Achievements', listArea('achievements_text', person.achievements),
      'One per line. Private matching and application context; nothing here is published '
      + 'automatically, and confidential project names do not belong in it.'),
  ]);

  section('Targets', [
    field('Primary target roles', listArea('target_roles_text', person.target_roles),
      'One per line. Matched semantically - an exact title is never required.'),
    field('Secondary target roles', listArea('secondary_target_roles_text', person.secondary_target_roles),
      'One per line. Still welcome when the scope and responsibility are strong.'),
    field('Target geography', listArea('target_geography_text', person.target_geography),
      'One per line. Search context - it is never inserted into a CV or cover letter.'),
  ]);

  root.appendChild(el('div', { class: 'sticky-save' }, [
    el('button', { class: 'primary', text: 'Save profile', onclick: async () => {
      const values = readForm(form);
      const payload = Object.assign({}, values);
      delete payload.languages_text;
      delete payload.career_history_text;
      delete payload.technical_skills_text;
      delete payload.target_roles_text;
      delete payload.secondary_target_roles_text;
      delete payload.target_geography_text;
      delete payload.strengths_text;
      delete payload.achievements_text;
      payload.languages = (values.languages_text || '').split('\n').filter(Boolean).map((line) => {
        const [language, level] = line.split(':');
        return { language: (language || '').trim(), level: (level || '').trim() };
      });
      payload.career_history = (values.career_history_text || '').split('\n').filter(Boolean).map((line) => {
        const parts = line.split('|').map((p) => p.trim());
        return { title: parts[0] || '', company: parts[1] || '', start: parts[2] || '',
          end: parts[3] || '', location: parts[4] || '', highlights: parts[5] || '' };
      });
      payload.technical_skills = (values.technical_skills_text || '').split('\n').map((s) => s.trim()).filter(Boolean);
      payload.target_roles = (values.target_roles_text || '').split('\n').map((s) => s.trim()).filter(Boolean);
      payload.target_geography = splitLines(values.target_geography_text);
      payload.secondary_target_roles = splitLines(values.secondary_target_roles_text);
      payload.strengths = splitLines(values.strengths_text);
      payload.achievements = splitLines(values.achievements_text);
      try {
        await api('/api/profile', { method: 'PUT', body: payload });
        toast('Profile saved.');
      } catch (err) { toast(err.message, true); }
    } }),
    el('span', { class: 'muted', text: 'Stored locally in SQLite. Documents stay on disk.' }),
  ]));

  /* documents */
  root.appendChild(el('h2', { text: 'Documents' }));
  root.appendChild(el('p', { class: 'muted', text:
    'Files are stored in documents/ on this machine. Only the path is kept in the database.' }));
  root.appendChild(categoryTable(data.document_categories || []));
  const docTable = el('div', {});
  root.appendChild(docTable);
  renderDocuments(docTable, data.documents, data.document_kinds);
}

/* Every supported category, configured or not. A category with no file says
   "Not configured" - no placeholder document is ever invented for it. */
function categoryTable(categories) {
  const statusClass = (status) => (status === 'Configured' ? 'status-ok'
    : (status === 'Missing file' ? 'status-broken' : 'status-missing'));
  return el('table', {}, [
    el('thead', {}, [el('tr', {}, ['Category', 'Folder', 'Status', 'Use', 'Files']
      .map((h) => el('th', { text: h })))]),
    el('tbody', {}, categories.map((cat) => el('tr', {}, [
      el('td', { text: cat.label }),
      el('td', {}, [el('code', { text: cat.directory })]),
      el('td', { class: statusClass(cat.status), text: cat.status }),
      el('td', { class: 'muted', text: cat.upload_allowed
        ? 'Application upload' : 'Private - never uploaded' }),
      el('td', { class: 'muted', text: cat.files.join(', ') || '-' }),
    ]))),
  ]);
}

function renderDocuments(wrap, documents, kinds) {
  clear(wrap);
  if (documents.length) {
    wrap.appendChild(el('table', {}, [
      el('thead', {}, [el('tr', {}, [
        el('th', { text: 'Kind' }), el('th', { text: 'File' }), el('th', { text: 'Size' }),
        el('th', { text: 'Primary' }), el('th', {}),
      ])]),
      el('tbody', {}, documents.map((doc) => el('tr', {}, [
        el('td', { text: doc.kind_label || doc.kind }),
        el('td', {}, [el('a', { href: '/api/profile/documents/' + doc.id + '/file',
          target: '_blank', text: doc.filename })]),
        el('td', { text: Math.round(doc.size_bytes / 1024) + ' KB' }),
        el('td', { text: doc.is_primary ? 'yes' : '' }),
        el('td', { class: 'actions' }, [
          doc.is_primary ? null : el('button', { class: 'small ghost', text: 'Make primary',
            onclick: async () => { await api('/api/profile/documents/' + doc.id + '/primary',
              { method: 'POST' }); loadProfile(); } }),
          el('button', { class: 'small ghost danger', text: 'Remove', onclick: async () => {
            await api('/api/profile/documents/' + doc.id, { method: 'DELETE' }); loadProfile(); } }),
        ]),
      ]))),
    ]));
  } else {
    wrap.appendChild(el('p', { class: 'muted', text: 'No documents uploaded yet.' }));
  }

  const kindPicker = selectBox('kind', 'cv_en', kinds.map((k) => [k.kind, k.label]));
  const fileInput = el('input', { type: 'file' });
  wrap.appendChild(el('div', { class: 'card-actions' }, [
    kindPicker, fileInput,
    el('button', { class: 'small primary', text: 'Upload', onclick: async () => {
      if (!fileInput.files.length) { toast('Choose a file first.', true); return; }
      const body = new FormData();
      body.append('kind', kindPicker.value);
      body.append('file', fileInput.files[0]);
      try {
        await api('/api/profile/documents', { method: 'POST', body });
        toast('Uploaded.');
        loadProfile();
      } catch (err) { toast(err.message, true); }
    } }),
  ]));
}

/* ============================== CONFIG ============================= */
async function loadConfig() {
  const data = await api('/api/config');
  state.config = data;
  const root = clear($('#config-body'));
  const settings = data.settings;

  const section = (title, content) => root.appendChild(
    el('fieldset', {}, [el('legend', { text: title })].concat(content)));

  /* -- Job Sources -- */
  const health = data.source_health || { sources: [], failures: [] };
  const healthRows = health.sources.map((kind) => el('tr', {}, [
    el('td', { text: kind.label }),
    el('td', { text: kind.companies + (kind.companies === 1 ? ' company' : ' companies') }),
    el('td', { text: kind.status }),
    el('td', { class: 'muted', text: shortStamp(kind.last_success_at) || '-' }),
    el('td', { text: kind.jobs_returned ? String(kind.jobs_returned) : '-' }),
  ]));
  // A broken integration is never silent: every failure is listed by name.
  const failureRows = health.failures.map((f) => el('tr', {}, [
    el('td', { text: f.company || f.source }),
    el('td', { text: f.status === 'ERROR' ? 'Source error' : 'No postings returned' }),
    el('td', { class: 'muted', text: f.error || '-' }),
    el('td', { class: 'muted', text: f.last_success_at
      ? 'Last successful scan: ' + shortStamp(f.last_success_at) : 'Never scanned successfully' }),
  ]));

  const sourceRows = data.sources.map((source) => el('tr', {}, [
    el('td', { text: source.name }),
    el('td', { text: source.source_type }),
    el('td', {}, [(() => {
      const box = el('input', { type: 'checkbox' });
      box.checked = source.enabled;
      box.addEventListener('change', async () => {
        await api('/api/config/sources/' + source.id, { method: 'PUT', body: { enabled: box.checked } });
        toast('Source updated.');
      });
      return box;
    })()]),
    el('td', { text: source.last_status || '-' }),
    el('td', { text: source.last_job_count ? String(source.last_job_count) : '-' }),
    el('td', { class: 'muted', text: JSON.stringify(source.config) }),
    el('td', { class: 'actions' }, [el('button', { class: 'small ghost danger', text: 'Remove',
      onclick: async () => { await api('/api/config/sources/' + source.id, { method: 'DELETE' }); loadConfig(); } })]),
  ]));
  const newSource = el('div', { class: 'grid3' }, [
    field('Name', input('name', '')),
    field('Type', selectBox('source_type', 'greenhouse', data.source_types.map((t) => [t.type, t.label]))),
    field('Config (JSON)', input('config', '{"board_token": "company"}')),
  ]);
  section('Job Sources', [
    el('table', {}, [
      el('thead', {}, [el('tr', {}, ['Source', 'Companies', 'Status', 'Last successful scan', 'Jobs returned']
        .map((h) => el('th', { text: h })))]),
      el('tbody', {}, healthRows),
    ]),
    failureRows.length ? el('h3', { text: 'Failures' }) : null,
    failureRows.length ? el('table', {}, [el('tbody', {}, failureRows)]) : null,
    el('h3', { text: 'Configured sources' }),
    el('table', {}, [
      el('thead', {}, [el('tr', {}, ['Name', 'Type', 'Enabled', 'Last scan', 'Jobs', 'Config', '']
        .map((h) => el('th', { text: h })))]),
      el('tbody', {}, sourceRows),
    ]),
    newSource,
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small', text: 'Add source', onclick: async () => {
        const values = readForm(newSource);
        let config = {};
        try { config = JSON.parse(values.config || '{}'); }
        catch (err) { toast('Config must be valid JSON.', true); return; }
        try {
          await api('/api/config/sources', { method: 'POST',
            body: { name: values.name, source_type: values.source_type, config, enabled: true } });
          loadConfig();
        } catch (err) { toast(err.message, true); }
      } }),
    ]),
    el('div', { class: 'muted', html: data.source_types.map((t) =>
      '<b>' + escapeHtml(t.label) + '</b>: ' + escapeHtml(t.limitations)).join('<br>') }),
  ]);

  /* -- Company Watchlist -- */
  const kinds = data.watchlist_source_kinds || [{ type: 'manual', label: 'Manual' }];
  const watchRows = data.watchlist.map((entry) => el('tr', {}, [
    el('td', { text: entry.company_name }),
    el('td', { text: entry.priority }),
    el('td', { text: entry.source_label }),
    // MANUAL means "not scanned" and says so; it never looks like a live source.
    el('td', { text: entry.source_status }),
    el('td', { text: entry.automated && entry.job_count_last_scan
      ? String(entry.job_count_last_scan) : '-' }),
    el('td', { class: 'muted', text: shortStamp(entry.last_scan_at) || '-' }),
    el('td', { class: 'actions' }, [
      entry.career_url ? el('a', { class: 'small ghost', href: entry.career_url,
        target: '_blank', text: 'Open careers page' }) : null,
      entry.automated ? el('button', { class: 'small ghost', text: 'Check source',
        onclick: async (event) => {
          const button = event.target;
          button.disabled = true;
          button.textContent = 'Checking...';
          try {
            const result = await api('/api/config/watchlist/' + entry.id + '/verify', { method: 'POST' });
            toast(entry.company_name + ': ' + result.entry.source_status +
              (result.entry.last_error ? ' - ' + result.entry.last_error : ''),
              result.entry.source_status !== 'ACTIVE');
            loadConfig();
          } catch (err) { toast(err.message, true); button.disabled = false; }
        } }) : null,
      el('button', { class: 'small ghost danger', text: 'Remove',
        onclick: async () => { await api('/api/config/watchlist/' + entry.id, { method: 'DELETE' }); loadConfig(); } }),
    ]),
  ]));
  const newWatch = el('div', { class: 'grid3' }, [
    field('Company', input('company_name', '')),
    field('Priority', selectBox('priority', 'B', ['A', 'B', 'C'])),
    field('Careers URL', input('career_url', '')),
    field('Source', selectBox('career_source_type', 'manual', kinds.map((k) => [k.type, k.label]))),
    field('Identifier', input('career_source_identifier', '')),
  ]);
  section('Company Watchlist', [
    el('table', {}, [
      el('thead', {}, [el('tr', {}, ['Company', 'Priority', 'Source', 'Status', 'Jobs', 'Last scan', 'Action']
        .map((h) => el('th', { text: h })))]),
      el('tbody', {}, watchRows),
    ]),
    el('div', { class: 'muted', text: 'MANUAL means no public endpoint could be verified for that '
      + 'company - nothing is fetched for it and only the careers link is offered. A source only '
      + 'becomes ACTIVE once a live request returned real postings.' }),
    newWatch,
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small', text: 'Add company', onclick: async () => {
        try {
          await api('/api/config/watchlist', { method: 'POST', body: readForm(newWatch) });
          loadConfig();
        } catch (err) { toast(err.message, true); }
      } }),
    ]),
    el('div', { class: 'muted', html: kinds.map((k) =>
      '<b>' + escapeHtml(k.label) + '</b>: ' + escapeHtml(k.identifier_label || '-')).join('<br>') }),
  ]);

  /* -- Search profile: geography, roles, matching and compensation all edit
        the one stored profile, so they share a single form and one save. -- */
  const matching = data.matching;
  const profileForm = el('div', {});
  const saveProfile = async () => {
    const values = readForm(profileForm);
    const payload = Object.assign({}, matching, {
      country_mode: values.country_mode,
      location_filter_mode: values.location_filter_mode,
      hybrid_max_office_days: Number(values.hybrid_max_office_days),
      preferred_radius_km: Number(values.preferred_radius_km),
      max_commute_minutes: Number(values.max_commute_minutes),
      allowed_locations: splitLines(values.allowed_locations_text),
      optional_locations: splitLines(values.optional_locations_text),
      tertiary_locations: splitLines(values.tertiary_locations_text),
      remote_policy: {
        allow_remote: values.allow_remote,
        allow_hybrid: values.allow_hybrid,
        allow_onsite: values.allow_onsite,
      },
      seniority_levels: splitLines(values.seniority_levels_text),
      secondary_titles: splitLines(values.secondary_titles_text),
      include_titles: splitLines(values.include_titles_text),
      preferred_keywords: splitLines(values.preferred_keywords_text),
      excluded_keywords: splitLines(values.excluded_keywords_text),
      deprioritized_keywords: splitLines(values.deprioritized_keywords_text),
      exclude_titles: splitLines(values.exclude_titles_text),
      minimum_match_score: Number(values.minimum_match_score),
      salary_mode: values.salary_mode,
      allow_missing_salary: values.allow_missing_salary,
      minimum_salary_chf: Number(values.minimum_salary_chf),
      salary_target_chf: Number(values.salary_target_chf),
      salary_floor_chf: Number(values.salary_floor_chf),
    });
    try {
      await api('/api/config/matching', { method: 'PUT', body: payload });
      toast('Search profile saved.');
      loadConfig();
    } catch (err) { toast(err.message, true); }
  };
  const saveRow = (label) => el('div', { class: 'card-actions' }, [
    el('button', { class: 'small primary', text: label, onclick: saveProfile }),
  ]);
  const part = (title, content) => {
    const box = el('div', {}, content);
    profileForm.appendChild(box);
    section(title, [box, saveRow('Save search profile')]);
    return box;
  };

  part('Search Profile', [
    el('div', { class: 'notice', text:
      'Stage 1 applies the hard rules: Switzerland only, and no junior, intern, recruiter, '
      + 'HR or helpdesk roles. Stage 2 scores what survives. Everything on this screen edits '
      + 'the one profile the scanner reads - there is no second copy.' }),
    el('div', { class: 'grid3' }, [
      field('Minimum match score', input('minimum_match_score', matching.minimum_match_score, 'number'),
        'Jobs below this are kept in the database and explained, but not listed.'),
      field('Country mode', selectBox('country_mode', matching.country_mode, ['strict', 'preferred', 'off']),
        'Strict: a posting must be explicitly Swiss-eligible.'),
      field('Preferred-location mode', selectBox('location_filter_mode', matching.location_filter_mode,
        [['ranking', 'Ranking only (recommended)'], ['hard', 'Hard filter']]),
        'Ranking: a Swiss job in an unlisted city still appears, it just scores lower.'),
    ]),
  ]);

  part('Geography', [
    el('div', { class: 'grid3' }, [
      field('Primary locations', listArea('allowed_locations_text', matching.allowed_locations), 'One per line.'),
      field('Other preferred regions', listArea('optional_locations_text', matching.optional_locations), 'One per line.'),
      field('Secondary acceptable', listArea('tertiary_locations_text', matching.tertiary_locations), 'One per line.'),
    ]),
    el('div', { class: 'grid3' }, [
      field('Preferred radius (km)', input('preferred_radius_km', matching.preferred_radius_km, 'number'),
        'A preference the UI shows. No filter reads it - it never rejects a job.'),
      field('Preferred commute (minutes)', input('max_commute_minutes', matching.max_commute_minutes, 'number'),
        'Also a preference, never a rejection criterion.'),
      field('Max office days / week', input('hybrid_max_office_days', matching.hybrid_max_office_days, 'number')),
    ]),
    el('div', { class: 'grid3' }, [
      checkbox('allow_remote', matching.remote_policy.allow_remote, 'Remote accepted'),
      checkbox('allow_hybrid', matching.remote_policy.allow_hybrid, 'Hybrid accepted'),
      checkbox('allow_onsite', matching.remote_policy.allow_onsite, 'Onsite accepted'),
    ]),
    el('div', { class: 'muted', text:
      'An unknown work model is never rejected. Generic "Europe", "EU", "EMEA", "DACH" or '
      + 'Germany postings stay rejected unless the posting states Swiss eligibility itself.' }),
  ]);

  part('Target Roles', [
    el('div', { class: 'grid2' }, [
      field('Preferred seniority', listArea('seniority_levels_text', matching.seniority_levels), 'One per line.'),
      field('Also accepted titles', listArea('secondary_titles_text', matching.secondary_titles),
        'One per line. These can never be rejected for "wrong seniority"; scope decides.'),
    ]),
    field('Target responsibility areas', listArea('include_titles_text', matching.include_titles),
      'One per line. A vocabulary, not a title whitelist - an exact title match is never required.'),
    field('Technology / domain keywords', listArea('preferred_keywords_text', matching.preferred_keywords),
      'One per line. Repetition is never rewarded twice.'),
    el('div', { class: 'grid2' }, [
      field('Down-ranked domains', listArea('deprioritized_keywords_text', matching.deprioritized_keywords),
        'One per line. These cost points and are named as a concern - they never reject a job.'),
      field('Excluded titles', listArea('exclude_titles_text', matching.exclude_titles), 'One per line.'),
    ]),
    field('Excluded keywords', listArea('excluded_keywords_text', matching.excluded_keywords),
      'One per line. A different profession in the title; a technical signal in the same title rescues it.'),
  ]);

  /* -- Matching: the weights and the bands are fixed, so they are shown
        rather than offered as another thing to get wrong. -- */
  const weights = data.weights || { weights: [], total: 0 };
  section('Matching', [
    el('table', {}, [
      el('thead', {}, [el('tr', {}, ['Dimension', 'Weight'].map((h) => el('th', { text: h })))]),
      el('tbody', {}, weights.weights.map((w) => el('tr', {}, [
        el('td', { text: w.label }), el('td', { text: String(w.points) }),
      ])).concat([el('tr', {}, [
        el('td', {}, [el('b', { text: 'Total' })]), el('td', {}, [el('b', { text: String(weights.total) })]),
      ])])),
    ]),
    el('h3', { text: 'How to read a score' }),
    el('table', {}, [
      el('tbody', {}, (data.bands || []).map((b) => el('tr', {}, [
        el('td', { text: b.from + '-' + b.to }), el('td', { text: b.label }),
      ]))),
    ]),
    el('div', { class: 'muted', text:
      'Semantic responsibility fit outweighs exact keywords, and keyword repetition earns '
      + 'nothing extra. Company priority only breaks a tie between equal scores - it never '
      + 'moves a job up the list on its own.' }),
  ]);

  part('Compensation', [
    el('div', { class: 'grid3' }, [
      field('Interesting from (CHF TC)', input('minimum_salary_chf', matching.minimum_salary_chf, 'number')),
      field('Target (CHF TC)', input('salary_target_chf', matching.salary_target_chf, 'number')),
      field('Published-and-clearly-below (CHF)', input('salary_floor_chf', matching.salary_floor_chf, 'number')),
    ]),
    field('Salary handling', selectBox('salary_mode', matching.salary_mode,
      [['ranking', 'Ranking signal only (recommended)'], ['ignore', 'Ignore salary'],
       ['hard', 'Hard minimum']])),
    checkbox('allow_missing_salary', matching.allow_missing_salary,
      'Keep jobs that publish no salary (most leadership roles publish none)'),
    el('div', { class: 'muted', text:
      'Estimates use, in order: a published salary, company-specific Swiss evidence, then a '
      + 'role + seniority + Swiss baseline. Every estimate carries a range and a confidence; '
      + 'a job without a published salary is never hidden or penalised for it.' }),
  ]);

  /* -- AI -- */
  const aiForm = el('div', {}, [
    checkbox('ai_enabled', settings.ai_enabled, 'Enable AI analysis'),
    el('div', { class: 'grid3' }, [
      field('Provider', selectBox('ai_provider', settings.ai_provider, ['none', 'anthropic', 'openai'])),
      field('Model', input('ai_model', settings.ai_model)),
      field('Timeout (s)', input('ai_timeout_seconds', settings.ai_timeout_seconds, 'number')),
    ]),
  ]);
  section('AI', [
    el('div', { class: 'notice' + (data.ai.enabled ? '' : ' warn'), html:
      escapeHtml(data.ai.reason) + '<br>API keys are read from environment variables only ' +
      '(<code>ANTHROPIC_API_KEY</code> / <code>OPENAI_API_KEY</code>) and are never stored.' }),
    aiForm,
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small primary', text: 'Save AI settings',
        onclick: () => saveSettings(readForm(aiForm)) }),
      el('button', { class: 'small', text: 'Test provider', onclick: async () => {
        const result = await api('/api/config/ai/test', { method: 'POST' });
        toast(result.message, !result.ok);
      } }),
      el('button', { class: 'small ghost', text: 'Clear analysis cache', onclick: async () => {
        await api('/api/config/ai/clear-cache', { method: 'POST' });
        toast('Cache cleared.');
      } }),
    ]),
  ]);

  /* -- Salary Model -- */
  const salaryForm = el('div', {}, [
    checkbox('salary_show_estimates', settings.salary_show_estimates, 'Show compensation estimates on job cards'),
    field('Market uplift (%)', input('salary_market_uplift_pct', settings.salary_market_uplift_pct, 'number'),
      'Applies to the local baseline, never to a salary published in the posting.'),
  ]);
  const benchRows = data.benchmarks.slice(0, 40).map((b) => el('tr', {}, [
    el('td', { text: b.company }),
    el('td', { text: b.role_family.replace(/_/g, ' ') }),
    el('td', { text: b.seniority }),
    el('td', { text: 'CHF ' + Math.round(b.base_min / 1000) + 'k-' + Math.round(b.base_max / 1000) + 'k' }),
    el('td', { text: b.bonus_pct_min + '-' + b.bonus_pct_max + '%' }),
    el('td', { text: b.confidence }),
    el('td', { class: 'actions' }, [el('button', { class: 'small ghost danger', text: 'Remove',
      onclick: async () => { await api('/api/config/benchmarks/' + b.id, { method: 'DELETE' }); loadConfig(); } })]),
  ]));
  section('Salary Model', [
    salaryForm,
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small primary', text: 'Save salary settings',
        onclick: () => saveSettings(readForm(salaryForm)) }),
      el('button', { class: 'small', text: 'Recalculate all estimates', onclick: async () => {
        await api('/api/config/salary/recalculate', { method: 'POST' });
        toast('Estimates cleared; they are rebuilt on the next Jobs load.');
      } }),
    ]),
    el('h2', { text: 'Local market data' }),
    el('table', {}, [
      el('thead', {}, [el('tr', {}, ['Company', 'Role family', 'Seniority', 'Base', 'Bonus', 'Confidence', '']
        .map((h) => el('th', { text: h })))]),
      el('tbody', {}, benchRows),
    ]),
  ]);

  /* -- Application Automation -- */
  const applyForm = el('div', {}, [
    checkbox('apply_enabled', settings.apply_enabled, 'Enable the apply assistant'),
    checkbox('apply_autofill', settings.apply_autofill, 'Fill objective fields automatically'),
    checkbox('apply_upload_cv', settings.apply_upload_cv, 'Attach the CV when the form asks for one'),
    checkbox('apply_upload_motivation', settings.apply_upload_motivation, 'Attach the motivation letter when clearly requested'),
    checkbox('apply_headless', settings.apply_headless, 'Run the browser headless (not recommended - you cannot review)'),
    field('CV language', selectBox('apply_cv_language', settings.apply_cv_language, [['en', 'English'], ['de', 'German']])),
  ]);
  section('Application Automation', [
    el('div', { class: 'notice warn', html:
      '<strong>The assistant never submits an application.</strong> It fills what it can identify ' +
      'objectively, marks subjective questions as "Review required", and stops before the submit button.' }),
    applyForm,
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small primary', text: 'Save automation settings',
        onclick: () => saveSettings(readForm(applyForm)) }),
      el('button', { class: 'small', text: 'Check Playwright', onclick: async () => {
        const status = await api('/api/apply/status');
        toast(status.available ? 'Playwright is ready. CV: ' + (status.cv || 'none stored') : status.message,
          !status.available);
      } }),
      el('button', { class: 'small ghost', text: 'Close browser window', onclick: async () => {
        await api('/api/apply/close', { method: 'POST' });
        toast('Browser closed.');
      } }),
    ]),
  ]);

  /* -- Documents -- */
  section('Documents', [
    categoryTable(data.documents || []),
    el('div', { class: 'muted', text:
      'Files live in documents/ on this machine; only the relative path and the metadata are '
      + 'stored in SQLite, never the file itself. Upload and replace them under Profile.' }),
  ]);

  /* -- Personal feedback -- */
  const feedback = (data.feedback || {}).summary || { verdicts: {}, reasons: {}, total: 0 };
  section('Personal feedback', [
    el('div', { class: 'kv', html:
      'Recorded verdicts: <b>' + (feedback.total || 0) + '</b><br>' +
      ['YES', 'MAYBE', 'NO'].map((v) => v + ': <b>' + (feedback.verdicts[v] || 0) + '</b>').join(' · ') }),
    Object.keys(feedback.reasons || {}).length ? el('table', {}, [
      el('thead', {}, [el('tr', {}, ['"No" reason', 'Count'].map((h) => el('th', { text: h })))]),
      el('tbody', {}, Object.entries(feedback.reasons).map(([reason, count]) => el('tr', {}, [
        el('td', { text: reason }), el('td', { text: String(count) }),
      ]))),
    ]) : null,
    el('div', { class: 'muted', text:
      'Stored for a later, explicit calibration step. Nothing here retrains the scorer or '
      + 'changes a filter on its own.' }),
  ]);

  /* -- Backup & Transfer -- */
  const transfer = data.transfer || { excluded: [], cli: {} };
  const importFile = el('input', { type: 'file', accept: '.zip' });
  section('Backup & Transfer', [
    el('div', { class: 'notice warn', text: transfer.privacy_notice || '' }),
    el('div', { class: 'kv', html:
      'Workspace: <code>' + escapeHtml(transfer.workspace_root || '') + '</code><br>' +
      'Database: <code>' + escapeHtml(transfer.database_path || '') + '</code><br>' +
      'Documents: <code>' + escapeHtml(transfer.documents_path || '') + '</code><br>' +
      'Export format <b>' + transfer.format_version + '</b>, database schema <b>'
      + transfer.schema_version + '</b>' }),
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small primary', text: 'Export workspace', onclick: exportWorkspace }),
      importFile,
      el('button', { class: 'small', text: 'Import workspace', onclick: () => {
        const file = importFile.files && importFile.files[0];
        if (!file) { toast('Choose a workspace .zip first.', true); return; }
        importWorkspace(file);
      } }),
    ]),
    el('div', { class: 'muted', html:
      'The export contains <b>data/app.db</b> and <b>documents/</b> only. Never included: '
      + (transfer.excluded || []).map(escapeHtml).join(', ') + '. Configure API keys again on '
      + 'the other machine.<br>Command line: <code>' + escapeHtml(transfer.cli.export || '')
      + '</code> and <code>' + escapeHtml(transfer.cli.import || '') + '</code>' }),
  ]);

  /* -- System -- */
  const systemForm = el('div', { class: 'grid2' }, [
    field('Jobs per page', input('jobs_page_size', settings.jobs_page_size, 'number')),
  ]);
  section('System', [
    el('div', { class: 'kv', html:
      'Database: <code>' + escapeHtml(data.system.database_path) + '</code><br>' +
      Object.entries(data.system.counts).map(([key, value]) => key + ': <b>' + value + '</b>').join(' · ') }),
    systemForm,
    checkboxRow('scan_hide_ignored', settings.scan_hide_ignored,
      'Hide ignored jobs on the Jobs screen'),
    el('div', { class: 'card-actions' }, [
      el('button', { class: 'small primary', text: 'Save system settings', onclick: () => {
        const values = readForm(systemForm);
        values.scan_hide_ignored = $('#scan_hide_ignored').checked;
        saveSettings(values);
      } }),
      el('button', { class: 'small', text: 'Back up database now', onclick: async () => {
        const result = await api('/api/config/system/backup', { method: 'POST' });
        toast('Backup written: ' + result.backup);
      } }),
    ]),
    data.system.backups.length ? el('div', { class: 'muted', text:
      'Recent backups: ' + data.system.backups.join(', ') }) : null,
  ]);
}

/* The archive is streamed straight to disk; nothing is uploaded anywhere. */
async function exportWorkspace() {
  try {
    const response = await fetch('/api/config/workspace/export', { method: 'POST' });
    if (!response.ok) throw new Error(await response.text());
    const blob = await response.blob();
    const name = (response.headers.get('content-disposition') || '')
      .split('filename=').pop().replace(/["']/g, '') || 'job-assistant-workspace.zip';
    const link = el('a', { href: URL.createObjectURL(blob), download: name });
    document.body.appendChild(link);
    link.click();
    link.remove();
    toast('Workspace exported. It contains personal data - store it securely.');
  } catch (err) { toast('Export failed: ' + err.message, true); }
}

/* Import replaces the local workspace. The server copies the current database
   and documents aside first and restores them if anything goes wrong, so a
   failed import cannot leave this machine half-written. */
async function importWorkspace(file) {
  const body = new FormData();
  body.append('file', file);
  let report;
  try {
    report = await api('/api/config/workspace/inspect', { method: 'POST', body });
  } catch (err) { toast('Not a usable workspace archive: ' + err.message, true); return; }

  const confirmed = window.confirm(
    'Replace this workspace with ' + file.name + '?\n\n'
    + 'Archive schema: ' + report.schema_version + '\n'
    + 'Documents: ' + report.documents + '\n'
    + (report.needs_migration ? 'It is older than this application and will be migrated.\n' : '')
    + '\nYour current database and documents are copied to data/backups/ first.');
  if (!confirmed) return;

  const payload = new FormData();
  payload.append('file', file);
  try {
    const result = await api('/api/config/workspace/import', { method: 'POST', body: payload });
    toast('Workspace imported: ' + result.document_files + ' document files. Backup: '
      + (result.backup.database || 'none needed'));
    loadConfig();
  } catch (err) { toast('Import failed, workspace unchanged: ' + err.message, true); }
}

function checkboxRow(id, checked, label) {
  const box = el('input', { type: 'checkbox', id });
  box.checked = !!checked;
  return el('label', { class: 'check' }, [box, el('span', { text: label })]);
}

function splitLines(text) {
  return (text || '').split('\n').map((line) => line.trim()).filter(Boolean);
}

async function saveSettings(values) {
  try {
    await api('/api/config/settings', { method: 'PUT', body: values });
    toast('Saved.');
    loadConfig();
  } catch (err) { toast(err.message, true); }
}

/* =============================== SHELL ============================= */
const LOADERS = { jobs: loadJobs, applications: loadApplications, profile: loadProfile, config: loadConfig };

function switchView(view) {
  state.view = view;
  closeDialog();
  document.querySelectorAll('.tab').forEach((tab) =>
    tab.classList.toggle('active', tab.dataset.view === view));
  document.querySelectorAll('.view').forEach((node) =>
    node.classList.toggle('active', node.id === 'view-' + view));
  if (location.hash.slice(1) !== view) location.hash = view;
  LOADERS[view]().catch((err) => toast(err.message, true));
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tab').forEach((tab) =>
    tab.addEventListener('click', () => switchView(tab.dataset.view)));
  $('#scan-btn').addEventListener('click', scan);
  $('#new-application').addEventListener('click', newApplicationDialog);
  $('#dialog-close').addEventListener('click', closeDialog);
  $('#dialog').addEventListener('click', (event) => { if (event.target.id === 'dialog') closeDialog(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeDialog(); });
  window.addEventListener('hashchange', () => {
    const view = location.hash.slice(1);
    if (LOADERS[view] && view !== state.view) switchView(view);
  });
  const initial = LOADERS[location.hash.slice(1)] ? location.hash.slice(1) : 'jobs';
  switchView(initial);
});
