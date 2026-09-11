(() => {
  const STATUS = ['Vorbereitung','Beworben','Eingangsbestätigung','Screening / HR','Interview 1','Interview 2','Case / Assessment','Final Interview','Angebot','On Hold','Abgelehnt','Zurückgezogen'];
  const state = { applications: [], dashboard: null, selectedId: null, scoutJobs: [], scoutSummary: null, scoutProfile: null, sources: [], scoutBusy: false };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  const fmtDate = iso => {
    if (!iso) return '—';
    const clean = String(iso).slice(0, 10);
    const [y,m,d] = clean.split('-');
    return y && m && d ? `${d}.${m}.${y}` : iso;
  };
  const fmtDateTime = iso => {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? fmtDate(iso) : new Intl.DateTimeFormat('de-CH',{dateStyle:'medium',timeStyle:'short'}).format(d);
  };
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
  const isDue = app => app.follow_up_date && app.follow_up_date <= todayIso() && !['Abgelehnt','Zurückgezogen'].includes(app.status);
  const statusClass = status => status === 'Angebot' ? 'offer' : status === 'Abgelehnt' ? 'rejected' : /Interview|Assessment/.test(status) ? 'interview' : '';
  const splitLines = value => String(value || '').split(/[\n,;]+/).map(x => x.trim()).filter(Boolean);

  async function api(url, options = {}) {
    const res = await fetch(url, { headers: {'Content-Type':'application/json', ...(options.headers||{})}, ...options });
    if (!res.ok) { let msg = `Fehler ${res.status}`; try { const data = await res.json(); msg = data.error || msg; } catch (_) {} throw new Error(msg); }
    return res.json();
  }

  function toast(message) { const el=$('toast'); el.textContent=message; el.classList.remove('hidden'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.classList.add('hidden'),3200); }
  function openModal(id) { $(id).classList.remove('hidden'); }
  function closeModal(id) { $(id).classList.add('hidden'); }
  function setView(id) {
    document.querySelectorAll('.app-view').forEach(v => v.classList.toggle('hidden', v.id !== id));
    document.querySelectorAll('.view-tab').forEach(t => t.classList.toggle('active', t.dataset.view === id));
    if (id === 'scoutView') loadScout().catch(err => toast(err.message));
  }

  function initSelects() {
    $('status').innerHTML = STATUS.map(s => `<option>${esc(s)}</option>`).join('');
    $('statusFilter').innerHTML += STATUS.map(s => `<option>${esc(s)}</option>`).join('');
  }

  async function loadAll() {
    const params = new URLSearchParams();
    const search=$('searchInput').value.trim(), status=$('statusFilter').value, priority=$('priorityFilter').value;
    if (search) params.set('search',search); if (status) params.set('status',status); if (priority) params.set('priority',priority);
    const qs=params.toString()?`?${params}`:'';
    const [applications,dashboard]=await Promise.all([api(`/api/applications${qs}`),api('/api/dashboard')]);
    state.applications=applications; state.dashboard=dashboard; renderApplications(); renderDashboard();
  }

  function renderDashboard() {
    const d=state.dashboard; if (!d) return;
    $('kpiTotal').textContent=d.total; $('kpiActive').textContent=d.active; $('kpiInterview').textContent=d.interviews; $('kpiOffers').textContent=d.offers;
    $('kpiFollowups').textContent=d.followups_due; $('kpiResponse').textContent=`${Math.round(d.response_rate*100)}%`; $('todayBadge').textContent=d.followups_due;
    $('appTabCount').textContent=d.total; $('scoutTabCount').textContent=d.scout_new; $('scoutNewBadge').textContent=`${d.scout_new} neu`; $('scoutStrongText').textContent=d.scout_strong;
    $('followupList').innerHTML=d.followups.map(f=>`<div class="followup-item"><strong>${esc(f.company)}</strong><small>${esc(f.position)} · ${fmtDate(f.follow_up_date)}</small><div class="followup-copy">${esc(f.next_action||'Follow-up durchführen')}</div><button type="button" class="link-button" data-detail="${f.id}">Öffnen</button></div>`).join('');
    $('noFollowups').classList.toggle('hidden',d.followups.length>0);
    const max=Math.max(1,...d.status_counts.map(x=>x.count));
    $('pipelineList').innerHTML=d.status_counts.length?d.status_counts.map(s=>`<div class="pipeline-row"><span class="pipeline-label">${esc(s.status)}</span><strong>${s.count}</strong><div class="pipeline-bar"><span style="width:${Math.max(6,(s.count/max)*100)}%"></span></div></div>`).join(''):'<p class="muted">Noch keine Pipeline-Daten.</p>';
  }

  function renderApplications() {
    const list=$('applicationList'); const filtered=!!($('searchInput').value||$('statusFilter').value||$('priorityFilter').value);
    $('emptyState').classList.toggle('hidden',state.applications.length!==0||filtered);
    if (!state.applications.length) { list.innerHTML=filtered?'<div class="empty-inline">Keine Bewerbungen für diesen Filter gefunden.</div>':''; return; }
    list.innerHTML=state.applications.map(app=>`<article class="application-card"><div><div class="app-company"><strong>${esc(app.company)}</strong>${app.priority?.startsWith('A')?'<span class="pill a">A-Priorität</span>':''}<span class="pill ${statusClass(app.status)}">${esc(app.status)}</span>${isDue(app)?'<span class="pill due">Follow-up fällig</span>':''}</div><div class="app-position">${esc(app.position)}</div><div class="app-meta">${app.level?`<span>${esc(app.level)}</span>`:''}${app.location?`<span>${esc(app.location)}</span>`:''}${app.work_model?`<span>${esc(app.work_model)}</span>`:''}${app.applied_date?`<span>Beworben ${fmtDate(app.applied_date)}</span>`:''}<span>${app.event_count} Ereignis${app.event_count===1?'':'se'}</span></div></div><div class="app-next"><strong>Nächster Schritt</strong><div>${esc(app.next_action||'Noch nicht gesetzt')}</div>${app.follow_up_date?`<small class="muted">${fmtDate(app.follow_up_date)}</small>`:''}</div><div class="app-actions"><button type="button" class="btn ghost" data-detail="${app.id}">Verlauf öffnen</button><button type="button" class="link-button" data-edit="${app.id}">Bearbeiten</button></div></article>`).join('');
  }

  function resetAppForm(){ $('appForm').reset(); $('appId').value=''; $('status').value='Vorbereitung'; $('priority').value='B - Interessant'; $('appModalTitle').textContent='Neue Bewerbung'; $('deleteAppBtn').classList.add('hidden'); }
  async function editApplication(id){ const app=await api(`/api/applications/${id}`); resetAppForm(); $('appId').value=app.id; $('appModalTitle').textContent=`${app.company} bearbeiten`; if(app.source && !Array.from($('source').options).some(o=>o.value===app.source)){ const o=document.createElement('option'); o.value=app.source; o.textContent=app.source; $('source').appendChild(o); } const map={company:'company',position:'position',level:'level',location:'location',work_model:'workModel',source:'source',job_url:'jobUrl',applied_date:'appliedDate',status:'status',last_response:'lastResponse',summary:'summary',next_action:'nextAction',follow_up_date:'followUpDate',contact_name:'contactName',contact_details:'contactDetails',salary_range:'salaryRange',priority:'priority',cv_version:'cvVersion',cover_letter:'coverLetter',notes:'notes'}; Object.entries(map).forEach(([field,id])=>$(id).value=app[field]||''); $('deleteAppBtn').classList.remove('hidden'); closeModal('detailModal'); openModal('appModal'); }
  function collectApp(){ return {company:$('company').value,position:$('position').value,level:$('level').value,location:$('location').value,work_model:$('workModel').value,source:$('source').value,job_url:$('jobUrl').value,applied_date:$('appliedDate').value,status:$('status').value,last_response:$('lastResponse').value,summary:$('summary').value,next_action:$('nextAction').value,follow_up_date:$('followUpDate').value,contact_name:$('contactName').value,contact_details:$('contactDetails').value,salary_range:$('salaryRange').value,priority:$('priority').value,cv_version:$('cvVersion').value,cover_letter:$('coverLetter').value,notes:$('notes').value}; }

  async function showDetail(id){ const [app,events]=await Promise.all([api(`/api/applications/${id}`),api(`/api/applications/${id}/events`)]); state.selectedId=id; $('detailTitle').textContent=app.company; $('detailSubtitle').textContent=app.position; $('jobLinkBtn').classList.toggle('hidden',!app.job_url); $('jobLinkBtn').href=app.job_url||'#'; $('detailSummary').innerHTML=[['Status',app.status],['Priorität',app.priority],['Beworben',fmtDate(app.applied_date)],['Letzte Rückmeldung',fmtDate(app.last_response)],['Nächste Aktion',app.next_action||'—'],['Follow-up',fmtDate(app.follow_up_date)],['Kontakt',app.contact_name||'—'],['Gehaltsband',app.salary_range||'—']].map(([label,value])=>`<div class="detail-chip"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join(''); $('timeline').innerHTML=events.map(ev=>`<div class="timeline-item"><div class="timeline-date">${fmtDate(ev.event_date)}</div><div class="timeline-dot"></div><div class="timeline-body"><strong>${esc(ev.event_type)}${ev.person?` · ${esc(ev.person)}`:''}</strong>${ev.note?`<p>${esc(ev.note)}</p>`:''}${ev.next_step?`<small>Nächster Schritt: ${esc(ev.next_step)}${ev.follow_up_date?` · ${fmtDate(ev.follow_up_date)}`:''}</small>`:''}</div><button type="button" class="timeline-delete" data-delete-event="${ev.id}" title="Ereignis löschen">×</button></div>`).join(''); $('timelineEmpty').classList.toggle('hidden',events.length>0); openModal('detailModal'); }

  // ---- Job Scout ----
  function salaryText(job){
    if (job.salary_min==null && job.salary_max==null) return '';
    const fmt=n=>Number(n).toLocaleString('de-CH',{maximumFractionDigits:0});
    const range=job.salary_min!=null&&job.salary_max!=null?`${fmt(job.salary_min)}–${fmt(job.salary_max)}`:fmt(job.salary_min??job.salary_max);
    return `${range}${job.salary_currency?' '+job.salary_currency:''}${job.salary_period?' / '+job.salary_period:''}`;
  }
  function scoreClass(score){ return score>=82?'excellent':score>=70?'strong':score>=58?'review':'weak'; }
  function reviewPill(job){ if(job.review_state==='Gemerkt') return '<span class="pill saved">Gemerkt</span>'; if(job.review_state==='Übernommen') return '<span class="pill offer">In Bewerbungen</span>'; if(job.review_state==='Ignoriert') return '<span class="pill rejected">Ignoriert</span>'; return job.is_new?'<span class="pill a">Neu</span>':''; }

  async function loadScout(){
    const params=new URLSearchParams(); const s=$('scoutSearch').value.trim(); const st=$('scoutStateFilter').value; const score=$('scoutScoreFilter').value;
    if(s)params.set('search',s); if(st)params.set('state',st); if(score)params.set('min_score',score); if($('scoutNewOnly').checked)params.set('new_only','1');
    const [jobs,summary,profile,sources]=await Promise.all([api(`/api/scout/jobs?${params}`),api('/api/scout/summary'),state.scoutProfile?Promise.resolve(state.scoutProfile):api('/api/scout/profile'),api('/api/scout/sources')]);
    state.scoutJobs=jobs; state.scoutSummary=summary; state.scoutProfile=profile; state.sources=sources; renderScout(); renderSourcesSummary();
  }

  function renderScout(){
    const s=state.scoutSummary; if(!s)return;
    $('scoutKpiNew').textContent=s.counts.new; $('scoutKpiStrong').textContent=s.counts.strong; $('scoutKpiSaved').textContent=s.counts.saved; $('scoutKpiConverted').textContent=s.counts.converted; $('scoutTabCount').textContent=s.counts.new;
    if(s.last_run){ const r=s.last_run; $('scoutRunStatus').textContent=r.status==='ok'?'Letzte Suche erfolgreich':r.status==='partial'?'Letzte Suche teilweise erfolgreich':'Letzte Suche mit Fehlern'; $('scoutRunMeta').textContent=` ${fmtDateTime(r.finished_at||r.started_at)} · ${r.fetched_count} geprüft · ${r.matched_count} passend · ${r.new_count} neu`; const errs=r.errors||[]; $('scoutError').classList.toggle('hidden',errs.length===0); $('scoutError').innerHTML=errs.length?`<strong>Nicht alle Quellen erreichbar:</strong> ${errs.map(e=>`${esc(e.source)}: ${esc(e.error)}`).join(' · ')}`:''; }
    else { $('scoutRunStatus').textContent='Noch keine Suche ausgeführt.'; $('scoutRunMeta').textContent=s.auto_hours?` Automatisch beim Öffnen nach ${s.auto_hours} h.`:' Automatische Suche ist aus.'; $('scoutError').classList.add('hidden'); }
    const list=$('scoutJobList'); $('scoutEmpty').classList.toggle('hidden',state.scoutJobs.length>0);
    list.innerHTML=state.scoutJobs.map(job=>`<article class="scout-job-card"><div class="score-ring ${scoreClass(job.match_score)}"><strong>${job.match_score}</strong><span>Match</span></div><div class="scout-job-main"><div class="scout-job-top"><div><div class="app-company"><strong>${esc(job.company||'Unbekannt')}</strong><span class="pill">${esc(job.source)}</span>${reviewPill(job)}</div><h3>${esc(job.title)}</h3></div><span class="match-label ${scoreClass(job.match_score)}">${esc(job.match_label)}</span></div><div class="app-meta">${job.location?`<span>${esc(job.location)}</span>`:''}${job.remote===true?'<span>Remote</span>':''}${job.published_at?`<span>Veröffentlicht ${fmtDate(job.published_at)}</span>`:''}${salaryText(job)?`<span>${esc(salaryText(job))}</span>`:''}</div><div class="match-reasons">${(job.match_reasons||[]).map(x=>`<span>${esc(x)}</span>`).join('')}</div>${job.excerpt?`<p class="job-excerpt">${esc(job.excerpt)}</p>`:''}<div class="matched-terms">${(job.matched_terms||[]).slice(0,7).map(x=>`<span>${esc(x)}</span>`).join('')}</div></div><div class="scout-job-actions"><a class="btn ghost" href="${esc(job.job_url)}" target="_blank" rel="noopener noreferrer">Stelle öffnen</a>${job.review_state!=='Übernommen'?`<button class="btn primary" type="button" data-convert-job="${job.id}">In Bewerbungen</button>`:`<button class="btn ghost" type="button" disabled>Übernommen</button>`}${job.review_state!=='Gemerkt'&&job.review_state!=='Übernommen'?`<button class="link-button" type="button" data-job-state="Gemerkt" data-job-id="${job.id}">Merken</button>`:''}${job.review_state!=='Ignoriert'&&job.review_state!=='Übernommen'?`<button class="link-button muted-action" type="button" data-job-state="Ignoriert" data-job-id="${job.id}">Ignorieren</button>`:''}</div></article>`).join('');
  }

  async function runScout(auto=false){
    if(state.scoutBusy)return; state.scoutBusy=true; const btn=$('runScoutBtn'); btn.disabled=true; btn.textContent='Suche läuft …'; $('scoutEmptySearch').disabled=true;
    try { const result=await api('/api/scout/search',{method:'POST',body:'{}'}); toast(result.errors?.length?`Suche beendet: ${result.new_count} neue Treffer, eine Quelle hatte Probleme.`:`${result.new_count} neue passende Jobs gefunden.`); await Promise.all([loadScout(),loadAll()]); }
    catch(err){ toast(`Job-Suche fehlgeschlagen: ${err.message}`); if(!auto)setView('scoutView'); }
    finally { state.scoutBusy=false; btn.disabled=false; btn.textContent='Neue Jobs suchen'; $('scoutEmptySearch').disabled=false; }
  }

  async function openProfile(){ const p=state.scoutProfile||await api('/api/scout/profile'); state.scoutProfile=p; $('targetRoles').value=(p.target_roles||[]).join('\n'); $('profileSkills').value=(p.skills||[]).join('\n'); $('profileLocations').value=(p.locations||[]).join('\n'); $('profileExcludes').value=(p.exclude_keywords||[]).join('\n'); $('profileMinScore').value=p.min_score??58; $('profileMinSalary').value=p.min_salary_chf??235000; $('profileAutoHours').value=p.auto_hours??12; $('profileRemote').checked=!!p.prefer_remote; $('profileHybrid').checked=!!p.allow_hybrid; $('profileEuropeRemote').checked=!!p.include_europe_remote; openModal('profileModal'); }

  function renderSourcesSummary(){
    const enabled=(state.sources||[]).filter(x=>x.enabled).map(x=>x.name);
    $('activeSourcesText').textContent=enabled.length?enabled.join(' · '):'keine';
  }
  function sourceTypeLabel(t){ return ({arbeitnow:'Arbeitnow',jobicy:'Jobicy',remotive:'Remotive',greenhouse:'Greenhouse',lever:'Lever',rss:'RSS / Atom'})[t]||t; }
  function renderSources(){
    const list=$('sourcesList');
    if(!state.sources.length){ list.innerHTML='<p class="muted">Noch keine Quellen eingerichtet.</p>'; return; }
    list.innerHTML=state.sources.map(src=>{
      const cfg=src.config||{};
      const detail=src.source_type==='greenhouse'?(cfg.board_token||'') : src.source_type==='lever'?`${cfg.site||''}${cfg.region?` · ${cfg.region}`:''}` : src.source_type==='rss'?(cfg.url||'') : 'öffentliche API';
      const fixed=['arbeitnow','jobicy','remotive'].includes(src.source_type);
      return `<div class="source-row"><div><strong>${esc(src.name)}</strong><small>${esc(sourceTypeLabel(src.source_type))}${detail?` · ${esc(detail)}`:''}</small></div><label class="switch-line"><input type="checkbox" data-source-toggle="${src.id}" ${src.enabled?'checked':''}> aktiv</label>${fixed?'':`<button type="button" class="link-button muted-action" data-source-delete="${src.id}">Löschen</button>`}</div>`;
    }).join('');
  }
  async function openSources(){ state.sources=await api('/api/scout/sources'); renderSources(); renderSourcesSummary(); openModal('sourcesModal'); }
  function updateSourceFields(){
    const type=$('sourceType').value;
    $('sourceRegionWrap').classList.toggle('hidden',type!=='lever');
    $('sourceUrlWrap').classList.toggle('hidden',type!=='rss');
    $('sourceTokenWrap').classList.toggle('hidden',type==='rss');
    $('sourceCompanyWrap').classList.toggle('hidden',false);
    $('sourceToken').required=type!=='rss'; $('sourceUrl').required=type==='rss';
    $('sourceToken').placeholder=type==='lever'?'z. B. company-site':'z. B. company-board-token';
  }

  // ---- Forms and listeners ----
  $('appForm').addEventListener('submit',async e=>{ e.preventDefault(); try{ const id=$('appId').value,payload=collectApp(); if(id)await api(`/api/applications/${id}`,{method:'PUT',body:JSON.stringify(payload)}); else await api('/api/applications',{method:'POST',body:JSON.stringify(payload)}); closeModal('appModal'); toast(id?'Bewerbung aktualisiert.':'Bewerbung angelegt.'); await loadAll(); }catch(err){toast(err.message);} });
  $('eventForm').addEventListener('submit',async e=>{ e.preventDefault(); try{ if(!state.selectedId)return; const payload={event_date:$('eventDate').value,event_type:$('eventType').value,person:$('eventPerson').value,note:$('eventNote').value,next_step:$('eventNextStep').value,follow_up_date:$('eventFollowUp').value}; await api(`/api/applications/${state.selectedId}/events`,{method:'POST',body:JSON.stringify(payload)}); closeModal('eventModal'); toast('Ereignis gespeichert.'); await loadAll(); await showDetail(state.selectedId); }catch(err){toast(err.message);} });
  $('profileForm').addEventListener('submit',async e=>{ e.preventDefault(); try{ const payload={target_roles:splitLines($('targetRoles').value),skills:splitLines($('profileSkills').value),locations:splitLines($('profileLocations').value),exclude_keywords:splitLines($('profileExcludes').value),min_score:Number($('profileMinScore').value),min_salary_chf:Number($('profileMinSalary').value),auto_hours:Number($('profileAutoHours').value),prefer_remote:$('profileRemote').checked,allow_hybrid:$('profileHybrid').checked,include_europe_remote:$('profileEuropeRemote').checked,jobicy_enabled:true,arbeitnow_enabled:true}; state.scoutProfile=await api('/api/scout/profile',{method:'PUT',body:JSON.stringify(payload)}); closeModal('profileModal'); toast('Filter gespeichert und vorhandene Jobs neu bewertet.'); await loadScout(); }catch(err){toast(err.message);} });

  $('sourceForm').addEventListener('submit',async e=>{ e.preventDefault(); try{ const type=$('sourceType').value; const config={company:$('sourceCompany').value.trim()}; if(type==='greenhouse')config.board_token=$('sourceToken').value.trim(); if(type==='lever'){config.site=$('sourceToken').value.trim();config.region=$('sourceRegion').value;} if(type==='rss')config.url=$('sourceUrl').value.trim(); await api('/api/scout/sources',{method:'POST',body:JSON.stringify({name:$('sourceName').value.trim(),source_type:type,config,enabled:true})}); $('sourceForm').reset(); updateSourceFields(); state.sources=await api('/api/scout/sources'); renderSources(); renderSourcesSummary(); toast('Quelle hinzugefügt.'); }catch(err){toast(err.message);} });
  $('sourceType').addEventListener('change',updateSourceFields);

  $('newAppBtn').addEventListener('click',()=>{resetAppForm();openModal('appModal');}); $('emptyNewBtn').addEventListener('click',()=>{resetAppForm();openModal('appModal');}); $('editFromDetailBtn').addEventListener('click',()=>editApplication(state.selectedId)); $('newEventBtn').addEventListener('click',()=>{$('eventForm').reset();$('eventDate').value=todayIso();openModal('eventModal');});
  $('deleteAppBtn').addEventListener('click',async()=>{const id=$('appId').value;if(!id||!confirm('Diese Bewerbung inklusive Verlauf wirklich löschen?'))return;try{await api(`/api/applications/${id}`,{method:'DELETE'});closeModal('appModal');toast('Bewerbung gelöscht.');await loadAll();}catch(err){toast(err.message);}});
  $('profileBtn').addEventListener('click',()=>openProfile().catch(err=>toast(err.message))); $('sourcesBtn').addEventListener('click',()=>openSources().catch(err=>toast(err.message))); $('runScoutBtn').addEventListener('click',()=>runScout(false)); $('scoutEmptySearch').addEventListener('click',()=>runScout(false));

  document.addEventListener('click',async e=>{
    const close=e.target.closest('[data-close]'); if(close)closeModal(close.dataset.close);
    const tab=e.target.closest('[data-view]'); if(tab)setView(tab.dataset.view);
    const sw=e.target.closest('[data-switch-view]'); if(sw)setView(sw.dataset.switchView);
    const detail=e.target.closest('[data-detail]'); if(detail)showDetail(Number(detail.dataset.detail));
    const edit=e.target.closest('[data-edit]'); if(edit)editApplication(Number(edit.dataset.edit));
    const delEvent=e.target.closest('[data-delete-event]'); if(delEvent&&confirm('Dieses Ereignis löschen?')){try{await api(`/api/events/${delEvent.dataset.deleteEvent}`,{method:'DELETE'});toast('Ereignis gelöscht.');await loadAll();await showDetail(state.selectedId);}catch(err){toast(err.message);}}
    const srcToggle=e.target.closest('[data-source-toggle]'); if(srcToggle){ const src=state.sources.find(x=>x.id===Number(srcToggle.dataset.sourceToggle)); if(src){ try{ await api(`/api/scout/sources/${src.id}`,{method:'PUT',body:JSON.stringify({name:src.name,source_type:src.source_type,config:src.config||{},enabled:srcToggle.checked})}); state.sources=await api('/api/scout/sources'); renderSources(); renderSourcesSummary(); }catch(err){toast(err.message);} } }
    const srcDelete=e.target.closest('[data-source-delete]'); if(srcDelete&&confirm('Diese Jobquelle wirklich löschen?')){ try{await api(`/api/scout/sources/${srcDelete.dataset.sourceDelete}`,{method:'DELETE'}); state.sources=await api('/api/scout/sources'); renderSources(); renderSourcesSummary(); toast('Quelle gelöscht.');}catch(err){toast(err.message);} }
    const stateBtn=e.target.closest('[data-job-state]'); if(stateBtn){try{await api(`/api/scout/jobs/${stateBtn.dataset.jobId}/state`,{method:'PUT',body:JSON.stringify({state:stateBtn.dataset.jobState})});toast(stateBtn.dataset.jobState==='Gemerkt'?'Job gemerkt.':'Job ignoriert.');await Promise.all([loadScout(),loadAll()]);}catch(err){toast(err.message);}}
    const conv=e.target.closest('[data-convert-job]'); if(conv){try{const result=await api(`/api/scout/jobs/${conv.dataset.convertJob}/convert`,{method:'POST',body:'{}'});toast(result.already_exists?'Bewerbung existiert bereits.':'Job in Bewerbungen übernommen.');await Promise.all([loadScout(),loadAll()]);setView('applicationsView');await editApplication(result.application.id);}catch(err){toast(err.message);}}
  });

  ['searchInput','statusFilter','priorityFilter'].forEach(id=>{const el=$(id);el.addEventListener(id==='searchInput'?'input':'change',()=>{clearTimeout(loadAll.timer);loadAll.timer=setTimeout(()=>loadAll().catch(err=>toast(err.message)),180);});});
  ['scoutSearch','scoutStateFilter','scoutScoreFilter','scoutNewOnly'].forEach(id=>{const el=$(id);el.addEventListener(id==='scoutSearch'?'input':'change',()=>{clearTimeout(loadScout.timer);loadScout.timer=setTimeout(()=>loadScout().catch(err=>toast(err.message)),180);});});

  $('backupBtn').addEventListener('click',async()=>{try{const res=await fetch('/api/export');if(!res.ok)throw new Error('Backup konnte nicht erstellt werden.');const blob=await res.blob(),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`bewerbungs-tracker-backup-${todayIso()}.json`;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(a.href);}catch(err){toast(err.message);}});
  $('importFile').addEventListener('change',async e=>{const file=e.target.files[0];e.target.value='';if(!file)return;if(!confirm('Import ersetzt den aktuellen Datenbestand vollständig. Fortfahren?'))return;try{const data=JSON.parse(await file.text());await api('/api/import',{method:'POST',body:JSON.stringify(data)});toast('Backup importiert.');state.scoutProfile=null;await Promise.all([loadAll(),loadScout()]);}catch(err){toast(`Import fehlgeschlagen: ${err.message}`);}});
  document.querySelectorAll('.modal-backdrop').forEach(backdrop=>backdrop.addEventListener('mousedown',e=>{if(e.target===backdrop)closeModal(backdrop.id);})); document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('.modal-backdrop:not(.hidden)').forEach(m=>closeModal(m.id));});

  async function init(){
    initSelects(); updateSourceFields();
    await Promise.all([loadAll(),loadScout()]);
    if(state.scoutSummary?.auto_due && state.scoutSummary?.auto_hours>0){ runScout(true); }
    // While the app stays open, check once per hour whether the configured search interval is due.
    setInterval(async()=>{
      if(state.scoutBusy) return;
      try {
        const summary=await api('/api/scout/summary');
        state.scoutSummary=summary;
        if(summary.auto_due && summary.auto_hours>0) await runScout(true);
      } catch (_) {}
    }, 60*60*1000);
  }
  init().catch(err=>toast(err.message));
})();
