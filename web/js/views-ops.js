/* ==========================================================================
   Views — daily operations: command centre, leads/CRM, pipeline, inbox
   ========================================================================== */
'use strict';

/* ------------------------------- dashboard ------------------------------- */
RE.views.dashboard = async () => {
  const b = await api('/api/v1/bootstrap');
  applyBootstrapState(b);
  const ov = b.overview;
  const learn = { proposals: b.proposals || [] };
  const agents = { agents: b.agents || [] };
  const tasks = { tasks: b.tasks || [] };
  const k = ov.kpi;
  const s = ov.series;
  const prevLeads = s.leads[s.leads.length - 2] || 1;
  const leadDelta = ((s.leads[s.leads.length - 1] - prevLeads) / prevLeads) * 100;
  const pending = learn.proposals.filter((p) => p.status === 'pending');
  const hottest = pending[0];

  return {
    title: 'Command centre',
    crumb: `Today · ${new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })} · Asia/Kolkata`,
    html: `
    <div class="page-head">
      <div>
        <h1>Good ${new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 17 ? 'afternoon' : 'evening'}, ${esc(RE.state.me.user.name.split(' ')[0])}</h1>
        <div class="sub">Every department, one roof. ${fmtNum(k.open_deals)} open deals worth ${fmtMoney(k.pipeline_value)} · ${k.consent_gap} contacts awaiting consent.</div>
      </div>
      <div class="actions">
        <button class="btn" data-act="run-redline">${icon('shield')}Run REDLINE</button>
        <button class="btn primary" data-act="new-lead">${icon('plus')}New lead</button>
      </div>
    </div>

    <div class="grid g-4" style="margin-bottom:14px">
      ${kpiCard('Leads · 30 days', fmtNum(k.leads_30d), leadDelta, s.leads.slice(-8), 'Lead volume', '#4f46e5')}
      ${kpiCard('Site visits', fmtNum(k.site_visits_30d), 8.4, s.site_visits.slice(-8), `${pct(k.site_visits_30d / Math.max(1, k.leads_30d))} of leads`, '#0e7490')}
      ${kpiCard('Bookings', fmtNum(k.bookings_30d), 12.1, s.bookings.slice(-8), `${fmtMoney(k.won_value)} booked`, '#059669')}
      ${kpiCard('Revenue · 30d', fmtMoney(k.revenue_30d), 9.7, s.revenue.slice(-8), 'recognised', '#7c3aed')}
    </div>

    <div class="grid g-2-1" style="margin-bottom:14px">
      <div class="card">
        <div class="card-head"><h2>Demand engine</h2><span class="chip grey">12 months</span>
          <div class="right"><a class="btn sm ghost" href="#/analytics">Open analytics ${icon('arrowRight')}</a></div></div>
        <div class="card-body">${areaChart([
          { name: 'Leads', values: s.leads, color: '#4f46e5', fill: true },
          { name: 'Site visits', values: s.site_visits, color: '#059669' },
          { name: 'Bookings', values: s.bookings, color: '#f59e0b' },
        ], s.months, { height: 226, legend: true })}</div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Today</h2><span class="chip green">Live</span></div>
        <div class="card-body">
          ${[
            ['Site visits booked', ov.today.site_visits_booked, 'target 8'],
            ['AI calls placed', ov.today.ai_calls_placed, 'p95 1.8s'],
            ['WhatsApp sent', ov.today.whatsapp_sent, 'templates only'],
            ['Price holds expiring', ov.today.holds_expiring, 'act now'],
            ['Tasks due', ov.today.tasks_due, 'today'],
          ].map(([l, v, m]) => `<div class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
            <span class="muted small">${esc(l)}</span><span class="spacer"></span>
            <span class="b num">${esc(v)}</span><span class="xs muted" style="width:70px;text-align:right">${esc(m)}</span></div>`).join('')}
          <div class="divider"></div>
          <div class="row small"><span class="muted">Median first response</span><span class="spacer"></span><span class="b">${k.median_first_response_s}s</span></div>
          <div class="row small" style="margin-top:6px"><span class="muted">AI-handled conversations</span><span class="spacer"></span><span class="b">${pct(k.ai_handled_pct)}</span></div>
        </div>
      </div>
    </div>

    <div class="grid g-3" style="margin-bottom:14px">
      <div class="card">
        <div class="card-head"><h2>Conversion funnel</h2></div>
        <div class="card-body">
          ${ov.series.funnel.map((f, i, arr) => barRow(f.stage, f.count, arr[0].count, ['#4f46e5','#6366f1','#818cf8','#22d3ee','#10b981','#059669'][i])).join('')}
          <div class="divider"></div>
          <div class="row small"><span class="muted">Lead → visit</span><span class="spacer"></span><b>${pct(ov.series.funnel[3].count / ov.series.funnel[0].count)}</b></div>
          <div class="row small" style="margin-top:6px"><span class="muted">Visit → booking</span><span class="spacer"></span><b>${pct(ov.series.funnel[5].count / ov.series.funnel[3].count)}</b></div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>AI workforce</h2><span class="chip brand">${agents.agents.length} agents</span>
          <div class="right"><a class="btn sm ghost" href="#/agents">Manage</a></div></div>
        <div class="card-body">
          ${agents.agents.slice(0, 5).map((a) => `<div class="row" style="padding:7px 0;border-bottom:1px solid var(--border)">
            <span class="agent-avatar" style="width:26px;height:26px;font-size:10px;background:${colorFor(a.name)}">${esc(initials(a.name))}</span>
            <div style="min-width:0"><div class="small b" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(a.name.split('—')[0])}</div>
              <div class="xs muted">${esc(a.department)} · ${fmtNum(a.runs)} runs</div></div>
            <span class="spacer"></span>
            <span class="xs num b" style="color:var(--emerald)">${pct(a.success_rate)}</span>
          </div>`).join('')}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Human guardrail queue</h2><span class="chip amber">${pending.length}</span>
          <div class="right"><a class="btn sm ghost" href="#/learning">Review</a></div></div>
        <div class="card-body">
          ${pending.length ? pending.slice(0, 3).map((p) => `<div style="padding:8px 0;border-bottom:1px solid var(--border)">
            <div class="small b">${esc(p.title)}</div>
            <div class="xs muted" style="margin:3px 0 6px">${esc(p.agent_name || 'agent')} · risk ${esc(p.risk)}</div>
            <div class="row">
              <button class="btn sm" data-act="decide" data-id="${esc(p.id)}" data-decision="approve">${icon('check')}Approve</button>
              <button class="btn sm ghost" data-act="decide" data-id="${esc(p.id)}" data-decision="reject">Reject</button>
            </div></div>`).join('') : '<div class="empty">Nothing waiting. Every learned skill has been reviewed.</div>'}
        </div>
      </div>
    </div>

    <div class="grid g-2-1">
      <div class="card">
        <div class="card-head"><h2>Source economics</h2><span class="sub">last 12 months</span></div>
        <div class="table-wrap"><table class="data resp-cards">
          <thead><tr><th>Source</th><th>Leads</th><th>CPL</th><th>Conversion</th><th>CAC</th><th></th></tr></thead>
          <tbody>${ov.series.sources.map((s2) => {
            const cac = s2.cpl && s2.conversion ? Math.round(s2.cpl / s2.conversion) : 0;
            return `<tr><td class="cell-strong">${esc(s2.name)}</td><td class="num">${fmtNum(s2.leads)}</td>
              <td class="num">${s2.cpl ? '₹' + fmtNum(s2.cpl) : '—'}</td>
              <td class="num">${pct(s2.conversion)}</td>
              <td class="num ${cac && cac < 12000 ? 'delta-up' : cac ? 'delta-down' : ''}">${cac ? '₹' + fmtNum(cac) : '—'}</td>
              <td><span class="chip ${s2.conversion > 0.18 ? 'green' : s2.conversion > 0.1 ? 'blue' : 'amber'}">${s2.conversion > 0.18 ? 'scale' : s2.conversion > 0.1 ? 'hold' : 'fix'}</span></td></tr>`;
          }).join('')}</tbody>
        </table></div>
      </div>

      <div class="card">
        <div class="card-head"><h2>Next actions</h2><span class="chip grey">${tasks.tasks.filter((t) => !t.done).length} open</span></div>
        <div class="card-body">
          ${tasks.tasks.filter((t) => !t.done).slice(0, 5).map((t) => `<div class="row" style="padding:8px 0;border-bottom:1px solid var(--border)">
            <button class="icon-btn" data-act="complete-task" data-id="${esc(t.id)}" title="Mark done" style="width:24px;height:24px">${icon('check')}</button>
            <div><div class="small">${esc(t.title)}</div><div class="xs muted">due ${timeAgo(t.due_at)} · ${esc(t.priority)}</div></div>
          </div>`).join('') || '<div class="empty">No open tasks.</div>'}
          <button class="btn sm" data-act="new-task" style="margin-top:10px">${icon('plus')}Add task</button>
        </div>
      </div>
    </div>`,
  };
};

function kpiCard(label, value, delta, spark, meta, color) {
  const up = Number(delta) >= 0;
  return `<div class="card kpi">
    <div class="kpi-label">${esc(label)}</div>
    <div class="kpi-value num">${esc(value)}</div>
    <div class="kpi-meta"><span class="${up ? 'delta-up' : 'delta-down'}">${up ? '▲' : '▼'} ${Math.abs(Number(delta)).toFixed(1)}%</span><span>${esc(meta)}</span></div>
    ${sparkline(spark, color)}
  </div>`;
}

/* --------------------------------- leads --------------------------------- */
RE.views.leads = async () => {
  const [res, tenant] = await Promise.all([api('/api/v1/contacts?limit=200'), api('/api/v1/tenant')]);
  RE.state.contacts = res.contacts;
  RE.state.tenant = tenant;
  const stages = ['', 'new', 'contacted', 'qualified', 'site_visit', 'negotiation', 'won'];
  return {
    title: 'Leads & CRM',
    crumb: `${res.count} contacts · PII masked by role`,
    html: `
    <div class="page-head">
      <div><h1>Leads &amp; CRM</h1>
        <div class="sub">Consent-gated contact records. Phone and email are encrypted at rest and masked unless your role holds <code>pii:read</code>.</div></div>
      <div class="actions">
        <button class="btn" data-act="scrape-modal">${icon('search')}Extract leads</button>
        <button class="btn" data-act="import-csv">${icon('download')}Import CSV</button>
        <button class="btn primary" data-act="new-lead">${icon('plus')}New lead</button>
      </div>
    </div>

    <div class="card" style="margin-bottom:14px">
      <div class="card-head" style="flex-wrap:wrap;gap:10px">
        <input class="input" id="lead-search" aria-label="Search contacts" placeholder="Search name, city, requirement…" style="max-width:280px" value="${esc(RE.state.leadQuery || '')}">
        <div class="row wrap" id="stage-filter">
          ${stages.map((s) => `<button class="chip ${(RE.state.leadStage || '') === s ? 'brand' : ''}" data-act="filter-stage" data-stage="${s}">${s ? esc(STAGE_LABEL[s]) : 'All'}</button>`).join('')}
        </div>
        <span class="right chip grey" id="lead-count">${res.count} shown</span>
      </div>
      <div class="table-wrap"><table class="data resp-cards" id="lead-table">
        <thead><tr><th>Lead</th><th>Score</th><th>Stage</th><th>Source</th><th>Budget</th><th>Consent</th><th>Last touch</th><th></th></tr></thead>
        <tbody>${leadRows(res.contacts)}</tbody>
      </table></div>
    </div>

    <div class="grid g-3">
      <div class="card card-pad">
        <h2>Consent posture</h2>
        <p class="small muted" style="margin-top:6px">Outbound calls and messages are blocked unless consent status is <b>granted</b> — server-side, not a UI warning.</p>
        <div class="progress" style="margin:12px 0 8px"><span style="width:${Math.round((res.contacts.filter((c) => c.consent && c.consent.status === 'granted').length / Math.max(1, res.contacts.length)) * 100)}%"></span></div>
        <div class="xs muted">${res.contacts.filter((c) => c.consent && c.consent.status === 'granted').length} of ${res.contacts.length} contacts contactable (DPDP §6)</div>
      </div>
      <div class="card card-pad">
        <h2>Scoring model</h2>
        <p class="small muted" style="margin-top:6px">Explainable additive model (re-score-v4): source weight, recency, consent, budget fit, intent tags, inventory gap.</p>
        <button class="btn sm" data-act="show-scoring" style="margin-top:10px">${icon('eye')}Inspect drivers</button>
      </div>
      <div class="card card-pad">
        <h2>Dedupe protection</h2>
        <p class="small muted" style="margin-top:6px">Phone numbers are stored as an HMAC blind index, so dedupe works without decrypting anything.</p>
        <div class="mono xs" style="margin-top:10px;background:var(--surface-2);padding:8px;border-radius:8px;word-break:break-all">${esc((res.contacts[0] && res.contacts[0].phone) || '')}</div>
      </div>
    </div>`,
  };
};

function leadRows(contacts) {
  return contacts.map((c) => `<tr data-act="open-lead" data-id="${esc(c.id)}">
    <td><div class="cell-strong">${esc(c.name)}</div><div class="cell-sub">${esc(c.city || '—')} · ${esc(c.requirement || '—')}</div></td>
    <td><span class="score-pill ${c.score >= 75 ? 'a' : c.score >= 55 ? '' : 'c'}">${esc(c.score)}</span></td>
    <td>${stageChip(c.stage)}</td>
    <td><div class="small">${esc(c.source)}</div><div class="cell-sub">${esc(c.phone)}</div></td>
    <td class="num">${c.budget_max ? fmtMoney(c.budget_max) : '—'}</td>
    <td>${c.consent && c.consent.status === 'granted' ? '<span class="chip green">granted</span>' : '<span class="chip amber">pending</span>'}</td>
    <td class="small muted">${timeAgo(c.last_activity_at)}</td>
    <td><span class="chip ${c.intent === 'high' ? 'red' : c.intent === 'medium' ? 'amber' : 'grey'}">${esc(c.intent || '—')}</span></td>
  </tr>`).join('') || '<tr><td colspan="8" class="empty">No contacts match.</td></tr>';
}

document.addEventListener('input', (e) => {
  if (e.target.id !== 'lead-search') return;
  const q = e.target.value.toLowerCase();
  RE.state.leadQuery = e.target.value;
  const rows = (RE.state.contacts || []).filter((c) => {
    const stageOk = !RE.state.leadStage || c.stage === RE.state.leadStage;
    return stageOk && (!q || `${c.name} ${c.city} ${c.requirement} ${c.source}`.toLowerCase().includes(q));
  });
  const tb = document.querySelector('#lead-table tbody');
  if (tb) tb.innerHTML = leadRows(rows);
  const cnt = document.getElementById('lead-count');
  if (cnt) cnt.textContent = rows.length + ' shown';
});

/* per-contact call simulation — delegates to the agent-level simulator */
RE.actions['quick-call'] = (el) => {
  const c = (RE.state.contacts || []).find((x) => x.id === (el && el.dataset.id));
  if (c) toast(`Simulating a call to ${c.name}…`);
  const run = RE.actions['voice-test-quick'];
  if (run) return run(el);
  toast('Voice simulator is still loading — try again in a second.', 'err');
};

/* -------------------------------- pipeline ------------------------------- */
RE.views.pipeline = async () => {
  const p = await api('/api/v1/pipeline');
  RE.state.pipeline = p;
  const total = Object.values(p.columns).flat();
  return {
    title: 'Pipeline',
    crumb: `${total.length} deals · ${fmtMoney(p.total_value)} weighted ${fmtMoney(total.reduce((s, d) => s + (d.value * (d.probability || 0)) / 100, 0))}`,
    html: `
    <div class="page-head">
      <div><h1>Pipeline</h1><div class="sub">Stage changes cascade to the CRM and the audit log. Probability is recomputed server-side.</div></div>
      <div class="actions"><a class="btn" href="/crm.html">Vertical desk</a>
        <button class="btn" data-act="govt-revenue-vault-modal">7/12 &amp; APF Vault</button>
        <button class="btn" data-act="govt-cost-sheet-modal">Statutory Cost Sheet</button>
        <button class="btn" data-act="new-task">${icon('plus')}Task</button>
        <button class="btn primary" data-act="new-lead">${icon('plus')}New lead</button></div>
    </div>
    <div class="kanban">
      ${p.stages.map((s) => {
        const cards = p.columns[s] || [];
        return `<div class="kcol">
          <div class="kcol-head">${stageChip(s)}<span class="count">${cards.length}</span>
            <span class="right xs muted num">${fmtMoney(cards.reduce((a, c) => a + (c.value || 0), 0))}</span></div>
          ${cards.map((d) => `<div class="kcard" data-act="open-deal" data-id="${esc(d.id)}">
            <div class="t">${esc(d.title)}</div>
            <div class="m"><span class="score-pill ${d.score >= 75 ? 'a' : ''}" style="width:22px;height:22px;font-size:10px">${esc(d.score || '—')}</span>
              ${esc(d.contact_name || '')}</div>
            <div class="v num">${fmtMoney(d.value)} <span class="xs muted" style="font-weight:500">· ${esc(d.probability)}%</span></div>
            <div class="row" style="margin-top:8px">
              <select class="input" style="padding:4px 6px;font-size:11px" data-act="move-deal" data-id="${esc(d.id)}">
                ${p.stages.map((s2) => `<option value="${s2}" ${s2 === s ? 'selected' : ''}>${esc(STAGE_LABEL[s2])}</option>`).join('')}
              </select>
            </div>
          </div>`).join('') || '<div class="xs muted" style="padding:8px">Nothing here yet.</div>'}
        </div>`;
      }).join('')}
    </div>`,
  };
};

document.addEventListener('change', (e) => {
  const sel = e.target.closest('[data-act="move-deal"]');
  if (sel) RE.actions['move-deal'](sel);
});

/* deal detail — the pipeline card is the entry point */
RE.actions['open-deal'] = async (el, e) => {
  if (e && e.target.closest('select,option')) return; // let the stage select do its own job
  const id = el && el.dataset ? el.dataset.id : '';
  let p = RE.state.pipeline;
  if (!p) { p = await api('/api/v1/pipeline'); RE.state.pipeline = p; }
  const all = Object.entries(p.columns || {}).flatMap(([stage, arr]) => (arr || []).map((d) => ({ ...d, stage })));
  const d = all.find((x) => x.id === id);
  if (!d) return toast('Deal not found — refresh the pipeline.', 'err');
  const weighted = Math.round((d.value || 0) * (d.probability || 0) / 100);
  openDrawer({
    title: d.title,
    body: `${renderSequenceStepper(d.stage, d.id)}
      <div class="grid g-2">
        <div class="card card-pad">
          <div class="label">Stage</div><div style="margin:6px 0 12px">${stageChip(d.stage)}</div>
          <div class="label">Value</div><div class="num" style="font-size:22px;font-weight:700">${fmtMoney(d.value, false)}</div>
          <div class="xs muted" style="margin-top:4px">Weighted ${fmtMoney(weighted)} at ${esc(d.probability)}%</div>
        </div>
        <div class="card card-pad">
          <div class="label">Contact</div><div style="font-weight:600;margin:6px 0 10px">${esc(d.contact_name || '—')}</div>
          <div class="label">Lead score</div><div style="margin:6px 0 12px"><span class="score-pill ${d.score >= 75 ? 'a' : ''}">${esc(d.score || '—')}</span></div>
          <div class="label">Expected close</div><div>${fmtDate(d.expected_close)}</div>
        </div>
      </div>
      <div id="deal-portal-box" style="margin-top:10px"></div>
      <div class="row wrap" style="margin-top:10px;gap:6px">
        <button class="btn sm" data-act="govt-cost-sheet-modal" data-value="${esc(d.value || 4500000)}">Govt. Cost Sheet</button>
        <button class="btn sm" data-act="deal-govt-desk" data-id="${esc(d.id)}" data-value="${esc(d.value || 4500000)}">e-Reg / Index-II / Loan / E-Ferfar Desk</button>
      </div>
      <p class="muted xs" style="margin-top:12px">Every stage move writes an activity, an audit entry and a <code>deal.stage_changed</code> event. Probability is recomputed server-side — the client cannot set it.</p>`,
    foot: `<button class="btn" data-act="close-drawer">Close</button>
           <button class="btn" data-act="revoke-buyer-portal" data-id="${esc(d.id)}">Revoke Portal</button>
           <button class="btn" data-act="deal-buyer-portal" data-id="${esc(d.id)}">Mint Buyer / NRI Portal</button>
           <button class="btn primary" data-act="goto" data-route="pipeline">Back to pipeline</button>`,
  });
};

RE.actions['govt-revenue-vault-modal'] = async () => {
  const pres = await api('/api/v1/properties');
  const props = pres.properties || [];
  const first = props[0] || {};
  modal({
    title: 'Land & Revenue Vault (7/12, 8A, NA, Boja & Bank APF)',
    wide: true,
    body: `<p class="small muted">Track Maharashtra revenue extracts (<code>7/12 Satbara</code> 30-day freshness, <code>8A</code>, Property Card, Collector NA Order, Mojni, 30-yr Title Search, Class-II permission, active Boja) and approved Bank APF codes.</p>
      <form data-form="govt-revenue-vault" class="grid g-2" style="margin-top:10px">
        <div class="field"><label>Property</label><select class="input" name="property_id">
          ${props.map((p) => `<option value="${esc(p.id)}">${esc(p.title)} (${esc(p.city || 'Nanded')})</option>`).join('')}
        </select></div>
        <div class="field"><label>Occupant Class</label><select class="input" name="occupant_class">
          <option value="class_1">Occupant Class I (Bhogvatadar 1 — Free Transfer)</option>
          <option value="class_2">Occupant Class II (Collector Permission Required)</option>
          <option value="govt_lessee">Government Lessee</option>
        </select></div>
        <div class="field"><label>7/12 Gat / Survey No.</label><input class="input" name="satbara_gat_or_survey_no" value="${esc((first.revenue_vault && first.revenue_vault.satbara_gat_or_survey_no) || 'Gat 142/2B')}" maxlength="80"></div>
        <div class="field"><label>7/12 Extract Date (YYYY-MM-DD)</label><input class="input" name="satbara_extract_date" value="${new Date().toISOString().slice(0, 10)}" maxlength="20"></div>
        <div class="field"><label>8A Khata No.</label><input class="input" name="khata_8a_no" value="8A-4419" maxlength="60"></div>
        <div class="field"><label>Collector NA Order Ref</label><input class="input" name="na_order_ref" value="COL/NND/NA-44/2026" maxlength="80"></div>
        <div class="field"><label>30-Yr Title Search Advocate</label><input class="input" name="title_search_advocate" value="Adv. Deshmukh & Associates" maxlength="100"></div>
        <div class="field"><label>Bank APF Lender &amp; Code (optional)</label><input class="input" name="apf_lender_code" placeholder="SBI · SBI/NND/APF-208" maxlength="100"></div>
      </form>
      <div id="govt-vault-result" style="margin-top:10px"></div>`,
    foot: `<button class="btn" data-act="close-modal">Close</button>
           <button class="btn primary" data-act="submit-form" data-form="govt-revenue-vault">Save &amp; Verify Clearance</button>`,
  });
};

RE.actions['form:govt-revenue-vault'] = async (payload) => {
  const pid = payload.property_id;
  if (!pid) return toast('Select a property first.', 'err');
  const res = await api('/api/v1/properties/' + encodeURIComponent(pid) + '/revenue-vault', {
    method: 'PUT',
    body: {
      satbara_7_12: {
        occupant_class: payload.occupant_class,
        gat_or_survey_no: payload.satbara_gat_or_survey_no,
        issued_at: payload.satbara_extract_date,
        itar_hakk_boja: [],
        bank_noc_obtained: true,
      },
      khata_8a: { khata_no: payload.khata_8a_no, verified: Boolean(payload.khata_8a_no) },
      na_order: { order_no: payload.na_order_ref, verified: Boolean(payload.na_order_ref) },
      title_search: {
        years_searched: 30,
        advocate_name: payload.title_search_advocate,
        clear_marketable_title: Boolean(payload.title_search_advocate),
      },
    },
  });
  if (payload.apf_lender_code && payload.apf_lender_code.includes('·')) {
    const [lender, code] = payload.apf_lender_code.split('·').map((s) => s.trim());
    if (lender && code) {
      await api('/api/v1/properties/' + encodeURIComponent(pid) + '/bank-apf', {
        method: 'POST',
        body: { bank_name: lender, apf_code: code, max_ltv_pct: 80 },
      });
    }
  }
  const box = document.getElementById('govt-vault-result');
  const clr = res.clearance || {};
  if (box) {
    box.innerHTML = `<div class="evidence">REVENUE CLEARANCE: ${clr.can_execute_agreement ? 'CLEAR FOR SALE' : 'BLOCKED'}
7/12 Age: ${esc(clr.satbara_age_days)}d · Blockers: ${esc((clr.blockers || []).map((b) => b.code).join(', ') || 'None')}</div>`;
  }
  toast('Revenue vault & clearance updated', 'ok');
};

RE.actions['govt-cost-sheet-modal'] = async (el) => {
  const defVal = (el && el.dataset && el.dataset.value) || '5500000';
  modal({
    title: 'Maharashtra Statutory Buyer Cost-Sheet Generator',
    wide: true,
    body: `<p class="small muted">Computes Ready Reckoner (ASR) floor, Maharashtra Stamp Duty zone, 1% Women Homebuyer Concession, ₹30,000-capped Registration Fee, GST, and Section 194-IA 1% TDS (≥ ₹50L).</p>
      <form data-form="govt-cost-sheet" class="grid g-2" style="margin-top:10px">
        <div class="field"><label>Agreement Value (₹)</label><input class="input" name="agreement_value_inr" type="number" value="${esc(defVal)}"></div>
        <div class="field"><label>Ready Reckoner / ASR Value (₹)</label><input class="input" name="asr_ready_reckoner_inr" type="number" value="${esc(defVal)}"></div>
        <div class="field"><label>Stamp Duty Zone</label><select class="input" name="local_body_zone">
          <option value="municipal_corp_tier2">Tier-2 Municipal Corp (Nanded NWCMC / Chh. Sambhajinagar — 6%)</option>
          <option value="municipal_corp_metro">Metro Municipal Corp (Mumbai / Pune / Nagpur — 7%)</option>
          <option value="municipal_council">Municipal Council / Nagar Parishad (5%)</option>
          <option value="gram_panchayat_rural">Gram Panchayat / Rural (5%)</option>
        </select></div>
        <div class="field"><label>Buyer Gender Profile</label><select class="input" name="buyer_gender_scheme">
          <option value="standard">General / Standard</option>
          <option value="sole_or_joint_female">Sole / All-Female Residential (-1% Stamp Duty)</option>
        </select></div>
        <div class="field"><label>GST Category</label><select class="input" name="property_gst_category">
          <option value="na_plot_or_oc_ready">Ready OC / Sanctioned NA Plot (0% GST)</option>
          <option value="standard_under_construction">Under-Construction Residential (5% GST)</option>
          <option value="affordable_under_construction">Affordable Housing ≤ ₹45L (1% GST)</option>
          <option value="commercial_under_construction">Under-Construction Commercial (12% GST)</option>
        </select></div>
        <div class="field"><label>Legal / E-Reg Handling (₹)</label><input class="input" name="other_charges_inr" type="number" value="15000"></div>
      </form>
      <div id="govt-cost-sheet-result" style="margin-top:10px"></div>`,
    foot: `<button class="btn" data-act="close-modal">Close</button>
           <button class="btn primary" data-act="submit-form" data-form="govt-cost-sheet">Compute Statutory Sheet</button>`,
  });
};

RE.actions['form:govt-cost-sheet'] = async (payload) => {
  const res = await api('/api/v1/govt/cost-sheet', {
    method: 'POST',
    body: {
      agreement_value_inr: Number(payload.agreement_value_inr || 0),
      asr_ready_reckoner_inr: Number(payload.asr_ready_reckoner_inr || 0),
      local_body_zone: payload.local_body_zone,
      buyer_gender_scheme: payload.buyer_gender_scheme,
      property_gst_category: payload.property_gst_category,
      other_charges_inr: Number(payload.other_charges_inr || 0),
    },
  });
  const cs = res.cost_sheet;
  const tds = cs.tds_194ia || {};
  const box = document.getElementById('govt-cost-sheet-result');
  if (box) {
    box.innerHTML = `<div class="evidence">ASSESSABLE VALUE: ${fmtMoney(cs.assessable_market_value_inr, false)} ${cs.asr_higher_than_agreement ? '(ASR UPLIFT APPLIED)' : ''}
Stamp Duty (${cs.effective_stamp_duty_pct}%): ${fmtMoney(cs.stamp_duty_inr, false)} · Registration: ${fmtMoney(cs.registration_fee_inr, false)} · GST (${cs.gst_pct}%): ${fmtMoney(cs.gst_inr, false)}
Net Payable to Seller: ${fmtMoney(tds.net_payable_to_seller_inr, false)} · Sec 194-IA Buyer TDS (${tds.rate_pct}%): ${fmtMoney(tds.tds_amount_inr, false)}
TOTAL ALL-IN BUYER OUTFLOW: ${fmtMoney(cs.all_in_buyer_outflow_inr, false)}</div>`;
  }
  toast('Statutory cost sheet generated', 'ok');
};

RE.actions['deal-govt-desk'] = async (el) => {
  const did = el.dataset.id;
  const dealVal = Number(el.dataset.value || 5500000);
  const state = await api('/api/v1/deals/' + encodeURIComponent(did) + '/registration');
  const reg = state.govt_registration || {};
  const loan = state.home_loan || {};
  const hnd = state.post_reg_handover || {};
  modal({
    title: 'IGR e-Registration, Index-II, Loan/CLP & E-Ferfar Desk',
    wide: true,
    body: `<div class="grid g-2" style="gap:12px">
      <form data-form="deal-govt-reg" class="card card-pad">
        <input type="hidden" name="deal_id" value="${esc(did)}">
        <div class="b small">1. IGR e-Registration / iSarita SRO &amp; Index-II</div>
        <div class="field" style="margin-top:8px"><label>Execution Mode</label><select class="input" name="mode">
          <option value="e_registration_builder_office" ${reg.mode === 'e_registration_builder_office' ? 'selected' : ''}>IGR e-Registration (Builder Office)</option>
          <option value="isarita_sro_physical" ${reg.mode === 'isarita_sro_physical' ? 'selected' : ''}>iSarita SRO Physical Appointment</option>
        </select></div>
        <div class="field"><label>GRAS e-SBTR Challan No.</label><input class="input" name="gras_challan_no" value="${esc(reg.gras_challan_no || 'MHGRAS2026991244')}"></div>
        <div class="field"><label>Stamp Duty Paid on GRAS (₹)</label><input class="input" name="stamp_duty_paid_inr" type="number" value="${esc(reg.stamp_duty_paid_inr || Math.round(dealVal * 0.06))}"></div>
        <div class="field"><label>Form 26QB Ack (if ≥ ₹50L)</label><input class="input" name="form_26qb_ack_no" value="${esc(reg.form_26qb_ack_no || (dealVal >= 5000000 ? '26QB99881122' : ''))}"></div>
        <div class="field"><label>Registered Doc No. (finalize Index-II)</label><input class="input" name="registered_doc_no" value="${esc(reg.registered_doc_no || '')}" placeholder="NND-1-4821-2026"></div>
        <button class="btn sm primary" type="button" data-act="submit-form" data-form="deal-govt-reg" style="margin-top:8px">Save Reg / Complete Index-II</button>
      </form>
      <form data-form="deal-govt-handover" class="card card-pad">
        <input type="hidden" name="deal_id" value="${esc(did)}">
        <div class="b small">2. Bank Loan, RERA CLP &amp; E-Ferfar Mutation</div>
        <div class="field" style="margin-top:8px"><label>Home Loan Bank</label><input class="input" name="bank_name" value="${esc(loan.bank_name || 'SBI')}"></div>
        <div class="field"><label>Loan Stage</label><select class="input" name="loan_stage">
          ${['login', 'sanctioned', 'legal_technical_clear', 'tripartite_noc_signed', 'disbursed'].map((s) => `<option value="${s}" ${loan.stage === s ? 'selected' : ''}>${s}</option>`).join('')}
        </select></div>
        <div class="field"><label>E-Ferfar Talathi Entry No.</label><input class="input" name="ferfar_entry_no" value="${esc(hnd.ferfar_entry_no || 'FF-NND-2026-771')}"></div>
        <div class="field"><label>Circle Officer &amp; Handover Status</label><select class="input" name="handover_complete">
          <option value="0">15-Day Talathi Notice Running</option>
          <option value="1" ${hnd.possession_letter_issued ? 'selected' : ''}>Circle Officer Certified + Possession Issued</option>
        </select></div>
        <button class="btn sm primary" type="button" data-act="submit-form" data-form="deal-govt-handover" style="margin-top:8px">Save Loan, CLP &amp; E-Ferfar</button>
      </form>
    </div>
    <div id="deal-govt-desk-out" style="margin-top:10px"></div>`,
    foot: `<button class="btn" data-act="close-modal">Close</button>`,
  });
};

RE.actions['form:deal-govt-reg'] = async (payload) => {
  const did = payload.deal_id;
  const regRes = await api('/api/v1/deals/' + encodeURIComponent(did) + '/registration', {
    method: 'PUT',
    body: {
      mode: payload.mode,
      sro_office_name: 'SRO Nanded-1',
      gras_challan_no: payload.gras_challan_no,
      stamp_duty_paid_inr: Number(payload.stamp_duty_paid_inr || 0),
      reg_fee_paid_inr: 30000,
      form_26qb_ack_no: payload.form_26qb_ack_no,
      witness_1_name: 'Ramesh Deshmukh',
      witness_2_name: 'Suresh Kulkarni',
      aadhaar_biometric_ready: true,
    },
  });
  let msg = `Reg Desk Ready: ${regRes.govt_registration.ready_for_execution ? 'YES' : 'NO'}`;
  if (payload.registered_doc_no) {
    const idx = await api('/api/v1/deals/' + encodeURIComponent(did) + '/registration/index-ii', {
      method: 'POST',
      body: { registered_doc_no: payload.registered_doc_no, index_ii_ref: 'IDX2-' + payload.registered_doc_no },
    });
    msg += ` · Index-II Completed (${idx.govt_registration.index_ii_ref})`;
  }
  const out = document.getElementById('deal-govt-desk-out');
  if (out) out.innerHTML = `<div class="evidence">${esc(msg)}</div>`;
  toast('Registration desk updated', 'ok');
};

RE.actions['form:deal-govt-handover'] = async (payload) => {
  const did = payload.deal_id;
  await api('/api/v1/deals/' + encodeURIComponent(did) + '/home-loan', {
    method: 'PUT',
    body: { funding_mode: 'bank_loan', bank_name: payload.bank_name, stage: payload.loan_stage, sanctioned_amount_inr: 3500000 },
  });
  await api('/api/v1/deals/' + encodeURIComponent(did) + '/clp/init', {
    method: 'POST',
    body: { schedule_kind: 'rera_building_7stage' },
  });
  const done = payload.handover_complete === '1';
  const hnd = await api('/api/v1/deals/' + encodeURIComponent(did) + '/handover', {
    method: 'PUT',
    body: {
      ferfar_entry_no: payload.ferfar_entry_no,
      circle_officer_certified: done,
      updated_7_12_or_property_card_issued: done,
      namuna8_municipal_tax_transferred: done,
      mseb_meter_transferred: done,
      snag_list_cleared: done,
      possession_letter_issued: done,
    },
  });
  const out = document.getElementById('deal-govt-desk-out');
  if (out) {
    out.innerHTML = `<div class="evidence">Loan (${esc(payload.loan_stage)}) + RERA CLP + E-Ferfar (${esc(hnd.post_reg_handover.ferfar_entry_no)}) saved · Handover Completion: ${esc(hnd.post_reg_handover.completion_pct)}%</div>`;
  }
  toast('Loan, CLP & E-Ferfar handover updated', 'ok');
};

RE.actions['deal-buyer-portal'] = async (el) => {
  const did = el.dataset.id;
  const box = document.getElementById('deal-portal-box');
  try {
    const res = await api('/api/v1/deals/' + encodeURIComponent(did) + '/buyer-portal', {
      method: 'POST',
      body: { buyer_residency: 'nri', ttl_days: 30 },
    });
    const url = location.origin + '/#/buyer-portal/' + res.token;
    if (box) {
      box.innerHTML = `<div class="evidence">SELF-SERVE BUYER PORTAL READY (FEMA: ${esc(res.fema.status)})
URL: ${esc(url)}</div>
      <div style="margin-top:6px"><a class="btn sm" href="#/buyer-portal/${esc(res.token)}" target="_blank" rel="noopener noreferrer">Open Buyer Self-Serve Portal</a></div>`;
    }
    toast('Buyer / NRI portal link minted', 'ok');
  } catch (_) {}
};

RE.actions['revoke-buyer-portal'] = async (el) => {
  const did = el.dataset.id;
  try {
    await api('/api/v1/deals/' + encodeURIComponent(did) + '/buyer-portal', { method: 'DELETE' });
    const box = document.getElementById('deal-portal-box');
    if (box) box.innerHTML = '<div class="xs muted">Buyer portal pass revoked immediately.</div>';
    toast('Buyer portal pass revoked', 'ok');
  } catch (_) {}
};

/* --------------------------------- inbox --------------------------------- */
RE.views.inbox = async () => {
  const res = await api('/api/v1/inbox');
  RE.state.threads = res.threads;
  RE.state.inboxBadge = res.threads.filter((t) => t.unread).length;
  const first = RE.state.activeThread || (res.threads[0] && res.threads[0].contact_id);
  let detail = null;
  if (first) detail = await api('/api/v1/inbox/' + first);
  RE.state.activeThread = first;

  return {
    title: 'Unified inbox',
    crumb: 'WhatsApp · SMS · voice · web chat — one thread per buyer',
    html: `
    <div class="page-head">
      <div><h1>Unified inbox</h1><div class="sub">Code-mixed conversations (mr/hi/en). AI drafts are guardrail-checked; sending requires consent and respects quiet hours.</div></div>
      <div class="actions"><button class="btn" data-act="voice-test-quick">${icon('phone')}Simulate call</button>
        <button class="btn primary" data-act="new-lead">${icon('plus')}New lead</button></div>
    </div>
    <div class="card" style="overflow:hidden">
      <div class="inbox">
        <div class="threads">${res.threads.map((t) => `<div class="thread ${t.contact_id === first ? 'active' : ''}" data-act="open-thread" data-id="${esc(t.contact_id)}">
          <div class="top"><span class="name">${esc(t.contact_name)}</span>
            ${t.unread ? '<span class="chip brand xs">new</span>' : ''}
            <span class="when">${timeAgo(t.last_at)}</span></div>
          <div class="prev">${esc(t.last_text)}</div>
          <div class="row" style="margin-top:6px">${stageChip(t.stage || 'new')}
            ${t.ai_handled ? '<span class="chip violet xs">AI</span>' : ''}
            <span class="xs muted right">${esc(t.channel)}</span></div>
        </div>`).join('')}</div>
        <div class="chat">${renderChat(detail)}</div>
        <aside class="inbox-context" aria-label="Buyer context">
          ${detail?.contact ? `<h2>Buyer context</h2><dl class="kv" style="margin-top:16px"><dt>Buyer</dt><dd>${esc(detail.contact.name)}</dd><dt>Stage</dt><dd>${stageChip(detail.contact.stage)}</dd><dt>Budget</dt><dd>${fmtMoney(detail.contact.budget_max, false)}</dd><dt>City</dt><dd>${esc(detail.contact.city || '—')}</dd><dt>Consent</dt><dd>${esc(detail.contact.consent?.status || 'pending')}</dd></dl><p class="muted small" style="margin:16px 0">Contact details stay masked according to your role. Consent and quiet-hour checks are enforced before sending.</p><button class="btn" data-act="open-lead" data-id="${esc(detail.contact.id)}">Open buyer profile</button>` : '<div class="empty">Select a buyer to see context.</div>'}
        </aside>
      </div>
    </div>`,
  };
};

function renderChat(detail) {
  if (!detail) return '<div class="empty">Select a conversation.</div>';
  const c = detail.contact;
  const msgs = (detail.conversation && detail.conversation.messages) || [];
  const consentOk = c.consent && c.consent.status === 'granted';
  return `
  <div class="chat-head">
    <span class="agent-avatar" style="background:${colorFor(c.name)}">${esc(initials(c.name))}</span>
    <div><div class="b">${esc(c.name)}</div><div class="xs muted">${esc(c.phone)} · ${esc(c.city || '')} · ${esc(c.requirement || '')}</div></div>
    <div class="right row">
      <span class="chip ${consentOk ? 'green' : 'amber'}">${consentOk ? 'consent granted' : 'consent pending'}</span>
      <span class="score-pill ${c.score >= 75 ? 'a' : ''}">${esc(c.score)}</span>
      <button class="btn sm" data-act="open-lead" data-id="${esc(c.id)}">${icon('eye')}Profile</button>
    </div>
  </div>
  <div class="chat-body" id="chat-body">
    ${msgs.map((m) => `<div class="msg ${m.dir === 'out' ? 'out' : 'in'}">
      <div class="bub">${esc(m.text)}</div>
      <div class="meta">${m.ai ? '<span class="chip violet xs">AI</span>' : ''}${esc(m.channel)} · ${timeAgo(m.at)} · ${esc(m.status)}</div>
    </div>`).join('') || '<div class="empty">No messages yet.</div>'}
  </div>
  <form class="composer" data-form="send-msg" data-contact="${esc(c.id)}">
    <textarea name="text" aria-label="Message reply" placeholder="${consentOk ? 'Reply in mr / hi / en…' : 'Consent required before messaging'}" ${consentOk ? '' : 'disabled'} required maxlength="2000"></textarea>
    <select class="input" name="channel" aria-label="Reply channel" style="width:120px">
      <option value="whatsapp">WhatsApp</option><option value="sms">SMS</option><option value="voice">Voice note</option><option value="email">Email</option>
    </select>
    <button class="btn" type="button" data-act="ai-draft" data-id="${esc(c.id)}">${icon('sparkles')}AI draft</button>
    <button class="btn primary" type="submit" ${consentOk ? '' : 'disabled'}>${icon('arrowRight')}Send</button>
  </form>`;
}

/* ------------------------------- actions --------------------------------- */
Object.assign(RE.actions, {
  async 'new-lead'() {
    modal({
      title: 'New lead',
      body: `<form data-form="create-lead" id="create-lead">
        <div class="grid g-2">
          <div class="field"><label>Name</label><input class="input" name="name" required maxlength="80"></div>
          <div class="field"><label>Phone (WhatsApp)</label><input class="input" name="phone" required placeholder="+919812345678" maxlength="20"></div>
          <div class="field"><label>Email</label><input class="input" name="email" type="email" maxlength="254"></div>
          <div class="field"><label>City</label><input class="input" name="city" maxlength="60"></div>
          <div class="field"><label>Requirement</label><input class="input" name="requirement" maxlength="200" placeholder="2400 sqft east-facing plot"></div>
          <div class="field"><label>Budget max (₹)</label><input class="input" name="budget_max" type="number" min="0" step="100000" value="3000000"></div>
        </div>
        <div class="field"><label>Source</label>
          <select class="input" name="source">
            <option>Website</option><option>99acres</option><option>MagicBricks</option><option>Meta Lead Ads</option>
            <option>Google Ads</option><option>WhatsApp CTWA</option><option>Referral</option><option>Walk-in</option><option>Manual</option>
          </select></div>
        <div class="check"><input type="checkbox" name="consent_granted" id="cg"><label for="cg" style="font-weight:400">Consent captured (purpose-bound, notice v3.1). Without this the lead is created but cannot be contacted.</label></div>
      </form>`,
      foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="create-lead">Create lead</button>`,
    });
  },

  async 'submit-form'(el) {
    const form = document.getElementById(el.dataset.form);
    if (form) form.requestSubmit();
  },

  'form:create-lead': async (payload) => {
    try {
      const body = {
        name: payload.name,
        phone: payload.phone,
        email: payload.email,
        city: payload.city,
        requirement: payload.requirement,
        budget_max: Number(payload.budget_max || 0),
        source: payload.source,
        consent: { status: payload.consent_granted ? 'granted' : 'pending', purpose: 'site visit follow-up', channel: payload.source },
      };
      const res = await api('/api/v1/contacts', { method: 'POST', body });
      closeModal();
      toast(`Lead created · score ${res.scoring.score} (${res.scoring.band})`, 'ok');
      route();
    } catch (_) {}
  },

  async 'import-csv'() {
    modal({
      title: 'Import leads (CSV)',
      body: `<p class="small muted">Paste CSV rows as <code>name,phone,email,city,requirement</code>. Duplicates are skipped using the encrypted blind index. Imported leads start as <b>consent pending</b> — they are not contactable until consent is recorded (DPDP §6).</p>
        <div class="field"><label>CSV</label><textarea class="input" id="csv" placeholder="Aarav Patil,+919812345678,aarav@example.com,Nanded,2400 sqft plot" style="min-height:140px"></textarea></div>`,
      foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="do-import">Import</button>`,
    });
  },

  async 'do-import'() {
    const raw = (document.getElementById('csv') || {}).value || '';
    const rows = raw
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((l) => {
        const [name, phone, email, city, requirement] = l.split(',').map((x) => (x || '').trim());
        return { name, phone, email, city, requirement };
      });
    try {
      const res = await api('/api/v1/leads/import', { method: 'POST', body: { rows } });
      closeModal();
      toast(`Imported ${res.accepted} · rejected ${res.rejected.length}`, 'ok');
      route();
    } catch (_) {}
  },

  async 'scrape-modal'() {
    const jobs = await api('/api/v1/leads/jobs');
    modal({
      title: 'Lead extraction — compliance-gated',
      wide: true,
      body: `<p class="small muted">Extraction only runs through lawful channels: partner APIs (99acres, MagicBricks), Meta/Google lead webhooks, and a robots-respecting crawler of <i>public business listings</i>. LinkedIn and direct portal scraping are hard-blocked and every attempt is written to the audit log.</p>
        <form data-form="scrape" class="grid g-2" style="margin-top:10px">
          <div class="field"><label>Source</label><select class="input" name="source">
            <option value="99acres">99acres — partner API</option>
            <option value="magicbricks">MagicBricks — partner API</option>
            <option value="meta_lead_ads">Meta Lead Ads — webhook</option>
            <option value="google_ads">Google Ads — lead form API</option>
            <option value="public_web">Public web — business listings crawler</option>
            <option value="linkedin">LinkedIn (blocked by policy)</option>
            <option value="magicbricks_scrape">MagicBricks direct scrape (blocked)</option>
          </select></div>
          <div class="field"><label>Keyword / intent</label><input class="input" name="keyword" value="plots" maxlength="120"></div>
          <div class="field"><label>City</label><input class="input" name="city" value="Nanded" maxlength="60"></div>
          <div class="field"><label>Limit</label><input class="input" name="limit" type="number" min="1" max="10" value="5"></div>
        </form>
        <div class="divider"></div>
        <h3>Recent extraction jobs</h3>
        <div class="small muted" style="margin-top:8px">${(jobs.jobs || []).slice(0, 4).map((j) => `<div class="row" style="padding:6px 0;border-bottom:1px solid var(--border)">
          <span class="chip blue">${esc(j.source)}</span><span class="small">${esc(j.keyword)} · ${esc(j.city)}</span>
          <span class="right xs muted">${esc(j.count)} candidates · ${timeAgo(j.created_at)}</span></div>`).join('') || '<div class="empty">No jobs yet.</div>'}</div>`,
      foot: `<button class="btn" data-act="close-modal">Close</button><button class="btn primary" data-act="submit-form" data-form="scrape">Run extraction</button>`,
    });
  },

  'form:scrape': async (payload) => {
    try {
      const res = await api('/api/v1/leads/scrape', { method: 'POST', body: { ...payload, limit: Number(payload.limit || 5) } });
      closeModal();
      modal({
        title: 'Extraction complete — consent required',
        body: `<p class="small muted">${esc(res.note)}</p>
          <table class="data resp-cards"><thead><tr><th>Name</th><th>Phone</th><th>Interest</th><th>Lawful basis</th><th>Next step</th></tr></thead>
          <tbody>${res.candidates.map((c) => `<tr><td class="cell-strong">${esc(c.name)}</td><td class="mono">${esc(c.phone_masked)}</td>
            <td>${esc(c.interest)}</td><td class="small muted">${esc(c.lawful_basis)}</td><td><span class="chip amber">capture consent</span></td></tr>`).join('')}</tbody></table>`,
        foot: `<button class="btn primary" data-act="close-modal">Done</button>`,
      });
      toast(`${res.candidates.length} candidates extracted through ${res.job.mode}`, 'ok');
    } catch (_) {}
  },

  async 'open-lead'(el) {
    const id = el.dataset.id;
    try {
      const d = await api('/api/v1/contacts/' + id);
      const c = d.contact;
      RE.state.currentLead = c;
      modal({
        title: c.name,
        wide: true,
        body: `<div class="grid g-1-2">
          <div>
            <dl class="kv">
              <dt>Phone</dt><dd class="mono">${esc(c.phone)}</dd>
              <dt>Email</dt><dd class="mono">${esc(c.email)}</dd>
              <dt>City</dt><dd>${esc(c.city || '—')}</dd>
              <dt>Source</dt><dd>${esc(c.source)}</dd>
              <dt>Stage</dt><dd>${stageChip(c.stage)}</dd>
              <dt>Budget</dt><dd>${fmtMoney(c.budget_min)} – ${fmtMoney(c.budget_max)}</dd>
              <dt>Requirement</dt><dd>${esc(c.requirement || '—')}</dd>
              <dt>Consent</dt><dd>${stageChip((c.consent || {}).status || 'pending')} <span class="xs muted">${esc((c.consent || {}).notice_version || '')}</span></dd>
              <dt>Tags</dt><dd>${(c.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join(' ')}</dd>
            </dl>
            <div class="divider"></div>
            <h3>Explainable score · ${esc(d.scoring.score)} (${esc(d.scoring.band)})</h3>
            <div class="small muted" style="margin:6px 0">${esc(d.scoring.next_best_action)}</div>
            ${d.scoring.drivers.map((x) => `<div class="row xs" style="padding:2px 0"><span class="muted">${esc(x.factor)}</span><span class="spacer"></span>
              <span class="b ${x.impact >= 0 ? 'delta-up' : 'delta-down'}">${x.impact > 0 ? '+' : ''}${esc(x.impact)}</span></div>`).join('')}
          </div>
          <div>
            <div class="row wrap" style="margin-bottom:10px">
              <button class="btn sm" data-act="capture-consent" data-id="${esc(c.id)}" data-status="granted">${icon('check')}Capture consent</button>
              <button class="btn sm ghost" data-act="capture-consent" data-id="${esc(c.id)}" data-status="withdrawn">Withdraw</button>
              <button class="btn sm" data-act="open-thread" data-id="${esc(c.id)}">${icon('chat')}Open thread</button>
              <button class="btn sm" data-act="quick-call" data-id="${esc(c.id)}">${icon('phone')}AI call test</button>
            </div>
            <form data-form="add-note" data-contact="${esc(c.id)}" class="row" style="gap:6px">
              <input class="input" name="text" placeholder="Log a note…" maxlength="1000" required>
              <button class="btn sm" type="submit">Add</button>
            </form>
            <div class="divider"></div>
            <h3>Timeline</h3>
            <ul class="timeline" style="margin-top:10px">
              ${(d.timeline || []).slice(0, 8).map((t) => `<li><div class="when">${timeAgo(t.at)} · ${esc(t.actor || '')}</div>
                <div class="what">${esc(t.text)}</div></li>`).join('') || '<li><div class="what muted">No activity yet.</div></li>'}
            </ul>
          </div></div>`,
        foot: `<button class="btn" data-act="close-modal">Close</button>
          <button class="btn primary" data-act="stage-select" data-id="${esc(c.id)}">${icon('layers')}Move stage</button>`,
      });
    } catch (_) {}
  },

  async 'capture-consent'(el) {
    try {
      await api(`/api/v1/contacts/${el.dataset.id}/consent`, { method: 'POST', body: { status: el.dataset.status, purpose: 'site visit follow-up', channel: 'manual' } });
      toast('Consent ledger updated — audit entry written', 'ok');
      closeModal();
      route();
    } catch (_) {}
  },

  async 'stage-select'(el) {
    const id = el.dataset.id;
    const stages = ['new', 'contacted', 'qualified', 'site_visit', 'negotiation', 'won', 'lost'];
    modal({
      title: 'Move stage',
      body: `<div class="row wrap">${stages.map((s) => `<button class="btn" data-act="set-stage" data-id="${esc(id)}" data-stage="${s}">${esc(STAGE_LABEL[s] || s)}</button>`).join('')}</div>`,
    });
  },

  async 'set-stage'(el) {
    try {
      await api('/api/v1/deals/' + el.dataset.id, { method: 'PATCH', body: { stage: el.dataset.stage } }).catch(async () => {
        await api('/api/v1/contacts/' + el.dataset.id, { method: 'PATCH', body: { stage: el.dataset.stage } });
      });
      closeModal();
      toast('Stage updated', 'ok');
      route();
    } catch (_) {
      try {
        await api('/api/v1/contacts/' + el.dataset.id, { method: 'PATCH', body: { stage: el.dataset.stage } });
        closeModal();
        toast('Stage updated', 'ok');
        route();
      } catch (_) {}
    }
  },

  'move-deal': async (el) => {
    try {
      await api('/api/v1/deals/' + el.dataset.id, { method: 'PATCH', body: { stage: el.value } });
      toast('Deal moved · probability recalculated', 'ok');
      route();
    } catch (_) {}
  },

  async 'open-thread'(el) {
    RE.state.activeThread = el.dataset.id;
    closeModal();
    location.hash = '#/inbox';
    RE.state.route = 'inbox';
    const view = await RE.views.inbox();
    renderShell(view.html, { title: view.title, crumb: view.crumb });
  },

  async 'open-thread-inline'() {},

  'form:send-msg': async (payload, form) => {
    const cid = form.dataset.contact;
    try {
      const res = await api(`/api/v1/inbox/${cid}/send`, { method: 'POST', body: { text: payload.text, channel: payload.channel } });
      toast(`Sent via ${payload.channel} · guardrail: ${res.guardrail.action}`, 'ok');
      RE.state.activeThread = cid;
      route();
    } catch (_) {}
  },

  async 'ai-draft'(el) {
    try {
      const res = await api(`/api/v1/inbox/${el.dataset.id}/ai-draft`, { method: 'POST', body: {} });
      const ta = document.querySelector('.composer textarea');
      if (ta) { ta.value = res.draft; ta.focus(); }
      toast(`Draft from ${res.agent} (${res.guardrail.action}, risk ${res.guardrail.score})`, 'ok');
    } catch (_) {}
  },

  async 'complete-task'(el) {
    try {
      await api('/api/v1/tasks/' + el.dataset.id, { method: 'PATCH', body: { done: true } });
      toast('Task completed');
      route();
    } catch (_) {}
  },

  async 'new-task'() {
    modal({
      title: 'New task',
      body: `<form data-form="create-task"><div class="field"><label>Task</label><input class="input" name="title" required maxlength="140"></div>
        <div class="field"><label>Priority</label><select class="input" name="priority"><option>high</option><option selected>medium</option><option>low</option></select></div></form>`,
      foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="create-task">Create</button>`,
    });
  },

  'form:create-task': async (payload) => {
    try {
      await api('/api/v1/tasks', { method: 'POST', body: { title: payload.title, priority: payload.priority } });
      closeModal();
      toast('Task created', 'ok');
      route();
    } catch (_) {}
  },

  'form:add-note': async (payload, form) => {
    try {
      await api(`/api/v1/contacts/${form.dataset.contact}/note`, { method: 'POST', body: { text: payload.text } });
      closeModal();
      toast('Note logged', 'ok');
      route();
    } catch (_) {}
  },

  'filter-stage'(el) {
    RE.state.leadStage = el.dataset.stage || '';
    route();
  },

  async 'show-scoring'() {
    const res = await api('/api/v1/leads/scoring');
    modal({
      title: 'Lead scoring — explainable',
      wide: true,
      body: `<p class="small muted">Model <b>${esc(res.model)}</b>. Every score decomposes into weighted drivers so a sales head can argue with it.</p>
        <table class="data resp-cards"><thead><tr><th>Lead</th><th>Score</th><th>Band</th><th>Next best action</th><th>Top drivers</th></tr></thead>
        <tbody>${res.scored.slice(0, 12).map((s) => `<tr><td class="cell-strong">${esc(s.name)}</td><td><span class="score-pill ${s.score >= 75 ? 'a' : ''}">${esc(s.score)}</span></td>
          <td>${esc(s.band)}</td><td class="small">${esc(s.next_best_action)}</td>
          <td class="small muted">${s.drivers.slice(0, 3).map((d) => `${esc(d.factor)} ${d.impact > 0 ? '+' : ''}${d.impact}`).join(' · ')}</td></tr>`).join('')}</tbody></table>`,
      foot: `<button class="btn primary" data-act="close-modal">Close</button>`,
    });
  },
});
