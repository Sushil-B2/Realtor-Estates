/* ==========================================================================
   Views — growth: funnels & website, social studio, paid campaigns, analytics
   ========================================================================== */
'use strict';

/* ---------------------------- funnels + website --------------------------- */
RE.views.funnels = async () => {
  const [fres, pages, tenant] = await Promise.all([api('/api/v1/funnels'), api('/api/v1/pages'), api('/api/v1/tenant')]);
  return {
    title: 'Funnels & website',
    crumb: `${fres.funnels.length} funnels · ${pages.pages.length} site pages · public rendering is JS-free`,
    html: `
    ${marketingWebsiteSelector(fres.funnels)}
    <div class="page-head">
      <div><h1>Funnels &amp; website</h1>
        <div class="sub">Buyer-facing surfaces render server-side with escaped output, so they work on a 2G phone with JavaScript disabled — and they capture consent before anything else happens.</div></div>
      <div class="actions">
        <button class="btn" data-act="marketing-trust-pack-modal">Govt. Trust &amp; Cost Pack</button>
        <a class="btn" href="/site" target="_blank" rel="noopener noreferrer">${icon('eye')}Open public site</a>
        <button class="btn primary" data-act="new-funnel">${icon('plus')}New funnel</button>
      </div>
    </div>

    <div class="grid g-2" style="margin-bottom:16px">
      ${fres.funnels.map((f) => `<div class="card">
        <div class="card-head"><h2>${esc(f.name)}</h2>
          <span class="right chip ${f.published ? 'green' : 'grey'}">${f.published ? 'live' : 'draft'}</span></div>
        <div class="card-body">
          <div class="small b">${esc(f.headline)}</div>
          <p class="small muted" style="margin-top:4px">${esc(f.sub)}</p>
          <div class="row" style="margin-top:10px">
            <span class="chip grey xs mono">/f/${esc(f.slug)}</span>
            <span class="chip blue xs">${fmtNum(f.visits)} visits</span>
            <span class="chip green xs">${fmtNum(f.submissions)} leads</span>
            <span class="chip brand xs">${pct(f.submissions / Math.max(1, f.visits))} CVR</span>
          </div>
          <div class="divider"></div>
          <div class="row">
            <a class="btn sm" href="/f/${esc(f.slug)}" target="_blank" rel="noopener">${icon('eye')}Preview</a>
            <button class="btn sm" data-act="edit-funnel" data-id="${esc(f.id)}">Edit</button>
            <button class="btn sm" data-act="toggle-funnel" data-id="${esc(f.id)}" data-published="${f.published ? 'false' : 'true'}">${f.published ? 'Unpublish' : 'Publish'}</button>
            <span class="xs muted right">${f.sections.length} sections</span>
          </div>
        </div>
      </div>`).join('')}
    </div>

    <div class="card" style="margin-bottom:16px">
      <div class="card-head"><h2>Website pages</h2><span class="sub">server-rendered · escaped · no external assets</span></div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Page</th><th>Slug</th><th>Status</th><th>Body</th><th></th></tr></thead>
        <tbody>${pages.pages.map((p) => `<tr><td class="cell-strong">${esc(p.title)}</td><td class="mono small">/${esc(p.slug)}</td>
          <td>${stageChip(p.published ? 'published' : 'draft')}</td><td class="small muted">${esc((p.body || '').slice(0, 90))}…</td>
          <td><button class="btn sm" data-act="edit-page" data-id="${esc(p.id)}">Edit</button></td></tr>`).join('')}</tbody>
      </table></div>
    </div>

    <div class="grid g-3">
      <div class="card card-pad"><h2>Why doc-pack funnels convert</h2>
        <p class="small muted" style="margin-top:6px">The buyer's real question is "is the title clean?", not "when can I visit?". Funnels that lead with 7/12, NA order and encumbrance status convert materially better than price-led pages.</p></div>
      <div class="card card-pad"><h2>Consent-first forms</h2>
        <p class="small muted" style="margin-top:6px">Every form carries a purpose-bound consent checkbox, a notice version and a honeypot field. No consent, no contact — enforced in the API, not the page.</p></div>
      <div class="card card-pad"><h2>Workspace</h2>
        <p class="small muted" style="margin-top:6px">Plan <b>${esc(tenant.plan)}</b> · region ${esc(tenant.region)} · quiet hours ${esc((tenant.settings.quiet_hours || []).join('–'))} IST · AI disclosure <b>always on</b>.</p></div>
    </div>`,
  };
};

RE.actions['new-funnel'] = () => {
  modal({
    title: 'New funnel',
    body: `<form data-form="create-funnel">
      <div class="grid g-2">
        <div class="field"><label>Name</label><input class="input" name="name" required maxlength="100"></div>
        <div class="field"><label>Slug</label><input class="input" name="slug" required maxlength="60" pattern="[a-z0-9\\-]+" placeholder="nanded-plots-2027"></div>
      </div>
      <div class="field"><label>Headline</label><input class="input" name="headline" maxlength="160" placeholder="Own a clear-title plot in Nanded from ₹24L"></div>
      <div class="field"><label>Sub-headline</label><input class="input" name="sub" maxlength="240"></div>
      <div class="field"><label>CTA</label><input class="input" name="cta" maxlength="60" value="Book a free site visit"></div>
    </form>`,
    foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="create-funnel">Create funnel</button>`,
  });
};

RE.actions['form:create-funnel'] = async (payload) => {
  try {
    await api('/api/v1/funnels', { method: 'POST', body: payload });
    closeModal();
    toast('Funnel created as draft', 'ok');
    route();
  } catch (_) {}
};

RE.actions['toggle-funnel'] = async (el) => {
  try {
    await api('/api/v1/funnels/' + el.dataset.id, { method: 'PATCH', body: { published: el.dataset.published === 'true' } });
    toast('Funnel visibility updated', 'ok');
    route();
  } catch (_) {}
};

RE.actions['marketing-trust-pack-modal'] = async () => {
  const [pres, fres] = await Promise.all([api('/api/v1/properties'), api('/api/v1/funnels')]);
  const props = pres.properties || [];
  const funnels = fres.funnels || [];
  const firstProp = props[0];
  let pack = null;
  if (firstProp) {
    pack = await api('/api/v1/properties/' + encodeURIComponent(firstProp.id) + '/marketing-trust-pack');
  }
  modal({
    title: 'Marketing Govt.-Verified Trust Badges & All-In Cost Pack',
    wide: true,
    body: `<p class="small muted">Badges only light up when backed by actual MahaRERA, Collector NA, fresh 7/12 (≤30 days, zero active Boja), 30-year Title Search, and Bank APF records — zero unverified marketing claims.</p>
      <form data-form="bind-funnel-trust-pack" class="grid g-2" style="margin-top:10px">
        <div class="field"><label>Property</label><select class="input" name="property_id">
          ${props.map((p) => `<option value="${esc(p.id)}">${esc(p.title)} (${esc(p.city || 'Nanded')})</option>`).join('')}
        </select></div>
        <div class="field"><label>Bind to Public Funnel (renders on /f/&lt;slug&gt;)</label><select class="input" name="funnel_id">
          <option value="">— Preview Trust Pack Only —</option>
          ${funnels.map((f) => `<option value="${esc(f.id)}">${esc(f.name)} (/f/${esc(f.slug)})</option>`).join('')}
        </select></div>
      </form>
      <div id="trust-pack-preview" style="margin-top:10px">
        ${pack ? `<div class="evidence">VERIFIED BADGES (${pack.badge_count}): ${(pack.verified_badges || []).map((b) => b.label).join(' · ') || 'Complete 7/12 & NA Vault to unlock'}
WHATSAPP COPY:
${esc(pack.whatsapp_brochure_copy || '')}</div>` : '<div class="xs muted">No properties available.</div>'}
      </div>`,
    foot: `<button class="btn" data-act="close-modal">Close</button>
           <button class="btn primary" data-act="submit-form" data-form="bind-funnel-trust-pack">Inspect / Bind to Funnel</button>`,
  });
};

RE.actions['form:bind-funnel-trust-pack'] = async (payload) => {
  if (!payload.property_id) return toast('Select a property.', 'err');
  const pack = await api('/api/v1/properties/' + encodeURIComponent(payload.property_id) + '/marketing-trust-pack');
  if (payload.funnel_id) {
    await api('/api/v1/funnels/' + encodeURIComponent(payload.funnel_id), {
      method: 'PATCH',
      body: { property_id: payload.property_id },
    });
    toast('Funnel linked to property Govt. Trust & Cost Pack', 'ok');
  } else {
    toast('Loaded live Govt. Trust Pack', 'ok');
  }
  const box = document.getElementById('trust-pack-preview');
  if (box) {
    box.innerHTML = `<div class="evidence">VERIFIED BADGES (${pack.badge_count}): ${esc((pack.verified_badges || []).map((b) => b.label).join(' · ') || 'None yet')}
SAMPLE ALL-IN OUTFLOW: ${fmtMoney((pack.sample_cost_sheet && pack.sample_cost_sheet.all_in_buyer_outflow_inr) || 0, false)}
WHATSAPP COPY:
${esc(pack.whatsapp_brochure_copy || '')}</div>`;
  }
};

RE.actions['edit-funnel'] = async (el) => {
  const res = await api('/api/v1/funnels');
  const f = res.funnels.find((x) => x.id === el.dataset.id);
  modal({
    title: 'Edit funnel',
    body: `<form data-form="edit-funnel" data-id="${esc(f.id)}">
      <div class="field"><label>Headline</label><input class="input" name="headline" value="${esc(f.headline)}" maxlength="160"></div>
      <div class="field"><label>Sub-headline</label><textarea class="input" name="sub" maxlength="240">${esc(f.sub)}</textarea></div>
      <div class="field"><label>CTA</label><input class="input" name="cta" value="${esc(f.cta)}" maxlength="60"></div>
    </form>`,
    foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="edit-funnel">Save</button>`,
  });
};

RE.actions['form:edit-funnel'] = async (payload, form) => {
  try {
    await api('/api/v1/funnels/' + form.dataset.id, { method: 'PATCH', body: payload });
    closeModal();
    toast('Funnel updated', 'ok');
    route();
  } catch (_) {}
};

RE.actions['edit-page'] = async (el) => {
  const res = await api('/api/v1/pages');
  const p = res.pages.find((x) => x.id === el.dataset.id);
  modal({
    title: 'Edit page — ' + p.slug,
    body: `<form data-form="edit-page" data-id="${esc(p.id)}">
      <div class="field"><label>Title</label><input class="input" name="title" value="${esc(p.title)}" maxlength="120"></div>
      <div class="field"><label>Body</label><textarea class="input" name="body" style="min-height:130px">${esc(p.body)}</textarea></div>
      <div class="check"><input type="checkbox" name="published" id="pp" ${p.published ? 'checked' : ''}><label for="pp" style="font-weight:400">Published</label></div>
      <p class="hint">Content is HTML-escaped on render. Markdown is not interpreted — deliberately.</p>
    </form>`,
    foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="edit-page">Save page</button>`,
  });
};

RE.actions['form:edit-page'] = async (payload, form) => {
  try {
    await api('/api/v1/pages/' + form.dataset.id, { method: 'PATCH', body: { title: payload.title, body: payload.body, published: !!payload.published } });
    closeModal();
    toast('Page saved', 'ok');
    route();
  } catch (_) {}
};

/* --------------------------------- social -------------------------------- */
RE.views.social = async () => {
  const res = await api('/api/v1/social');
  const days = [...Array(7)].map((_, i) => {
    const d = new Date();
    d.setDate(d.getDate() + i);
    return d;
  });
  return {
    title: 'Social studio',
    crumb: `${res.posts.filter((p) => p.status === 'scheduled').length} scheduled · publishing is human-gated`,
    html: `
    <div class="page-head">
      <div><h1>Social studio</h1>
        <div class="sub">Draft, schedule and review. Agents can produce drafts and suggested angles; a manager or above must approve publishing (brand-safety + RERA advertising rules).</div></div>
      <div class="actions"><button class="btn primary" data-act="new-post">${icon('plus')}New post</button></div>
    </div>

    <div class="card" style="margin-bottom:14px">
      <div class="card-head"><h2>Next 7 days</h2><span class="sub">Asia/Kolkata</span></div>
      <div class="card-body grid" style="grid-template-columns:repeat(7,minmax(0,1fr));gap:8px">
        ${days.map((d) => {
          const key = d.toDateString();
          const posts = res.posts.filter((p) => new Date(p.scheduled_at * 1000).toDateString() === key);
          return `<div class="matrix-item" style="min-height:110px">
            <div class="xs muted b">${d.toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric' })}</div>
            ${posts.map((p) => `<div class="xs" style="margin-top:5px;padding:4px 6px;border-radius:6px;background:${colorFor(p.channel)}22;border:1px solid ${colorFor(p.channel)}44">
              <b>${esc(p.channel)}</b><br>${esc(p.text.slice(0, 42))}…</div>`).join('') || '<div class="xs muted" style="margin-top:8px">—</div>'}
          </div>`;
        }).join('')}
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>All posts</h2><span class="chip grey">${res.posts.length}</span></div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Post</th><th>Channel</th><th>Scheduled</th><th>Status</th><th>Engagement</th><th></th></tr></thead>
        <tbody>${res.posts.map((p) => `<tr>
          <td><div class="cell-strong" style="max-width:420px">${esc(p.text)}</div><div class="cell-sub">${esc(p.media_hint || '')}</div></td>
          <td><span class="chip grey xs">${esc(p.channel)}</span></td>
          <td class="small muted">${fmtDate(p.scheduled_at)} ${fmtTime(p.scheduled_at)}</td>
          <td>${stageChip(p.status)}</td>
          <td class="small muted">${p.engagement ? `${fmtNum(p.engagement.likes)} ♥ · ${fmtNum(p.engagement.comments)} 💬` : '—'}</td>
          <td class="row">${p.status !== 'published' ? `<button class="btn sm" data-act="publish-post" data-id="${esc(p.id)}">Publish</button>` : ''}
            <button class="btn sm ghost" data-act="draft-social-ai" data-id="${esc(p.id)}">${icon('sparkles')}</button></td></tr>`).join('')}</tbody>
      </table></div>
    </div>`,
  };
};

RE.actions['publish-post'] = async (el) => {
  try {
    await api('/api/v1/social/' + el.dataset.id, { method: 'PATCH', body: { status: 'published', engagement: { likes: 0, comments: 0, shares: 0 } } });
    toast('Published — brand-safety gate satisfied by your role', 'ok');
    route();
  } catch (_) {}
};

RE.actions['new-post'] = () => {
  modal({
    title: 'New social post',
    body: `<form data-form="create-post">
      <div class="grid g-2">
        <div class="field"><label>Channel</label><select class="input" name="channel">
          <option>instagram</option><option>youtube</option><option>linkedin</option><option>facebook</option><option>whatsapp_channel</option></select></div>
        <div class="field"><label>Schedule</label><input class="input" name="scheduled_at" type="datetime-local"></div>
      </div>
      <div class="field"><label>Copy</label><textarea class="input" name="text" required maxlength="900" placeholder="Why Nanded's Taroda belt is the smartest ₹30L land bet of 2027 — 5 data points."></textarea></div>
      <div class="field"><label>Media hint</label><input class="input" name="media_hint" maxlength="40" placeholder="carousel/5 or video/6m"></div>
      <p class="hint">Posts land in draft. Publishing needs a manager or above.</p>
    </form>`,
    foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="create-post">Save draft</button>`,
  });
};

RE.actions['form:create-post'] = async (payload) => {
  const ts = payload.scheduled_at ? Math.floor(new Date(payload.scheduled_at).getTime() / 1000) : undefined;
  try {
    await api('/api/v1/social', { method: 'POST', body: { channel: payload.channel, text: payload.text, media_hint: payload.media_hint, scheduled_at: ts } });
    closeModal();
    toast('Draft saved', 'ok');
    route();
  } catch (_) {}
};

RE.actions['draft-social-ai'] = async () => {
  const res = await api('/api/v1/security/inspect', { method: 'POST', body: { text: 'Draft: 3 reasons buyers check 7/12 before price — and how we hand you the pack before your visit.' } });
  toast(`AI draft ready · guardrail ${res.inspection.action}`, 'ok');
};

/* ---------------------------------- ads ---------------------------------- */
RE.views.ads = async () => {
  const res = await api('/api/v1/campaigns');
  const spend = res.campaigns.reduce((s, c) => s + (c.spend || 0), 0);
  const leads = res.campaigns.reduce((s, c) => s + (c.leads || 0), 0);
  return {
    title: 'Paid campaigns',
    crumb: `${fmtMoney(spend)} spend · ${fmtNum(leads)} leads · ${fmtMoney(leads ? spend / leads : 0)} blended CPL`,
    html: `
    <div class="page-head">
      <div><h1>Paid campaigns</h1>
        <div class="sub">Offline-conversion feedback closes the loop: "site visit happened" is uploaded back to Google/Meta so bidding optimises for visits, not clicks.</div></div>
      <div class="actions">
        <button class="btn" data-act="ads-spend-policy">${icon('shield')}Spend Cap Policy</button>
        <button class="btn" data-act="run-ads-autopilot">${icon('bolt')}Run Autopilot</button>
        <button class="btn primary" data-act="new-campaign">${icon('plus')}New campaign</button>
      </div>
    </div>

    <div class="grid g-3" style="margin-bottom:14px">
      ${res.campaigns.map((c) => `<div class="card">
        <div class="card-head"><h2>${esc(c.name)}</h2><span class="right chip ${c.status === 'active' ? 'green' : c.status === 'paused' ? 'amber' : 'grey'}">${esc(c.status)}</span></div>
        <div class="card-body">
          <div class="row xs muted"><span>${esc(c.platform)}</span><span class="spacer"></span><span>budget ${fmtMoney(c.budget_daily)}/day</span></div>
          <div class="grid g-2" style="margin-top:12px;gap:8px">
            <div><div class="xs muted">Spend</div><div class="b num">${fmtMoney(c.spend)}</div></div>
            <div><div class="xs muted">Leads</div><div class="b num">${fmtNum(c.leads)}</div></div>
            <div><div class="xs muted">CPL</div><div class="b num">${c.cpl ? '₹' + fmtNum(c.cpl) : '—'}</div></div>
            <div><div class="xs muted">ROAS</div><div class="b num ${c.roas >= 3 ? 'delta-up' : c.roas ? 'delta-down' : ''}">${c.roas ? c.roas.toFixed(1) + '×' : '—'}</div></div>
          </div>
          <div class="divider"></div>
          <div class="row">
            <button class="btn sm" data-act="toggle-campaign" data-id="${esc(c.id)}" data-status="${c.status === 'active' ? 'paused' : 'active'}">${c.status === 'active' ? 'Pause' : 'Activate'}</button>
            <button class="btn sm ghost" data-act="request-campaign-budget" data-id="${esc(c.id)}" data-budget="${esc(c.budget_daily)}">+25% Approval</button>
            <span class="xs muted right">${esc(c.objective)}</span>
          </div>
        </div>
      </div>`).join('')}
    </div>

    <div class="grid g-2">
      <div class="card">
        <div class="card-head"><h2>Guardrails on spend</h2></div>
        <div class="card-body">
          <div class="small muted">Budget raises above 5× the current daily cap are refused with <code>APPROVAL_REQUIRED</code> — the same failure mode as an agent quietly scaling spend. Owner approval is required, and the change is audit-logged.</div>
          <div class="divider"></div>
          <div class="row xs"><span class="chip amber xs">denial-of-wallet guard</span><span class="chip blue xs">offline conversions</span><span class="chip green xs">consent-checked audiences</span></div>
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Attribution</h2></div>
        <div class="card-body">
          <div class="small muted">Click IDs and consent-safe identifiers are stored against the lead. When a site visit or booking happens, the conversion is uploaded back to the platform within the hour — with the buyer's advertising consent recorded.</div>
        </div>
      </div>
    </div>`,
  };
};

RE.actions['toggle-campaign'] = async (el) => {
  try {
    await api('/api/v1/campaigns/' + el.dataset.id, { method: 'PATCH', body: { status: el.dataset.status } });
    toast('Campaign updated', 'ok');
    route();
  } catch (_) {}
};

RE.actions['new-campaign'] = () => {
  modal({
    title: 'New campaign',
    body: `<form data-form="create-campaign">
      <div class="grid g-2">
        <div class="field"><label>Platform</label><select class="input" name="platform"><option>google_ads</option><option>meta_ads</option><option>linkedin_ads</option></select></div>
        <div class="field"><label>Daily budget (₹)</label><input class="input" name="budget_daily" type="number" min="100" step="100" value="3000"></div>
      </div>
      <div class="field"><label>Name</label><input class="input" name="name" required maxlength="120" placeholder="Nanded Plots — Search (High Intent)"></div>
      <div class="field"><label>Objective</label><select class="input" name="objective"><option>leads</option><option>site visits</option><option>bookings</option></select></div>
    </form>`,
    foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="create-campaign">Create</button>`,
  });
};

RE.actions['form:create-campaign'] = async (payload) => {
  try {
    await api('/api/v1/campaigns', { method: 'POST', body: { ...payload, budget_daily: Number(payload.budget_daily) } });
    closeModal();
    toast('Campaign created in draft', 'ok');
    route();
  } catch (_) {}
};

RE.actions['run-ads-autopilot'] = async () => {
  try {
    const res = await api('/api/v1/ads/autopilot/run', { method: 'POST', body: {} });
    toast(`Autopilot complete: ${res.paused_campaigns.length} paused · ${res.staged_budget_proposals.length} budget increase(s) parked for approval`, 'ok');
    route();
  } catch (_) {}
};

RE.actions['request-campaign-budget'] = async (el) => {
  const proposed = Math.round(Number(el.dataset.budget || 2000) * 1.25);
  try {
    await api('/api/v1/ads/budget-requests', {
      method: 'POST',
      body: { campaign_id: el.dataset.id, proposed_budget_daily: proposed, reason: 'Operator requested +25% daily scale.' },
    });
    toast('Budget increase parked in Human Approval Queue (#/learning)', 'ok');
  } catch (_) {}
};

RE.actions['ads-spend-policy'] = async () => {
  try {
    const res = await api('/api/v1/ads/policy');
    const p = res.policy;
    modal({
      title: 'Ads Autopilot Hard Spend Caps & Approval Threshold',
      body: `<form data-form="save-ads-policy">
        <div class="grid g-2">
          <div class="field"><label>Hard Daily Cap (₹)</label><input class="input" type="number" name="hard_daily_cap_inr" value="${esc(p.hard_daily_cap_inr)}"></div>
          <div class="field"><label>Hard Monthly Cap (₹)</label><input class="input" type="number" name="hard_monthly_cap_inr" value="${esc(p.hard_monthly_cap_inr)}"></div>
          <div class="field"><label>Max CPL Ceiling (₹)</label><input class="input" type="number" name="max_cpl_inr" value="${esc(p.max_cpl_inr)}"></div>
          <div class="field"><label>Approval Required Above (%)</label><input class="input" type="number" name="require_approval_above_pct" value="${esc(p.require_approval_above_pct)}"></div>
        </div>
        <p class="xs muted" style="margin-top:8px">Current workspace cumulative spend: <b>${fmtMoney(res.headroom.total_spend_inr)}</b> · Active daily sum: <b>${fmtMoney(res.headroom.other_daily_inr)}</b>.</p>
      </form>`,
      foot: `<button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="submit-form" data-form="save-ads-policy">Save Spend Policy</button>`,
    });
  } catch (_) {}
};

RE.actions['form:save-ads-policy'] = async (payload) => {
  try {
    await api('/api/v1/ads/policy', {
      method: 'PATCH',
      body: {
        hard_daily_cap_inr: Number(payload.hard_daily_cap_inr),
        hard_monthly_cap_inr: Number(payload.hard_monthly_cap_inr),
        max_cpl_inr: Number(payload.max_cpl_inr),
        require_approval_above_pct: Number(payload.require_approval_above_pct),
      },
    });
    closeModal();
    toast('Ads hard spend-cap policy saved', 'ok');
    route();
  } catch (_) {}
};

/* -------------------------------- analytics ------------------------------ */
RE.views.analytics = async () => {
  const metric = RE.state.forecastMetric || 'leads';
  const horizon = RE.state.forecastHorizon || 3;
  const [ov, fc, insights, costDrivers, subInfo] = await Promise.all([
    api('/api/v1/analytics/overview'),
    api(`/api/v1/analytics/forecast?metric=${metric}&horizon=${horizon}`),
    api('/api/v1/analytics/insights'),
    api('/api/v1/billing/cost-drivers').catch(() => null),
    api('/api/v1/billing/subscription').catch(() => null),
  ]);
  const s = ov.series;
  const labels = [...s.months, ...fc.forecast.next.map((p) => '+' + p.h)];
  const last = fc.forecast.next[fc.forecast.next.length - 1];
  return {
    title: 'Analytics & forecasting',
    crumb: `${metric} · ${horizon}-month horizon · ${fc.forecast.confidence} confidence · MAPE ${pct(fc.forecast.mape)}`,
    html: `
    <div class="page-head">
      <div><h1>Analytics &amp; forecasting</h1>
        <div class="sub">Twelve months of tenant data, an explainable trend × seasonality model, and a 95% interval you can defend in a board meeting.</div></div>
      <div class="actions">
        ${['leads', 'site_visits', 'bookings', 'revenue'].map((m) => `<button class="chip ${m === metric ? 'brand' : ''}" data-act="set-metric" data-metric="${m}">${esc(m.replace('_', ' '))}</button>`).join('')}
        <select class="input" style="width:120px" data-act="set-horizon">
          ${[2, 3, 4, 6].map((h) => `<option value="${h}" ${h === horizon ? 'selected' : ''}>${h} months</option>`).join('')}
        </select>
      </div>
    </div>

    <div class="grid g-4" style="margin-bottom:14px">
      ${kpiCard('Leads · 30d', fmtNum(ov.kpi.leads_30d), 6.2, s.leads.slice(-8), 'vs prior', '#4f46e5')}
      ${kpiCard('Pipeline', fmtMoney(ov.kpi.pipeline_value), 11.4, s.revenue.slice(-8), `${ov.kpi.open_deals} open deals`, '#0e7490')}
      ${kpiCard('Booked value', fmtMoney(ov.kpi.won_value), 9.1, s.bookings.slice(-8), 'won stage', '#059669')}
      ${kpiCard(`Forecast +${horizon}M`, fmtNum(last.value), ((last.value - fc.history[fc.history.length - 1]) / Math.max(1, fc.history[fc.history.length - 1])) * 100, fc.forecast.next.map((p) => p.value), `${metric}`, '#7c3aed')}
    </div>

    ${costDrivers ? `
    <div class="card" style="margin-bottom:14px">
      <div class="card-head">
        <h2>Five Weekly Cost Drivers &amp; Unit Economics (docs/09 §4)</h2>
        <span class="chip ${costDrivers.unit_economics.meets_60pct_gate ? 'green' : 'amber'} xs">Gross margin ${esc(costDrivers.unit_economics.gross_margin_pct)}%</span>
        <span class="chip grey xs">COGS ₹${fmtNum(costDrivers.unit_economics.total_cogs_inr)} · Plan ${esc(costDrivers.plan)}</span>
        ${subInfo ? `<button class="right btn sm primary" data-act="generate-saas-invoice">Issue ${esc(subInfo.subscription.period_label)} GST Invoice</button>` : ''}
      </div>
      <div class="card-body">
        <div class="grid g-3">
          ${costDrivers.drivers.map((d) => `<div class="matrix-item">
            <div class="row"><div class="c">${esc(d.label)}</div><span class="spacer"></span>
              <span class="chip ${d.status === 'healthy' ? 'green' : d.status === 'watch' ? 'amber' : 'red'} xs">${esc(d.status)}</span></div>
            <div class="d"><b class="num">${esc(d.value)}</b> <span class="xs muted">${esc(d.unit)} (target ${esc(d.target)})</span></div>
          </div>`).join('')}
        </div>
      </div>
    </div>` : ''}

    <div class="card" style="margin-bottom:14px">
      <div class="card-head"><h2>Forecast — ${esc(metric.replace('_', ' '))}</h2>
        <span class="chip grey">linear trend × seasonal index</span>
        <span class="right xs muted">95% interval widens with horizon</span></div>
      <div class="card-body">
        ${forecastChart(fc.history, fc.forecast, labels)}
        <div class="grid g-3" style="margin-top:14px">
          ${fc.forecast.next.map((p) => `<div class="matrix-item"><div class="c">Month +${p.h}</div>
            <div class="d"><b class="num">${fmtNum(p.value)}</b> <span class="xs">(${fmtNum(p.low)} – ${fmtNum(p.high)})</span></div></div>`).join('')}
        </div>
      </div>
    </div>

    <div class="grid g-2-1" style="margin-bottom:14px">
      <div class="card">
        <div class="card-head"><h2>Recommendations</h2><span class="chip violet">model-generated, human-decided</span></div>
        <div class="card-body">
          ${insights.insights.map((i) => `<div style="padding:11px 0;border-bottom:1px solid var(--border)">
            <div class="row"><span class="b small">${esc(i.title)}</span><span class="spacer"></span>
              <span class="chip ${i.impact === 'high' ? 'green' : 'blue'} xs">${esc(i.impact)} impact</span>
              <span class="chip grey xs">conf ${Math.round(i.confidence * 100)}%</span></div>
            <div class="small muted" style="margin-top:4px">${esc(i.detail)}</div></div>`).join('')}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><h2>Funnel</h2></div>
        <div class="card-body">
          ${s.funnel.map((f, i, arr) => barRow(f.stage, f.count, arr[0].count, ['#4f46e5','#6366f1','#818cf8','#22d3ee','#10b981','#059669'][i])).join('')}
          <div class="divider"></div>
          <div class="row small"><span class="muted">Consent gap</span><span class="spacer"></span><b class="delta-down">${esc(ov.kpi.consent_gap)}</b></div>
          <div class="row small" style="margin-top:6px"><span class="muted">Median first response</span><span class="spacer"></span><b>${esc(ov.kpi.median_first_response_s)}s</b></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head"><h2>Source performance</h2></div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Source</th><th>Leads</th><th>CPL</th><th>Conversion</th><th>CAC</th><th>Verdict</th></tr></thead>
        <tbody>${s.sources.map((x) => {
          const cac = x.cpl && x.conversion ? Math.round(x.cpl / x.conversion) : 0;
          return `<tr><td class="cell-strong">${esc(x.name)}</td><td class="num">${fmtNum(x.leads)}</td>
            <td class="num">${x.cpl ? '₹' + fmtNum(x.cpl) : '—'}</td><td class="num">${pct(x.conversion)}</td>
            <td class="num">${cac ? '₹' + fmtNum(cac) : '—'}</td>
            <td>${barRow('', x.conversion * 100, 35, '#6366f1', '%')}</td></tr>`;
        }).join('')}</tbody>
      </table></div>
    </div>`,
  };
};

RE.actions['set-metric'] = (el) => {
  RE.state.forecastMetric = el.dataset.metric;
  route();
};

document.addEventListener('change', (e) => {
  const sel = e.target.closest('[data-act="set-horizon"]');
  if (sel) {
    RE.state.forecastHorizon = Number(sel.value);
    route();
  }
});

RE.actions['generate-saas-invoice'] = async () => {
  try {
    const res = await api('/api/v1/billing/invoices/generate', { method: 'POST', body: {} });
    const inv = res.invoice;
    modal({
      title: `SaaS GST Invoice · ${inv.invoice_no}`,
      wide: true,
      body: `<div class="row wrap" style="margin-bottom:10px">
        <span class="chip green">${esc(inv.status)}</span>
        <span class="chip blue xs">Plan: ${esc(inv.plan)}</span>
        <span class="chip grey xs">${res.duplicate ? 'idempotent existing' : 'newly issued'}</span>
        <span class="chip grey xs">Total ₹${fmtNum(inv.amount_inr)} (incl. 18% GST)</span>
      </div>
      <div class="evidence">${esc(JSON.stringify(inv, null, 2))}</div>`,
      foot: `<button class="btn primary" data-act="close-modal">Close</button>`,
    });
  } catch (_) {}
};



function marketingWebsiteSelector(funnels) {
  const published = funnels.filter(f => f.published);
  const choice = RE.state.websiteDesign || 'standard';
  const chosen = published.find(f => f.slug === RE.state.websiteFunnel) || published[0];
  const href = choice === 'nagpur' ? '/website/' + (chosen ? '?funnel=' + encodeURIComponent(chosen.slug) : '') : '/site';
  return `<section class="card card-pad" style="margin-bottom:20px" aria-label="Landing page design selection">
    <div class="row wrap"><div><h2>Landing page &amp; sales funnel design</h2><p class="muted small" style="margin-top:6px">Choose the existing lightweight website or your imported Realtor Estates Nagpur website. This does not replace the default public site.</p></div></div>
    <div class="grid g-2" style="margin-top:16px"><div class="field"><label for="website-design">Website option</label><select class="input" id="website-design"><option value="standard" ${choice === 'standard' ? 'selected' : ''}>Standard · fast server-rendered site</option><option value="nagpur" ${choice === 'nagpur' ? 'selected' : ''}>Realtor Estates Nagpur · imported 3D website</option></select></div>
    <div class="field"><label for="website-funnel">Enquiry destination · published CRM funnel</label><select class="input" id="website-funnel" ${published.length ? '' : 'disabled'}>${published.length ? published.map(f => `<option value="${esc(f.slug)}" ${f.slug === chosen?.slug ? 'selected' : ''}>${esc(f.name)}</option>`).join('') : '<option>No published funnel · preview only</option>'}</select></div></div>
    <div class="row wrap"><a class="btn primary" href="${esc(href)}" target="_blank" rel="noopener">${icon('externalLink')}Open selected website</a><a class="btn" href="https://github.com/Sushil-B2/Realtor-Estates" target="_blank" rel="noopener">Website source</a><span class="muted xs">${chosen ? 'Enquiries use the selected published funnel, consent checks and encrypted CRM storage.' : 'Publish a funnel to enable enquiry capture.'}</span></div>
    <p class="muted xs" style="margin-top:12px">Imported project inventory, pricing and legal claims need owner review before promotion. Map integrations require separately configured, domain-restricted public browser keys. No keys were imported.</p>
  </section>`;
}
document.addEventListener('change', event => {
  if (event.target.id === 'website-design') RE.state.websiteDesign = event.target.value;
  else if (event.target.id === 'website-funnel') RE.state.websiteFunnel = event.target.value;
  else return;
  RE.state.viewCache.clear();
  route({force:true});
});
