/* Channel-partner portal.
 *
 * A partner is a real login (role `channel_partner`) that the server confines to
 * /api/v1/cp/* — the scoping is enforced there, not here. This file is only the
 * view: register a buyer, watch the protection clock, see visits credited and
 * commission with an explicit basis.
 */
(() => {
  'use strict';

  const S = { csrf: '', me: null, inv: null };
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 });
  const dt = (epoch) => (epoch ? new Date(Number(epoch) * 1000).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');

  async function api(path, opts = {}) {
    const method = (opts.method || 'GET').toUpperCase();
    const headers = { Accept: 'application/json' };
    if (method !== 'GET') {
      headers['Content-Type'] = 'application/json';
      if (S.csrf) headers['X-CSRF-Token'] = S.csrf;
    }
    const res = await fetch(path, { method, headers, credentials: 'same-origin', body: opts.body ? JSON.stringify(opts.body) : undefined });
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) {
      const err = new Error((data.error && data.error.message) || ('HTTP ' + res.status));
      err.code = data.error && data.error.code;
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function show(portal) {
    $('authcard').hidden = portal;
    $('portal').hidden = !portal;
    $('logout').hidden = !portal;
    $('house').hidden = portal;
  }

  /* --------------------------------------------------------------- session */
  async function resume() {
    try {
      const session = await api('/api/v1/auth/session');
      S.csrf = session.csrf;
      if (session.user.role !== 'channel_partner') {
        $('autherr').textContent = 'This login belongs to the brokerage console, not the partner portal.';
        show(false);
        return;
      }
      S.me = session;
      await boot();
    } catch (e) {
      show(false);
    }
  }

  async function boot() {
    const [me, leads, visits, earnings, inventory] = await Promise.all([
      api('/api/v1/cp/me'),
      api('/api/v1/cp/leads'),
      api('/api/v1/cp/visits'),
      api('/api/v1/cp/earnings'),
      api('/api/v1/cp/inventory'),
    ]);
    show(true);
    $('who').textContent = me.partner.firm + ' · ' + me.partner.commission_pct + '% of gross commission · ' + me.partner.protection_days + '-day protection';
    renderTiles(me, earnings);
    renderLeads(leads);
    renderVisits(visits);
    renderEarnings(earnings);
    renderInventory(inventory);
  }

  /* ------------------------------------------------------------- inventory */
  // What a partner may sell, and the two things that stop a CP portal leaking:
  // only free stock is listed, and the seller is never on the page. The server
  // decides both; this only draws what it returns.
  function renderInventory(data) {
    S.inv = data;
    const rows = data.inventory || [];
    const basis = data.commission_basis || {};
    $('invnote').textContent = (data.count || 0) + ' propert' + (data.count === 1 ? 'y' : 'ies') +
      ' you can sell' + (data.truncated ? ' (first ' + rows.length + ')' : '');

    $('invtiles').innerHTML = [
      ['Properties', String(data.count || 0), (data.listed || 0) + ' with free stock or a whole listing'],
      ['Your share', (basis.your_share_pct || 0) + '%', 'of the gross commission, not of the sale value'],
      ['Rate basis', basis.gross_rate_pct ? '~' + basis.gross_rate_pct + '%' : 'n/a', basis.basis || ''],
      ['Ready to advertise', String(rows.filter((r) => r.marketing && r.marketing.ready_to_show).length), 'RERA and at least 3 photos'],
    ].map(([k, v, n]) => `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');

    // filters learn their options from the catalogue, so a new city needs no code
    const fill = (id, values, allLabel) => {
      const el = $(id);
      if (!el || el.dataset.filled === '1') return;
      const keep = el.value;
      el.innerHTML = `<option value="">${esc(allLabel)}</option>` +
        (values || []).map((v) => `<option value="${esc(v.value)}">${esc(v.value)} (${v.count})</option>`).join('');
      el.value = keep;
      el.dataset.filled = '1';
    };
    fill('i_city', (data.facets || {}).cities, 'All cities');
    fill('i_type', (data.facets || {}).types, 'All types');

    if (!rows.length) {
      $('inventory').innerHTML = '<tr><td colspan="7" class="hint">Nothing matches. Free stock only — a held, booked or sold property is somebody else\'s conversation.</td></tr>';
    } else {
      $('inventory').innerHTML = rows.map((r) => {
        const av = r.availability || {};
        const free = av.total ? (av.available + '/' + av.total + ' units') : 'whole property';
        const c = r.commission || {};
        const m = r.marketing || {};
        const cls = { available: 'ok', limited: 'warn', sold_out: 'bad', off_market: '' }[r.status] || '';
        // the cover is served from the brokerage's own media pipeline; when it
        // cannot sign a URL we show the listing's monogram, never a stock photo
        const ph = r.photos || {};
        const cover = r.cover && r.cover.url
          ? `<img class="thumb" src="${esc(r.cover.url)}" alt="" width="${Number(r.cover.width) || 34}" height="${Number(r.cover.height) || 26}" loading="lazy">`
          : `<div class="thumb mono" aria-hidden="true">${esc((r.title || '?').trim().charAt(0).toUpperCase())}</div>`;
        const photoNote = ph.known === false
          ? '<div class="hint">photos: not visible here</div>'
          : `<div class="hint">${ph.count || 0} photo${(ph.count || 0) === 1 ? '' : 's'}</div>`;
        const ready = r.marketing && r.marketing.ready_to_show;
        return `<tr>
          <td>${cover}</td>
          <td><strong>${esc(r.title)}</strong><div class="hint">${esc(r.type || '')}${r.rera ? ' · RERA ' + esc(r.rera) : ''}</div></td>
          <td>${esc(r.city || '')}<div class="hint">${esc(r.locality || '')}</div></td>
          <td class="num">${esc(money(r.price))}<div class="hint">${esc((r.area || {}).value ? r.area.value + ' ' + (r.area.unit || '') : '')}</div></td>
          <td><span class="pill ${cls}">${esc(r.status)}</span><div class="hint">${esc(free)}</div>${photoNote}</td>
          <td class="num">${c.estimate != null ? esc(money(c.estimate)) : '<span class="hint">on request</span>'}<div class="hint">${esc(c.basis || '')}</div></td>
          <td>${ready ? '<span class="pill ok">yes</span>' : '<span class="pill warn">not yet</span><div class="hint">' +
            esc((m.missing || []).length ? 'missing ' + m.missing.join(', ') + (ph.ready === false ? ' · ' + (ph.note || '') : '')
                                         : (ph.note || '')) + '</div>'}</td>
        </tr>`;
      }).join('');
    }
    const w = data.withheld || [];
    $('withheld').innerHTML = w.length
      ? '<strong>Not shown to you, on purpose:</strong> ' + w.map((x) => esc(x.what) + ' — ' + esc(x.why)).join(' · ')
      : '';
  }

  async function filterInventory() {
    const params = new URLSearchParams();
    const q = $('i_q').value.trim(); if (q) params.set('q', q);
    if ($('i_city').value) params.set('city', $('i_city').value);
    if ($('i_type').value) params.set('type', $('i_type').value);
    if (Number($('i_min').value || 0)) params.set('min_price', $('i_min').value);
    if (Number($('i_max').value || 0)) params.set('max_price', $('i_max').value);
    try {
      renderInventory(await api('/api/v1/cp/inventory' + (params.toString() ? '?' + params : '')));
    } catch (e) {
      $('invnote').textContent = 'inventory unavailable';
    }
  }

  function renderTiles(me, earnings) {
    const c = me.counts || {};
    const s = (earnings && earnings.summary) || {};
    $('tiles').innerHTML = [
      ['My buyers', String(c.registrations || 0), (c.approved || 0) + ' protected · ' + (c.pending || 0) + ' awaiting approval'],
      ['Visits credited', String(c.visits_credited || 0), 'buyers who turned up'],
      ['Due to me', money(s.due_total || 0), (s.due_deals || 0) + ' closed deals'],
      ['Paid', money(s.paid_total || 0), 'settled by the brokerage'],
      ['In pipeline', money(s.pipeline_estimate || 0), 'estimate, not a promise'],
    ].map(([k, v, n]) => `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');
  }

  function renderLeads(data) {
    const rows = data.leads || [];
    $('leadnote').textContent = rows.length + ' registered';
    if (!rows.length) {
      $('leads').innerHTML = '<tr><td colspan="5" class="hint">No buyers yet. Register one above — if the number is already in the brokerage\'s pipeline it will be refused rather than silently duplicated.</td></tr>';
      return;
    }
    $('leads').innerHTML = rows.map((r) => {
      const cls = { approved: 'ok', pending: 'warn', rejected: 'bad', expired: '' }[r.status] || '';
      const protection = r.days_left == null ? '—' : r.days_left + ' days left';
      return `<tr>
        <td>${esc(r.name)}<div class="hint">${esc(r.phone || '')}</div></td>
        <td><span class="pill ${cls}">${esc(r.status)}</span>${r.reason ? `<div class="hint">${esc(r.reason)}</div>` : ''}</td>
        <td>${esc(protection)}</td>
        <td class="num">${Number(r.visits_credited || 0)}</td>
        <td>${r.deal_stage ? esc(r.deal_stage) + (r.deal_value ? `<div class="hint">${money(r.deal_value)}</div>` : '') : '<span class="hint">—</span>'}</td>
      </tr>`;
    }).join('');
  }

  function renderVisits(data) {
    const rows = data.visits || [];
    $('visitnote').textContent = (data.credited || 0) + ' credited';
    $('visits').innerHTML = rows.length
      ? rows.map((v) => `<tr><td>${esc(dt(v.scheduled_at))}</td><td>${esc(v.contact_name || '')}</td>
          <td>${esc(v.property || '')}</td><td><span class="pill">${esc(v.status)}</span></td>
          <td>${v.credited ? '<span class="pill ok">credited</span>' : '<span class="hint">—</span>'}</td></tr>`).join('')
      : '<tr><td colspan="5" class="hint">No visits booked yet.</td></tr>';
  }

  function renderEarnings(data) {
    const s = data.summary || {};
    $('earningtiles').innerHTML = [
      ['Due', money(s.due_total || 0), 'on closed deals'],
      ['Pending payout', money(s.pending_total || 0), 'approved, not paid'],
      ['Paid', money(s.paid_total || 0), 'settled'],
    ].map(([k, v, n]) => `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="n">${esc(n)}</div></div>`).join('');

    const rows = data.earnings || [];
    $('earnings').innerHTML = rows.length
      ? rows.map((r) => `<tr>
          <td>${esc(r.title || r.deal_id)}</td>
          <td><span class="pill ${r.stage === 'won' ? 'ok' : ''}">${esc(r.stage || '')}</span></td>
          <td class="num">${money(r.deal_value)}</td>
          <td class="num">${money(r.amount)}<div class="hint">${r.cp_pct}% of ${money(r.gross_commission)}</div></td>
          <td><span class="pill ${r.basis === 'recorded' ? 'ok' : ''}">${esc(r.basis)}</span></td>
          <td>${esc(r.payout_status)}${r.reference ? `<div class="hint">${esc(r.reference)}</div>` : ''}</td>
        </tr>`).join('')
      : '<tr><td colspan="6" class="hint">Nothing yet — earnings appear when a buyer you registered reaches a deal.</td></tr>';
  }

  /* ----------------------------------------------------------------- forms */
  $('i_go').addEventListener('click', filterInventory);
  $('i_q').addEventListener('keydown', (e) => { if (e.key === 'Enter') filterInventory(); });
  $('i_city').addEventListener('change', filterInventory);
  $('i_type').addEventListener('change', filterInventory);
  $('loginform').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('autherr').textContent = '';
    try {
      const res = await api('/api/v1/auth/login', { method: 'POST', body: { email: $('email').value.trim(), password: $('password').value } });
      S.csrf = res.csrf;
      if (res.user.role !== 'channel_partner') {
        $('autherr').textContent = 'That login is for the brokerage console. Partners sign in here with the account the brokerage created.';
        await api('/api/v1/auth/logout', { method: 'POST', body: {} }).catch(() => {});
        return;
      }
      $('password').value = '';
      await boot();
    } catch (err) {
      $('autherr').textContent = err.message;
    }
  });

  $('regform').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('regerr').textContent = '';
    try {
      const res = await api('/api/v1/cp/register', {
        method: 'POST',
        body: {
          name: $('r_name').value.trim(),
          phone: $('r_phone').value.trim(),
          city: $('r_city').value.trim(),
          requirement: $('r_req').value.trim(),
          budget_min: Number($('r_min').value || 0) || undefined,
          budget_max: Number($('r_max').value || 0) || undefined,
        },
      });
      $('regerr').textContent = res.message || 'Registration submitted.';
      $('regerr').style.color = 'var(--emerald)';
      $('regform').reset();
      await boot();
    } catch (err) {
      const messages = {
        CP_LEAD_CONFLICT: 'Another partner already holds this buyer. The claim is protected for its remaining window.',
        CP_LEAD_ALREADY_IN_PIPELINE: 'This number is already in the brokerage\'s own pipeline, so it cannot be claimed.',
        VALIDATION_FAILED: 'Check the name and the 10-digit mobile number.',
      };
      $('regerr').style.color = 'var(--danger)';
      $('regerr').textContent = messages[err.code] || err.message;
    }
  });

  $('logout').addEventListener('click', async () => {
    await api('/api/v1/auth/logout', { method: 'POST', body: {} }).catch(() => {});
    S.csrf = '';
    location.reload();
  });

  document.addEventListener('DOMContentLoaded', resume);
})();
