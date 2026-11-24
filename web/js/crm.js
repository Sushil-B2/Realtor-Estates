/* Vertical desk — pipeline, site visits, stage gates and commission.
 *
 * Plain DOM, no framework, no external calls: the app's CSP is
 * `script-src 'self'; connect-src 'self'`, and the preview iframe has no
 * network of its own. Everything here talks to the same origin.
 */
(() => {
  'use strict';

  const S = { csrf: '', me: null, summary: null, pipeline: null, visits: [], contacts: [], properties: [], deal: null, sla: null, sources: [], cpRegs: [], cpPartners: [], cpPayouts: [], canApprove: false, canPay: false, invProp: '', inv: null,
    // property taxonomy (docs/22): the catalogue, the selected category, and the
    // field metadata for whichever category's form is on screen
    tax: null, taxCat: 'residential', taxFields: {}, taxBooks: {},
    // automation runtime (docs/23): fleet, playbook catalogue, run queue
    autos: [], autQueue: null, autBooks: [],
    // money (docs/24): the board, the documents, and the last thing we reconciled
    money: null, invoices: [], moneyDeal: '' };
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const inr = (n) => {
    const v = Number(n || 0);
    if (v >= 1e7) return '₹' + (v / 1e7).toFixed(2).replace(/\.00$/, '') + 'Cr';
    if (v >= 1e5) return '₹' + (v / 1e5).toFixed(1).replace(/\.0$/, '') + 'L';
    if (v >= 1e3) return '₹' + Math.round(v / 1e3) + 'k';
    return '₹' + Math.round(v);
  };
  const money = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });

  const dt = (epoch) => {
    if (!epoch) return '—';
    const d = new Date(Number(epoch) * 1000);
    return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  };
  const dayKey = (epoch) => new Date(Number(epoch) * 1000).toISOString().slice(0, 10);

  function toast(msg, kind) {
    const box = $('toast');
    const el = document.createElement('div');
    el.className = kind || '';
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), 5200);
  }

  async function api(path, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRF-Token'] = S.csrf;
    }
    const res = await fetch(path, { method, headers, credentials: 'same-origin', body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) {
      const err = new Error((data.error && data.error.message) || ('HTTP ' + res.status));
      err.status = res.status;
      err.code = data.error && data.error.code;
      err.detail = data.error && data.error.details;
      throw err;
    }
    return data;
  }

  /* ---------------------------------------------------------------- boot */
  async function boot() {
    try {
      const session = await api('/api/v1/auth/session');
      S.csrf = session.csrf;
      S.me = session;
    } catch (e) {
      $('who').textContent = 'Session expired — open the console to sign in.';
      document.querySelector('.wrap').innerHTML = '<div class="card"><div class="body">' +
        '<p class="empty">You are not signed in. <a href="/">Open the console</a>, sign in, then come back to this page.</p></div></div>';
      return;
    }
    $('who').textContent = (S.me.tenant && S.me.tenant.name ? S.me.tenant.name : '') + ' · ' + S.me.user.name + ' (' + S.me.user.role + ')';
    if (!S.me.permissions.write) $('visitform').querySelector('button').disabled = true;
    $('refresh').addEventListener('click', (e) => { e.preventDefault(); load(); });
    $('visitform').addEventListener('submit', scheduleVisit);
    $('tax_save').addEventListener('click', saveListing);
    $('aut_tick').addEventListener('click', () => runTick(false));
    $('moneyissue').addEventListener('click', issueInvoice);
    $('moneyreconcile').addEventListener('click', reconcileMoney);
    $('moneydeal').addEventListener('input', (e) => { S.moneyDeal = e.target.value.trim(); });
    $('aut_dry').addEventListener('click', () => runTick(true));
    $('aut_install').addEventListener('click', installPlaybook);
    $('q_run').addEventListener('click', runSearch);
    if (!S.me.permissions.write) $('tax_save').disabled = true;
    await load();
  }

  async function load() {
    try {
      const [summary, pipeline, visits, contacts, properties, sla, sources] = await Promise.all([
        api('/api/v1/crm/summary'),
        api('/api/v1/pipeline'),
        api('/api/v1/site-visits?limit=80'),
        api('/api/v1/contacts?limit=200'),
        api('/api/v1/properties'),
        api('/api/v1/leads/sla').catch(() => null),
        api('/api/v1/leads/sources').catch(() => null),
      ]);
      S.summary = summary; S.pipeline = pipeline; S.visits = visits.visits || [];
      S.contacts = contacts.contacts || []; S.properties = properties.properties || properties.items || [];
      S.sla = sla; S.sources = (sources && sources.sources) || [];
      renderTiles(); renderBoard(); renderVisits(); renderBlockers(); renderPickers();
      renderSla(); renderSources();
      await loadPartners();
      renderInventoryPicker();
      await loadTaxonomy();
      await loadAutomations();
      await loadMoney();
      await loadInventory();
      if (S.deal) openDeal(S.deal.deal.id, true);
    } catch (e) {
      toast(e.message, 'bad');
    }
  }

  /* --------------------------------------------------------------- tiles */
  function renderTiles() {
    const s = S.summary || {};
    const tiles = [
      ['Pipeline value', inr(s.pipeline_value), (s.deals || 0) + ' open deals'],
      ['Won', inr(s.won_value), 'closed value'],
      ['Visits today', String(s.visits_today || 0), (s.visits_upcoming || 0) + ' upcoming'],
      ['Contacts', String(s.contacts || 0), (s.new_contacts_today || 0) + ' new today'],
      ['Gated deals', String((s.stage_blockers || []).length), 'need a document or a visit', 'warn'],
    ];
    $('tiles').innerHTML = tiles.map(([k, v, n, cls]) =>
      `<div class="tile ${cls || ''}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`
    ).join('');
  }

  /* --------------------------------------------------------------- board */
  function renderBoard() {
    const p = S.pipeline || { stages: [], columns: {} };
    $('boardnote').textContent = (p.stages || []).length + ' stages · ' + inr(p.total_value);
    $('board').innerHTML = (p.stages || []).map((stage) => {
      const rows = (p.columns && p.columns[stage]) || [];
      const sum = rows.reduce((a, d) => a + Number(d.value || 0), 0);
      return `<div class="col"><h3><span>${esc(stage.replace(/_/g, ' '))}</span><span class="count">${rows.length}</span></h3>` +
        rows.map((d) => `<div class="deal${S.deal && S.deal.deal.id === d.id ? ' sel' : ''}" data-deal="${esc(d.id)}">
            <div class="t">${esc(d.title || 'Untitled')}</div>
            <div class="m"><span class="v">${inr(d.value)}</span><span>·</span><span>${esc(d.contact_name || '')}</span>
            <span class="pill">${Number(d.probability || 0)}%</span></div>
          </div>`).join('') +
        `<div class="hint" style="margin-top:2px">${rows.length ? inr(sum) : '—'}</div></div>`;
    }).join('');
    $('board').querySelectorAll('[data-deal]').forEach((el) =>
      el.addEventListener('click', () => openDeal(el.dataset.deal)));
  }

  /* --------------------------------------------------------------- visits */
  function renderVisits() {
    const rows = S.visits.slice().sort((a, b) => Number(b.scheduled_at) - Number(a.scheduled_at));
    $('visitnote').textContent = rows.length + ' visits';
    if (!rows.length) {
      $('visits').innerHTML = '<tr><td colspan="6" class="empty">No visits yet. Schedule one above — the agent\'s calendar will refuse an overlap.</td></tr>';
      return;
    }
    $('visits').innerHTML = rows.map((v) => {
      const actions = (v.allowed_next || []).map((n) =>
        `<button class="ghost" data-visit="${esc(v.id)}" data-to="${esc(n)}">${esc(n)}</button>`).join('');
      const cls = { visited: 'ok', confirmed: 'info', revisit: 'warn', dropped: 'bad', scheduled: '' }[v.status] || '';
      return `<tr>
        <td>${esc(dt(v.scheduled_at))}<div class="hint">${esc(v.duration_min || 45)} min</div></td>
        <td>${esc(v.contact_name || v.contact_id)}<div class="hint">${esc(v.agent_name || '')}</div></td>
        <td>${esc(v.property_title || v.property_id)}</td>
        <td><span class="pill ${cls}">${esc(v.status)}</span>${v.outcome ? `<div class="hint">${esc(v.outcome)}</div>` : ''}</td>
        <td>${esc(v.feedback || '')}</td>
        <td style="white-space:nowrap">${actions || '<span class="hint">—</span>'}</td>
      </tr>`;
    }).join('');
    $('visits').querySelectorAll('[data-visit]').forEach((b) =>
      b.addEventListener('click', () => moveVisit(b.dataset.visit, b.dataset.to)));
  }

  function renderPickers() {
    $('v_contact').innerHTML = S.contacts.map((c) => `<option value="${esc(c.id)}">${esc(c.name)} · ${esc(c.city || '')}</option>`).join('');
    $('v_property').innerHTML = S.properties.map((p) => `<option value="${esc(p.id)}">${esc(p.title)} · ${inr(p.price)}</option>`).join('');
    if (!$('v_when').value) {
      const d = new Date(Date.now() + 26 * 3600 * 1000);
      d.setMinutes(0, 0, 0);
      const pad = (x) => String(x).padStart(2, '0');
      $('v_when').value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }
  }

  async function scheduleVisit(ev) {
    ev.preventDefault();
    const when = new Date($('v_when').value);
    if (isNaN(when.getTime())) return toast('Pick a valid date and time.', 'bad');
    try {
      const res = await api('/api/v1/site-visits', {
        method: 'POST',
        body: {
          contact_id: $('v_contact').value,
          property_id: $('v_property').value,
          scheduled_at: Math.floor(when.getTime() / 1000),
          duration_min: Number($('v_dur').value || 45),
          pickup: $('v_pickup').value,
        },
      });
      toast('Visit scheduled for ' + dt(res.visit.scheduled_at), 'ok');
      $('v_pickup').value = '';
      await load();
    } catch (e) {
      if (e.code === 'VISIT_CONFLICT') toast('Slot taken — that agent already has a visit then. Pick another time.', 'bad');
      else toast(e.message, 'bad');
    }
  }

  async function moveVisit(id, to) {
    try {
      await api('/api/v1/site-visits/' + encodeURIComponent(id), { method: 'PATCH', body: { status: to } });
      toast('Visit → ' + to, 'ok');
      await load();
    } catch (e) {
      toast(e.message, 'bad');
    }
  }

  /* ------------------------------------------------------------- blockers */
  function renderBlockers() {
    const rows = (S.summary && S.summary.stage_blockers) || [];
    if (!rows.length) {
      $('blockers').innerHTML = '<div class="empty">Nothing stuck: every open deal can move to its next stage.</div>';
      return;
    }
    $('blockers').innerHTML = '<ul class="steps">' + rows.map((b) =>
      `<li><span class="mark no">!</span><div><a href="#" data-deal-link="${esc(b.deal_id)}" style="color:var(--brand);text-decoration:none">${esc(b.title || b.deal_id)}</a>
        <div class="why">${esc(b.detail || b.code)}</div></div></li>`).join('') + '</ul>';
    $('blockers').querySelectorAll('[data-deal-link]').forEach((a) =>
      a.addEventListener('click', (e) => { e.preventDefault(); openDeal(a.dataset.dealLink); }));
  }

  /* ----------------------------------------------------------------- SLA */
  function clockText(row) {
    if (row.status === 'open') return Math.max(0, Math.round(row.remaining_sec)) + 's left';
    if (row.status === 'breached') return '+' + Math.round((row.overdue_sec || 0) / 60) + ' min over';
    if (row.status === 'met') return 'in ' + Math.round(row.responded_in_sec) + 's';
    if (row.status === 'met_late') return 'late: ' + Math.round(row.responded_in_sec) + 's';
    return '—';
  }

  function renderSla() {
    const b = S.sla;
    if (!b) {
      $('sla').innerHTML = '<tr><td colspan="5" class="empty">Speed-to-lead needs a write-scope session.</td></tr>';
      return;
    }
    $('slanote').textContent = (b.avg_response_sec != null ? 'avg first reply ' + b.avg_response_sec + 's' : 'no replies yet');
    $('slatiles').innerHTML = [
      ['Inside window', String(b.counts.open), 'still catchable', b.counts.open ? '' : 'good'],
      ['Breached', String(b.counts.breached), 'no reply yet', b.counts.breached ? 'warn' : 'good'],
      ['Answered', String(b.counts.met), 'leads with a first reply'],
    ].map(([k, v, n, cls]) => `<div class="tile ${cls || ''}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');

    const rows = (b.breached || []).concat(b.open || []);
    if (!rows.length) {
      $('sla').innerHTML = '<tr><td colspan="5" class="empty">Nothing waiting: every lead has had a first reply.</td></tr>';
      return;
    }
    $('sla').innerHTML = rows.map((r) => `<tr>
        <td>${esc(r.name)}</td>
        <td>${esc(r.source || '—')}</td>
        <td class="num" data-clock="${esc(r.contact_id)}">${esc(clockText(r))}</td>
        <td><span class="pill ${r.status === 'breached' ? 'bad' : 'warn'}">${esc(r.status)}</span></td>
        <td><button class="ghost" data-respond="${esc(r.contact_id)}">Reply logged</button></td>
      </tr>`).join('');
    $('sla').querySelectorAll('[data-respond]').forEach((btn) =>
      btn.addEventListener('click', () => respond(btn.dataset.respond)));
  }

  async function respond(contactId) {
    try {
      const res = await api('/api/v1/leads/' + encodeURIComponent(contactId) + '/respond', { method: 'POST', body: { channel: 'call' } });
      toast(res.already_responded ? 'Already had a first reply.' :
        'First reply logged ' + (res.sla.status === 'met' ? 'inside' : 'outside') + ' the window (' + res.sla.responded_in_sec + 's).',
        res.sla.status === 'met' ? 'ok' : 'bad');
      await load();
    } catch (e) { toast(e.message, 'bad'); }
  }

  function renderSources() {
    if (!S.sources.length) { $('sources').innerHTML = '<div class="empty">No adapters.</div>'; return; }
    const max = Math.max(1, ...S.sources.map((s) => s.leads || 0));
    $('sources').innerHTML = S.sources.map((s) => `
      <div style="margin-bottom:8px">
        <div class="row" style="justify-content:space-between"><span style="font-size:12.5px">${esc(s.label)}</span>
          <span class="hint">${s.leads || 0}</span></div>
        <div class="bar"><i style="width:${Math.round(((s.leads || 0) / max) * 100)}%"></i></div>
      </div>`).join('') +
      '<div class="hint" style="margin-top:10px">Feeds post to <code>/api/v1/ingest/&lt;source&gt;</code> with an <code>X-Ingest-Key</code> header. A repeat enquiry from the same number is merged and the new source appended — attribution survives to booking.</div>';
  }



  /* -------------------------------------------------------- automations */
  // The runtime is a tick, not a cron: this panel shows what fired, what was
  // refused and why. A run that did not send must always say why — that is the
  // difference between a compliant automation and a silent one.
  async function loadAutomations() {
    try {
      const [fleet, queue, books] = await Promise.all([
        api('/api/v1/automations'),
        api('/api/v1/automations/queue'),
        api('/api/v1/automations/playbooks'),
      ]);
      S.autos = fleet.automations || [];
      S.autQueue = queue;
      S.autBooks = books.playbooks || [];
      renderAutomations();
    } catch (e) {
      $('autnote').textContent = 'runtime unavailable';
    }
  }

  function renderAutomations() {
    const q = S.autQueue || { counts: {}, recent: [] };
    const counts = q.counts || {};
    const tiles = [
      ['Enabled', S.autos.filter((a) => a.enabled).length, S.autos.length + ' installed'],
      ['Fired ok', counts.ok || 0, 'steps completed'],
      ['Deferred', counts.deferred || 0, 'quiet hours / cap'],
      ['Blocked', counts.blocked || 0, 'consent, DND, kill switch'],
      ['Dead', counts.dead || 0, 'gave up after ' + (q.max_attempts || 4) + ' attempts'],
    ];
    $('auttiles').innerHTML = tiles.map(([k, v, n]) =>
      '<div class="tile' + (k === 'Dead' && v ? ' warn' : '') + '"><div class="k">' + esc(k) + '</div>' +
      '<div class="v">' + esc(v) + '</div><div class="n">' + esc(n) + '</div></div>').join('');
    $('autnote').textContent = (q.due_now || 0) + ' due now · tick every ' + (q.tick_seconds || 15) + 's · ' +
      'cap ' + (q.frequency_cap_per_day || 6) + '/contact/day';

    $('autfleet').innerHTML = S.autos.map((a) => {
      const st = a.stats || {};
      const parts = ['ok', 'deferred', 'blocked', 'dead'].filter((k) => st[k]).map((k) => k + ' ' + st[k]);
      return '<tr><td><strong>' + esc(a.name) + '</strong>' +
        (a.playbook ? ' <span class="pill">' + esc(a.playbook) + '</span>' : '') + '</td>' +
        '<td>' + esc(a.trigger_label || a.trigger) + '</td>' +
        '<td>' + (a.step_meta || []).map((s) => '<span class="pill' + (s.outbound ? ' info' : '') + '">' + esc(s.type) + '</span>').join(' ') + '</td>' +
        '<td class="num">' + esc(a.runs || 0) + '</td>' +
        '<td>' + (a.enabled ? '<span class="pill ok">on</span>' : '<span class="pill">paused</span>') +
        (parts.length ? ' <span class="hint">' + esc(parts.join(' · ')) + '</span>' : '') + '</td></tr>';
    }).join('') || '<tr><td colspan="5" class="empty">No automations installed.</td></tr>';

    const installed = new Set(S.autos.map((a) => a.playbook).filter(Boolean));
    $('aut_book').innerHTML = S.autBooks.filter((b) => !installed.has(b.slug)).map((b) =>
      '<option value="' + esc(b.slug) + '">' + esc(b.name) + '</option>').join('') ||
      '<option value="">all playbooks installed</option>';

    const waiting = [].concat(q.dead || [], q.blocked || [], q.deferred || []).slice(0, 8);
    $('autdead').innerHTML = waiting.length ? waiting.map((r) =>
      '<div class="result"><div class="top"><span>' + esc(r.automation || '') + '</span>' +
      '<span class="pill ' + (r.status === 'dead' ? 'bad' : r.status === 'blocked' ? 'warn' : 'info') + '">' + esc(r.status) + '</span></div>' +
      '<div class="meta">' + esc(r.subject_kind || '') + ' ' + esc(r.subject_id || '') +
      (r.next_at ? ' · retry ' + esc(dt(r.next_at)) : '') + '</div>' +
      '<div class="hint">' + esc(r.last_error || 'no reason recorded') + '</div></div>').join('')
      : '<div class="empty">Nothing blocked, deferred or dead.</div>';
  }

  async function runTick(dry) {
    try {
      const res = await api('/api/v1/automations/tick', { method: 'POST', body: { dry_run: dry, limit: 100 } });
      const c = res.counts || {};
      $('autresult').textContent = (dry ? 'dry run: ' : '') + (c.due || 0) + ' due · ' +
        (c.ok || 0) + ' ok · ' + (c.deferred || 0) + ' deferred · ' + (c.blocked || 0) + ' blocked';
      toast((dry ? 'Dry run — nothing sent' : 'Tick complete') + ' · planned ' + (c.planned || 0), 'ok');
      await loadAutomations();
    } catch (e) {
      toast(e.message + (e.code ? ' [' + e.code + ']' : ''), 'bad');
    }
  }

  async function installPlaybook() {
    const slug = $('aut_book').value;
    if (!slug) return toast('Every playbook is already installed', 'bad');
    try {
      const res = await api('/api/v1/automations/playbooks/' + encodeURIComponent(slug) + '/install', { method: 'POST', body: {} });
      toast('Installed: ' + res.automation.name, 'ok');
      await loadAutomations();
    } catch (e) {
      toast(e.message + (e.code ? ' [' + e.code + ']' : ''), 'bad');
    }
  }


  /* --------------------------------------------------------------- money */
  // Two documents, two tax treatments (docs/24). A token is the buyer's money in
  // custody — a receipt with no GST. Only our commission is a taxable supply, so
  // that is the document with a GST split and a TDS deduction on its face.
  async function loadMoney() {
    try {
      const [summary, invoices] = await Promise.all([
        api('/api/v1/money/summary'),
        api('/api/v1/invoices'),
      ]);
      S.money = summary;
      S.invoices = invoices.invoices || [];
      renderMoney();
    } catch (e) {
      $('moneynote').textContent = 'money unavailable';
    }
  }

  function renderMoney() {
    const m = S.money || {};
    const rates = m.rates || {};
    $('moneytiles').innerHTML = [
      ['Collected', inr(m.collected), (m.token_held ? inr(m.token_held) + ' held for the seller' : 'no booking amounts')],
      ['Invoiced', inr(m.invoiced), (m.counts && m.counts.unpaid_invoices || 0) + ' unpaid'],
      ['GST payable', inr(m.gst), 'SAC ' + esc(rates.sac || '') + ' · ' + (rates.gst_pct || 0) + '%'],
      ['TDS withheld', inr(m.tds_withheld), '194-H ' + (rates.tds_194h_pct || 0) + '% above ' + inr(rates.tds_194h_threshold_inr)],
      ['Receivable', inr(m.receivable), 'net of TDS'],
    ].map(([label, value, note]) => `<div class="tile"><span>${esc(label)}</span><strong>${esc(value)}</strong><em>${esc(note)}</em></div>`).join('');

    $('moneynote').textContent = S.invoices.length + ' documents · one running series';
    const rows = S.invoices.slice(0, 12);
    $('moneyinvoices').innerHTML = rows.length ? rows.map((i) => {
      const gst = i.gst || {};
      const tds = i.tds || {};
      const net = i.net_receivable != null ? i.net_receivable : i.total;
      const docLabel = {
        receipt: 'Receipt',
        tax_invoice: 'Tax invoice',
        credit_note: 'Credit note (§34)',
        refund_voucher: 'Refund voucher',
      }[i.type] || 'Tax invoice';
      return `<tr>
        <td><code>${esc(i.number)}</code></td>
        <td>${esc(docLabel)}</td>
        <td>${esc(i.deal_id || '')}</td>
        <td>${esc(money(i.taxable))}</td>
        <td>${gst.total_paise ? esc(money(gst.total)) + (gst.place_of_supply === 'inter_state' ? ' IGST' : ' CGST+SGST') : '—'}</td>
        <td>${tds.withheld_paise ? esc(money(tds.withheld)) : '—'}</td>
        <td>${esc(money(i.total))}</td>
        <td>${esc(money(net))}</td>
        <td>${esc(i.status)}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="9" class="empty">No documents yet — a settled token issues a receipt.</td></tr>';
  }

  async function issueInvoice() {
    const id = (S.moneyDeal || $('moneydeal').value || '').trim();
    if (!id) return toast('Enter a deal id (the pipeline card shows it)', 'bad');
    try {
      const res = await api('/api/v1/deals/' + encodeURIComponent(id) + '/invoice', { method: 'POST', body: {} });
      const inv = res.invoice;
      $('moneyresult').textContent = inv.number + ' · ' + money(inv.total) + ' incl. GST, net ' + money(inv.net_receivable);
      toast('Tax invoice ' + inv.number + ' issued', 'ok');
      await loadMoney();
    } catch (e) {
      toast(e.message + (e.code ? ' [' + e.code + ']' : ''), 'bad');
    }
  }

  async function reconcileMoney() {
    try {
      const res = await api('/api/v1/payments/reconcile', { method: 'POST', body: { limit: 50 } });
      $('moneyresult').textContent = 'scanned ' + res.scanned + ' · ' + JSON.stringify(res.reconciled || {});
      toast('Reconciled ' + res.scanned + ' verified event(s)', 'ok');
      await loadMoney();
    } catch (e) {
      toast(e.message + (e.code ? ' [' + e.code + ']' : ''), 'bad');
    }
  }

  /* ------------------------------------------------------------ taxonomy */
  // Five categories, five forms. Nothing here hard-codes a property field: the
  // form is generated from GET /taxonomy, so the server's schema is the only
  // place a field is ever declared.
  async function loadTaxonomy() {
    if (!S.tax) {
      try { S.tax = await api('/api/v1/taxonomy'); }
      catch (e) { $('taxnote').textContent = 'taxonomy unavailable'; return; }
    }
    const cats = S.tax.categories;
    S.taxBooks = {};
    for (const c of cats) S.taxBooks[c.slug] = c;

    const unitOpts = S.tax.area_units.map((u) => '<option value="' + esc(u.unit) + '">' + esc(u.unit) + '</option>').join('');
    $('tax_unit').innerHTML = unitOpts;
    $('q_unit').innerHTML = unitOpts;
    $('tax_basis').innerHTML = S.tax.price_bases.map((b) => '<option value="' + esc(b) + '">' + esc(b) + '</option>').join('');

    $('taxcats').innerHTML = cats.map((c) =>
      '<button type="button" class="catbtn" data-cat="' + esc(c.slug) + '" aria-pressed="' + (c.slug === S.taxCat) + '">' +
      esc(c.label) + '<span class="n">' + c.types.length + ' types · ' + c.fields.length + ' fields</span></button>').join('');
    $('taxcats').querySelectorAll('[data-cat]').forEach((b) => b.addEventListener('click', () => {
      S.taxCat = b.dataset.cat;
      $('taxcats').querySelectorAll('[data-cat]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.cat === S.taxCat)));
      renderTaxForm();
    }));

    $('q_cat').innerHTML = '<option value="">any category</option>' +
      cats.map((c) => '<option value="' + esc(c.slug) + '">' + esc(c.label) + '</option>').join('');
    $('q_deal').innerHTML = '<option value="">any</option>' +
      S.tax.deal_kinds.map((d) => '<option value="' + esc(d) + '">' + esc(d) + '</option>').join('');
    $('taxnote').textContent = 'bigha refused · ' + S.tax.area_units.length + ' area units · ' + S.tax.price_bases.length + ' price bases';
    renderTaxForm();
  }

  function renderTaxForm() {
    const cat = S.taxBooks[S.taxCat];
    if (!cat) return;
    $('tax_type').innerHTML = cat.types.map((t) =>
      '<option value="' + esc(t.slug) + '">' + esc(t.label) + '</option>').join('');
    $('tax_deal').innerHTML = cat.deal_kinds.map((d) =>
      '<option value="' + esc(d) + '">' + esc(d) + '</option>').join('');
    $('tax_basis').value = cat.default_price_basis[cat.deal_kinds[0]] || 'total';
    $('tax_unit').value = cat.slug === 'land' ? 'acre' : 'sqft';
    S.taxFields = {};
    $('tax_fields').innerHTML = cat.fields.map((f) => {
      S.taxFields[f.name] = f;
      const mark = f.required ? ' <span class="req">*</span>' : '';
      const lab = esc(f.label) + (f.unit ? ' <span class="hint">(' + esc(f.unit) + ')</span>' : '') + mark;
      let input;
      if (f.type === 'enum') {
        input = '<select id="tf_' + esc(f.name) + '"><option value="">—</option>' +
          f.options.map((o) => '<option value="' + esc(o) + '">' + esc(o) + '</option>').join('') + '</select>';
      } else if (f.type === 'bool') {
        input = '<input id="tf_' + esc(f.name) + '" type="checkbox">';
      } else if (f.type === 'int' || f.type === 'float') {
        input = '<input id="tf_' + esc(f.name) + '" type="number" step="' + (f.type === 'int' ? '1' : 'any') + '" min="0">';
      } else if (f.type === 'multi') {
        input = '<input id="tf_' + esc(f.name) + '" type="text" placeholder="comma separated">';
      } else {
        input = '<input id="tf_' + esc(f.name) + '" type="text" maxlength="200">';
      }
      return '<label class="f" title="' + esc(f.help || '') + '">' + lab + input + '</label>';
    }).join('');
    const required = cat.required_fields.concat(cat.required_any || []);
    $('tax_req').textContent = required.length
      ? 'required for ' + cat.label + ': ' + required.map((r) => (S.taxFields[r] || {}).label || r).join(', ')
      : '';
  }

  function collectAttributes() {
    const out = {};
    for (const [name, meta] of Object.entries(S.taxFields)) {
      const el = $('tf_' + name);
      if (!el) continue;
      if (meta.type === 'bool') {
        if (el.checked) out[name] = true;
        continue;
      }
      const raw = el.value.trim();
      if (raw === '') continue;
      if (meta.type === 'int') out[name] = parseInt(raw, 10);
      else if (meta.type === 'float') out[name] = Number(raw);
      else if (meta.type === 'multi') out[name] = raw.split(',').map((v) => v.trim()).filter(Boolean);
      else out[name] = raw;
    }
    return out;
  }

  async function saveListing() {
    const payload = {
      title: $('tax_title').value.trim(),
      city: $('tax_city').value.trim(),
      locality: $('tax_locality').value.trim(),
      category: S.taxCat,
      type: $('tax_type').value,
      deal_kind: $('tax_deal').value,
      pricing: { basis: $('tax_basis').value, amount: Number($('tax_price').value || 0) },
      attributes: collectAttributes(),
    };
    const area = $('tax_area').value.trim();
    if (area) payload.area = { value: Number(area), unit: $('tax_unit').value };
    try {
      const res = await api('/api/v1/properties', { method: 'POST', body: payload });
      const m = res.property.money;
      toast('Saved ' + String(res.property.type).replace(/_/g, ' ') + ' · ' +
        (m.monthly ? inr(m.monthly) + '/mo' : inr(m.total || m.amount)), 'ok');
      $('tax_title').value = '';
      await load();
    } catch (e) {
      toast(e.message + (e.code ? ' [' + e.code + ']' : ''), 'bad');
      if (Array.isArray(e.detail) && e.detail.length) console.warn('property validation', e.detail);
    }
  }

  async function runSearch() {
    const body = { limit: 8 };
    if ($('q_cat').value) body.category = $('q_cat').value;
    if ($('q_deal').value) body.deal_kind = $('q_deal').value;
    if ($('q_budget').value) body.budget_max = Number($('q_budget').value);
    if ($('q_bhk').value) body.bedrooms_min = Number($('q_bhk').value);
    if ($('q_amin').value) body.area_min = Number($('q_amin').value);
    if ($('q_amax').value) body.area_max = Number($('q_amax').value);
    body.area_unit = $('q_unit').value;
    if ($('q_must').value.trim()) body.must_have = $('q_must').value.split(',').map((v) => v.trim()).filter(Boolean);
    if ($('q_city').value.trim()) body.city = $('q_city').value.trim();
    try {
      const res = await api('/api/v1/properties/search', { method: 'POST', body });
      $('q_note').textContent = res.count + ' of ' + res.candidates + ' candidates · ' +
        res.scanned + ' scanned across ' + res.categories_scanned.length + ' categories';
      if (!res.results.length) {
        $('q_results').innerHTML = '<div class="empty">Nothing matched those hard filters. Widen the category or clear a field.</div>';
        return;
      }
      $('q_results').innerHTML = res.results.map((r) => {
        const p = r.property;
        const m = p.money;
        const figure = m.monthly ? inr(m.monthly) + '/mo' : inr(m.total || m.amount);
        const areaTxt = p.area
          ? (p.area.sqft >= 43560 ? (p.area.sqft / 43560).toFixed(2) + ' acre' : Math.round(p.area.sqft).toLocaleString('en-IN') + ' sqft')
          : '—';
        return '<div class="result"><div class="top"><span>' + esc(p.title) + '</span>' +
          '<span class="scoredot">' + r.score + '</span></div>' +
          '<div class="bar"><i style="width:' + r.score + '%"></i></div>' +
          '<div class="meta">' + esc(p.category) + ' · ' + esc(String(p.type).replace(/_/g, ' ')) + ' · ' + esc(p.deal_kind) +
          ' · ' + esc(areaTxt) + ' · ' + figure + (p.locality ? ' · ' + esc(p.locality) : '') + '</div>' +
          '<ul>' + r.reasons.slice(0, 4).map((x) => '<li>' + esc(x) + '</li>').join('') + '</ul></div>';
      }).join('');
    } catch (e) {
      toast(e.message + (e.code ? ' [' + e.code + ']' : ''), 'bad');
    }
  }

  /* ----------------------------------------------------------- inventory */
  function renderInventoryPicker() {
    const sel = $('invprop');
    const current = sel.value || S.invProp;
    sel.innerHTML = S.properties.map((p) => `<option value="${esc(p.id)}">${esc(p.title)} · ${esc(p.city || '')}</option>`).join('');
    if (current && S.properties.some((p) => p.id === current)) sel.value = current;
    sel.onchange = () => { S.invProp = sel.value; loadInventory(); };
    S.invProp = sel.value;
  }

  async function loadInventory() {
    const pid = $('invprop').value;
    if (!pid) return;
    try {
      const [data, media] = await Promise.all([
        api('/api/v1/properties/' + encodeURIComponent(pid) + '/units'),
        api('/api/v1/properties/' + encodeURIComponent(pid) + '/media').catch(() => null),
      ]);
      S.inv = data;
      renderInventory();
      renderPhotos(media, data.property);
    } catch (e) {
      $('invnote').textContent = e.message;
    }
  }

  /* ---------------------------------------------------------------- photos */
  // A listing photo is a luxury this pipeline does not assume: the media table
  // lives in Postgres and the variants are private objects, so this panel has
  // three honest states — photos, "no photos yet" (a monogram tile in the
  // listing's own hue), and "this instance cannot show photos" with the reason.
  // Nothing here ever falls back to a stock photograph of somebody else's
  // building.
  function monogram(placeholder) {
    const ph = placeholder || { initials: '?', hue: 210, label: 'no photo yet', kind: '' };
    return `<div class="photo mono" style="--h:${Number(ph.hue) || 210}">
      <span>${esc(ph.initials || '?')}</span>
      <em>${esc(ph.label || 'no photo yet')}</em>
    </div>`;
  }

  function renderPhotos(media, prop) {
    const box = $('invphotos');
    if (!media) {
      box.innerHTML = `<div class="photostate">Photos are served from the Postgres media pipeline; this instance answered with nothing
        (<code>${esc('media unavailable')}</code>).${prop ? ' ' + monogram(null) : ''}</div>`;
      return;
    }
    const photos = media.photos || [];
    const ready = media.readiness || {};
    const head = `<div class="photostate">
        <strong>${photos.length ? photos.length + ' photo' + (photos.length === 1 ? '' : 's') : 'No photos yet'}</strong>
        <span class="hint">${esc(ready.note || '')}</span>
        <span class="slots">${Array.from({ length: ready.slots || 5 }).map((_, i) =>
          `<i class="${i < (ready.have || 0) ? 'on' : ''}"></i>`).join('')}</span>
      </div>`;
    if (!photos.length) {
      box.innerHTML = head + monogram(media.placeholder);
      return;
    }
    box.innerHTML = head + `<div class="photogrid">${photos.map((p, i) => `
      <figure class="${i === 0 ? 'cover' : ''}">
        ${p.url ? `<img src="${esc(p.url)}" alt="${esc((prop && prop.title) || '')} — photo ${i + 1}"
             width="${Number(p.width) || 320}" height="${Number(p.height) || 213}" loading="lazy"
             onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'photo mono',textContent:'photo unavailable'}))">`
          : '<div class="photo mono">photo unavailable</div>'}
        <figcaption>${i === 0 ? 'cover · ' : ''}${esc(p.variant || '')} ${p.width ? p.width + '×' + p.height : ''}</figcaption>
      </figure>`).join('')}</div>`;
  }

  function renderInventory() {
    const data = S.inv;
    if (!data) return;
    const s = data.summary || {};
    $('invnote').textContent = `${s.total || 0} units · ${s.availability_pct || 0}% sellable`;
    $('invtiles').innerHTML = [
      ['Available', String(s.counts.available || 0), money(s.value.available || 0)],
      ['On hold', String(s.counts.blocked || 0), money(s.value.blocked || 0), s.counts.blocked ? 'warn' : ''],
      ['Booked', String(s.counts.booked || 0), money(s.value.booked || 0)],
      ['Sold', String(s.counts.sold || 0), money(s.value.sold || 0), 'good'],
    ].map(([k, v, n, cls]) => `<div class="tile ${cls || ''}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');

    if (!(data.units || []).length) {
      $('invgrid').innerHTML = '<div class="empty">No units yet — generate a floor grid with <code>POST /properties/{id}/units/bulk</code>.</div>';
      return;
    }
    $('invgrid').innerHTML = data.units.map((u) => {
      const held = u.status === 'blocked' ? `${Math.round(u.block_hours_left || 0)}h left` : '';
      const acts = [];
      if (u.status === 'available' || u.status === 'blocked') acts.push(`<button data-unit-block="${esc(u.id)}">hold</button>`);
      if (u.status === 'blocked' || u.status === 'available') acts.push(`<button data-unit-book="${esc(u.id)}">book</button>`);
      if (u.status === 'blocked' || u.status === 'booked') acts.push(`<button data-unit-release="${esc(u.id)}">release</button>`);
      if (u.status === 'available' || u.status === 'blocked' || u.status === 'booked') acts.push(`<button data-unit-withdraw="${esc(u.id)}">withdraw</button>`);
      if (u.status === 'withdrawn') acts.push(`<button data-unit-withdraw="${esc(u.id)}" data-reinstate="1">reinstate</button>`);
      return `<div class="unit ${esc(u.status)}">
        <div class="no"><span>${esc(u.unit_no)}</span><span class="pill">${esc(u.status)}</span></div>
        <div class="pr">${money(u.price)}${held ? ' · ' + esc(held) : ''}</div>
        <div class="pr">${esc(u.type || '')}${u.area_sqft ? ' · ' + Number(u.area_sqft).toLocaleString('en-IN') + ' sqft' : ''}${u.facing ? ' · ' + esc(u.facing) : ''}</div>
        ${held && u.deal_title ? `<div class="hint" style="font-size:11px">${esc(u.deal_title).slice(0, 26)}…</div>` : ''}
        <div class="acts">${acts.join('')}</div>
      </div>`;
    }).join('');

    const bind = (attr, fn) => $('invgrid').querySelectorAll(`[${attr}]`).forEach((b) => b.addEventListener('click', () => fn(b)));
    bind('data-unit-block', (b) => unitBlock(b.dataset.unitBlock));
    bind('data-unit-book', (b) => unitBook(b.dataset.unitBook));
    bind('data-unit-release', (b) => unitRelease(b.dataset.unitRelease));
    bind('data-unit-withdraw', (b) => unitWithdraw(b.dataset.unitWithdraw, b.dataset.reinstate === '1'));
  }

  /** A unit action always belongs to a deal — the one open in the drawer, else ask. */
  function activeDealId() {
    if (S.deal && S.deal.deal) return S.deal.deal.id;
    const open = (S.pipeline && S.pipeline.columns) ? Object.entries(S.pipeline.columns)
      .flatMap(([stage, rows]) => rows.map((d) => ({ ...d, stage })))
      .filter((d) => d.stage !== 'won' && d.stage !== 'lost') : [];
    if (!open.length) return '';
    const titles = open.slice(0, 12).map((d, i) => `${i + 1}. ${d.title}`).join('\n');
    const pick = window.prompt('Which deal? Enter a number:\n' + titles, '1');
    const idx = Number(pick) - 1;
    return open[idx] ? open[idx].id : '';
  }

  async function unitBlock(unitId) {
    const dealId = activeDealId();
    if (!dealId) return toast('Open a deal first — a hold belongs to one deal.', 'bad');
    try {
      const res = await api(`/api/v1/units/${encodeURIComponent(unitId)}/block`, { method: 'POST', body: { deal_id: dealId, hours: 48 } });
      toast(`Unit ${res.unit.unit_no} held for 48h`, 'ok');
      await loadInventory();
    } catch (e) {
      toast(e.code === 'UNIT_BLOCKED'
        ? `Held by another deal (${(e.detail && e.detail[0] && e.detail[0].hours_left) || '?'} h left).`
        : e.message, 'bad');
    }
  }

  async function unitBook(unitId) {
    const dealId = activeDealId();
    if (!dealId) return toast('Open a deal first.', 'bad');
    const amount = window.prompt('Token amount received (₹):', '100000');
    if (amount === null) return;
    try {
      const res = await api(`/api/v1/units/${encodeURIComponent(unitId)}/book`, {
        method: 'POST', body: { deal_id: dealId, token_amount: Number(amount) || 0 },
      });
      toast(`Unit ${res.unit.unit_no} booked`, 'ok');
      await loadInventory();
      if (S.deal) openDeal(S.deal.deal.id, true);
    } catch (e) { toast(e.message, 'bad'); }
  }

  async function unitRelease(unitId) {
    try {
      const res = await api(`/api/v1/units/${encodeURIComponent(unitId)}/release`, { method: 'POST', body: {} });
      toast(`Unit ${res.unit.unit_no} released`, 'ok');
      await loadInventory();
    } catch (e) {
      toast(e.code === 'FORBIDDEN_SCOPE' ? 'This hold belongs to another deal — a manager has to release it.' : e.message, 'bad');
    }
  }

  async function unitWithdraw(unitId, reinstate) {
    try {
      await api('/api/v1/units/' + encodeURIComponent(unitId), { method: 'PATCH', body: { status: reinstate ? 'available' : 'withdrawn' } });
      toast(reinstate ? 'Unit reinstated' : 'Unit withdrawn', 'ok');
      await loadInventory();
    } catch (e) { toast(e.message, 'bad'); }
  }

  /* ------------------------------------------------------------ partners */
  async function loadPartners() {
    try {
      const [regs, partners, payouts] = await Promise.all([
        api('/api/v1/cp/registrations').catch(() => null),
        api('/api/v1/cp/partners').catch(() => null),
        api('/api/v1/cp/payouts').catch(() => null),
      ]);
      S.cpRegs = (regs && regs.registrations) || [];
      S.cpPartners = (partners && partners.partners) || [];
      S.cpPayouts = (payouts && payouts.payouts) || [];
      renderPartners(payouts);
    } catch (e) { /* the panels stay empty rather than breaking the desk */ }
  }

  function renderPartners(payouts) {
    const pending = S.cpRegs.filter((r) => r.state === 'pending');
    const live = S.cpRegs.filter((r) => r.state === 'approved');
    $('cpnote').textContent = `${live.length} protected · ${pending.length} awaiting approval`;

    $('cpregs').innerHTML = pending.length
      ? `<ul class="steps">${pending.map((r) => `<li>
          <span class="mark no">·</span>
          <div><strong>${esc(r.contact_name || r.contact_id)}</strong>
            <div class="why">${esc(r.cp_name || '')} · registered ${esc(dt(r.created_at))}</div></div>
          <button class="ghost" data-cp-decide="approve" data-id="${esc(r.id)}">approve</button>
          <button class="ghost" data-cp-decide="reject" data-id="${esc(r.id)}">reject</button>
        </li>`).join('')}</ul>`
      : '<div class="empty">No claims waiting. Approved claims are protected for the partner\'s window.</div>';

    $('cppartners').innerHTML = S.cpPartners.length
      ? S.cpPartners.map((p) => `<tr>
          <td>${esc(p.firm)}<div class="hint">${esc(p.email || '')}</div></td>
          <td class="num">${Number(p.commission_pct || 0)}%</td>
          <td class="num">${p.approved || 0}/${p.registrations || 0}</td>
          <td class="num">${p.visits_credited || 0}</td>
          <td>${p.protection_days} days${p.active ? '' : ' <span class="pill bad">inactive</span>'}</td>
        </tr>`).join('')
      : '<tr><td colspan="5" class="hint">No partners yet.</td></tr>';

    const rows = S.cpPayouts.filter((r) => r.status === 'pending');
    $('paynote').textContent = payouts ? money(payouts.pending_total) + ' pending' : '';
    $('cppayouts').innerHTML = rows.length
      ? rows.map((r) => `<div class="row" style="justify-content:space-between;border-bottom:1px solid var(--border);padding:7px 0">
          <div><div style="font-size:12.5px">${esc(r.partner)}</div>
            <div class="hint">${esc(r.title || r.deal_id)}</div></div>
          <div class="row" style="gap:6px">
            <span class="num">${money(r.amount)}</span>
            <button class="ghost" data-cp-pay="${esc(r.deal_id)}">mark paid</button>
          </div>
        </div>`).join('')
      : '<div class="empty">Nothing pending. Partner commission appears here when a credited deal is won.</div>';

    $('cpregs').querySelectorAll('[data-cp-decide]').forEach((b) =>
      b.addEventListener('click', () => decideClaim(b.dataset.id, b.dataset.cpDecide)));
    $('cppayouts').querySelectorAll('[data-cp-pay]').forEach((b) =>
      b.addEventListener('click', () => payPartner(b.dataset.cpPay)));
  }

  async function decideClaim(id, decision) {
    let reason = '';
    if (decision === 'reject') {
      reason = window.prompt('Reason for rejecting the claim (shown to the partner):', 'not eligible') || '';
      if (!reason) return;
    }
    try {
      await api('/api/v1/cp/registrations/' + encodeURIComponent(id) + '/decide', { method: 'POST', body: { decision, reason } });
      toast(decision === 'approve' ? 'Claim approved — the buyer is protected for the partner window.' : 'Claim rejected.', 'ok');
      await load();
    } catch (e) {
      toast(e.code === 'FORBIDDEN_SCOPE' ? 'Only a manager or above can decide a partner claim.' : e.message, 'bad');
    }
  }

  async function payPartner(dealId) {
    const reference = window.prompt('Payment reference (UPI / NEFT / cheque no.) — this is written to the audit log:', '');
    if (!reference) return;
    try {
      const res = await api('/api/v1/cp/payouts/' + encodeURIComponent(dealId) + '/pay', { method: 'POST', body: { reference } });
      toast('Payout marked paid: ' + money(res.payout.amount), 'ok');
      await load();
    } catch (e) {
      toast(e.code === 'FORBIDDEN_SCOPE' ? 'Only an owner or admin can mark a payout paid.' : e.message, 'bad');
    }
  }

  /* ---------------------------------------------------------------- deal */
  async function openDeal(id, quiet) {
    try {
      const [detail, matches] = await Promise.all([
        api('/api/v1/deals/' + encodeURIComponent(id)),
        api('/api/v1/contacts/' + encodeURIComponent((S.pipeline && findContact(id)) || id) + '/matches?limit=3').catch(() => null),
      ]);
      S.deal = detail;
      renderDrawer(detail, matches);
      if (!quiet) renderBoard();
    } catch (e) {
      toast(e.message, 'bad');
    }
  }

  function findContact(dealId) {
    const cols = (S.pipeline && S.pipeline.columns) || {};
    for (const stage of Object.keys(cols)) {
      const hit = cols[stage].find((d) => d.id === dealId);
      if (hit) return hit.contact_id;
    }
    return '';
  }

  function renderDrawer(detail, matches) {
    const d = detail.deal;
    const c = detail.commission || {};
    const writable = !!(S.me && S.me.permissions.write);

    const checklist = (detail.checklist || []).map((s) => `
      <li><span class="mark ${s.ready ? 'ok' : 'no'}">${s.ready ? '✓' : '·'}</span>
        <div><strong>${esc(s.stage)}</strong>
          <div class="why">${s.ready ? 'ready to move'
            : [s.visit_required && !s.visit_ok ? 'needs a site visit at least “' + esc(s.visit_required) + '”' : '',
               s.documents_missing.length ? 'missing: ' + esc(s.documents_missing.join(', ')) : ''].filter(Boolean).join(' · ')}</div>
        </div>
        ${s.ready && writable ? `<button class="ghost" data-advance="${esc(s.stage)}">move →</button>` : ''}</li>`).join('');

    const docs = (d.documents || []).length
      ? (d.documents || []).map((x) => `<span class="pill ${x.status === 'verified' ? 'ok' : 'info'}">${esc(x.name)} · ${esc(x.status)}</span>`).join(' ')
      : '<span class="hint">None recorded.</span>';

    const visits = (detail.visits || []).length
      ? detail.visits.map((v) => `<li><span class="mark ${v.status === 'visited' ? 'ok' : 'no'}">·</span>
          <div>${esc(dt(v.scheduled_at))} · <span class="pill">${esc(v.status)}</span>
          <div class="why">${esc(v.property_title || '')}${v.feedback ? ' — ' + esc(v.feedback) : ''}</div></div></li>`).join('')
      : '<li><span class="mark no">·</span><div>No site visit yet<div class="why">A deal cannot enter “site visit” without one.</div></div></li>';

    const matchBox = matches && matches.matches && matches.matches.length
      ? matches.matches.map((m) => `<div class="match">
          <div class="top"><span>${esc(m.title)}</span><span>${m.score}</span></div>
          <div class="bar"><i style="width:${Math.max(2, Math.min(100, m.score))}%"></i></div>
          <ul>${m.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul></div>`).join('')
      : '<div class="empty">No matches computed.</div>';

    $('drawerid').textContent = d.id;
    $('drawer').querySelector('.body').innerHTML = `
      <dl class="kv">
        <dt>Deal</dt><dd><strong>${esc(d.title)}</strong></dd>
        <dt>Stage</dt><dd><span class="pill info">${esc(d.stage)}</span> ${Number(d.probability || 0)}%</dd>
        <dt>Value</dt><dd>${money(d.value)}</dd>
        <dt>Expected</dt><dd>${d.expected_close ? new Date(Number(d.expected_close) * 1000).toLocaleDateString('en-IN') : '—'}</dd>
      </dl>

      <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:16px 0 4px">Documents</h3>
      <div>${docs}</div>
      ${writable ? `<form id="docform" class="row" style="margin-top:8px">
          <select id="doc_name" style="flex:1">
            ${['KYC (Aadhaar/PAN)', 'Booking form', 'Sale agreement draft', 'Stamp duty receipt', 'Registration appointment', 'Registered sale deed', 'Other']
              .map((n) => `<option>${esc(n)}</option>`).join('')}
          </select>
          <button type="submit">Record</button></form>` : ''}

      <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:16px 0 4px">What the next stages need</h3>
      <ul class="steps">${checklist || '<li><span class="mark ok">✓</span><div>This deal is won.</div></li>'}</ul>

      <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:16px 0 4px">Site visits</h3>
      <ul class="steps">${visits}</ul>

      <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:16px 0 4px">Commission</h3>
      ${c && c.gross
        ? `<dl class="kv">
             <dt>Gross</dt><dd>${money(c.gross)} <span class="hint">(${c.rate_pct}% of ${money(c.deal_value)})</span></dd>
             <dt>Channel partner</dt><dd>${c.channel_partner ? money(c.channel_partner) + ' (' + c.channel_partner_pct + '%)' : '—'}</dd>
             <dt>Agent</dt><dd>${money(c.agent)} <span class="hint">(${c.agent_share_pct}% of the net)</span></dd>
             <dt>Brokerage</dt><dd>${money(c.brokerage)}</dd>
             <dt>Payout</dt><dd><span class="pill ${c.payout_status === 'paid' ? 'ok' : 'warn'}">${esc(c.payout_status || 'pending')}</span></dd>
           </dl>`
        : '<div class="empty">No commission computed yet.</div>'}
      ${writable ? `<form id="commform" class="row" style="margin-top:8px">
          <label class="f">Rate %<input id="c_rate" type="number" step="0.05" min="0" max="25" value="${c.rate_pct || 2}" style="width:76px"></label>
          <label class="f">Agent %<input id="c_agent" type="number" step="1" min="0" max="100" value="${c.agent_share_pct || 60}" style="width:76px"></label>
          <label class="f">CP %<input id="c_cp" type="number" step="1" min="0" max="100" value="${c.channel_partner_pct || 0}" style="width:70px"></label>
          <button type="submit" class="primary" style="align-self:end">Compute</button></form>` : ''}

      <h3 style="font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin:16px 0 4px">Property matches</h3>
      ${matchBox}
      ${matches && matches.requirement ? `<div class="hint">Requirement: ${esc(matches.requirement)}</div>` : ''}
    `;

    const df = $('docform');
    if (df) df.addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        await api('/api/v1/deals/' + encodeURIComponent(d.id) + '/documents', {
          method: 'POST', body: { name: $('doc_name').value, status: 'received' },
        });
        toast('Document recorded', 'ok');
        await load();
      } catch (err) { toast(err.message, 'bad'); }
    });

    const cf = $('commform');
    if (cf) cf.addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        const res = await api('/api/v1/deals/' + encodeURIComponent(d.id) + '/commission', {
          method: 'POST',
          body: { rate_pct: Number($('c_rate').value), agent_share_pct: Number($('c_agent').value), channel_partner_pct: Number($('c_cp').value) },
        });
        toast('Commission: ' + money(res.commission.gross) + ' gross, ' + money(res.commission.agent) + ' to the agent', 'ok');
        await load();
      } catch (err) { toast(err.message, 'bad'); }
    });

    $('drawer').querySelectorAll('[data-advance]').forEach((b) =>
      b.addEventListener('click', () => advance(d.id, b.dataset.advance)));
  }

  async function advance(dealId, stage) {
    try {
      const res = await api('/api/v1/deals/' + encodeURIComponent(dealId) + '/advance', { method: 'POST', body: { stage } });
      toast('Moved to ' + stage + (res.gate && res.gate.forced ? ' (forced)' : ''), 'ok');
      await load();
    } catch (e) {
      toast(e.message + (e.code ? ' [' + e.code + ']' : ''), 'bad');
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
