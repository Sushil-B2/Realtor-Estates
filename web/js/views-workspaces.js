'use strict';

/* Focused operational workspaces. Existing legacy routes and actions remain intact. */
function workspacePage(title, sub, body, action = '') {
  return { title, crumb: workspaceForRoute(RE.state.route).label,
    html: `<div class="page-head"><div><h1>${esc(title)}</h1><div class="sub">${esc(sub)}</div></div>${action}</div>${body}` };
}
function workspaceTable(headers, rows) {
  return `<div class="card" style="overflow:auto"><table class="data resp-cards"><thead><tr>${headers.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.join('') : `<tr><td colspan="${headers.length}"><div class="empty">No records to show.</div></td></tr>`}</tbody></table></div>`;
}
RE.views.overview = RE.views.dashboard;
RE.views.dashboard = async () => {
  const b = await api('/api/v1/bootstrap');
  applyBootstrapState(b);
  const queues = [
    ['Speed-to-lead', b.sla?.overdue || 0, 'sla', 'clock', 'Unanswered enquiries needing attention'],
    ['Approval queue', b.pending_approvals || 0, 'learning', 'shieldCheck', 'Human decisions before AI changes go live'],
    ['Upcoming visits', (b.site_visits || []).filter(v => Number(v.scheduled_at) >= Date.now()/1000 && !['cancelled','completed','no_show'].includes(v.status)).length, 'visits', 'calendar', 'Coordinate the next property walkthrough'],
    ['Expiring holds', (b.units_summary?.expiring_holds || []).length, 'units', 'building', 'Review inventory before holds expire'],
  ];
  const tasks = (b.tasks || []).filter(t => !t.done).slice(0, 8);
  return workspacePage('Your action desk', 'Start with the next decision. Reporting lives in Executive Overview.',
    `<div class="grid g-4">${queues.map(([label,count,target,ico,desc]) => `<a class="card card-pad" href="#/${target}" data-nav>${icon(ico)}<h2 style="margin-top:12px">${label}</h2><div class="num" style="font-size:30px;margin:8px 0">${fmtNum(count)}</div><p class="muted small">${desc}</p></a>`).join('')}</div>
    <div style="margin-top:20px"><h2 style="margin-bottom:12px">Tasks to move forward</h2>${workspaceTable(['Task', 'Due', 'Status'], tasks.map(t => `<tr><td>${esc(t.title || t.name)}</td><td>${fmtDate(t.due_at)}</td><td><span class="chip blue">Open</span></td></tr>`))}</div>`,
    '<button class="btn primary" data-act="new-lead">New lead</button>');
};
RE.views.sla = async () => {
  const b = await api('/api/v1/leads/sla');
  const rows = [...(b.breached || []), ...(b.open || [])];
  return workspacePage('Speed-to-lead SLA', 'Unanswered enquiries ordered by response urgency. A logged reply must reflect an actual contact.',
    workspaceTable(['Buyer', 'Source', 'Status', 'Action'], rows.map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.source)}</td><td>${stageChip(r.status)}</td><td><button class="btn sm" data-act="sla-reply" data-id="${esc(r.contact_id)}">Log first reply</button></td></tr>`)));
};
RE.actions['sla-reply'] = async el => {
  if (!confirm('Confirm that you have actually replied to this buyer?')) return;
  await api('/api/v1/leads/' + encodeURIComponent(el.dataset.id) + '/respond', { method: 'POST', body: {channel:'call'} });
  await route({ force: true });
};
RE.views.visits = async () => {
  const b = await api('/api/v1/site-visits?limit=80');
  return workspacePage('Site visits', 'Schedule, attendance and follow-through in one place.',
    workspaceTable(['Buyer / property', 'Scheduled', 'Status', 'Next action'], (b.visits || []).map(v => `<tr><td>${esc(v.contact_name || v.contact_id)}<div class="muted xs">${esc(v.property_title || v.property_id)}</div></td><td>${fmtDate(v.scheduled_at)} ${fmtTime(v.scheduled_at)}</td><td>${stageChip(v.status)}</td><td>${(v.allowed_next || []).map(next => `<button class="btn sm" data-act="visit-status" data-id="${esc(v.id)}" data-status="${esc(next)}">${esc(next)}</button>`).join(' ')}</td></tr>`)),
    '<button class="btn primary" data-act="schedule-visit">Schedule visit</button>');
};
RE.views.properties = async () => {
  const b = await api('/api/v1/properties');
  return workspacePage('Listings catalogue', 'Real inventory only — no stock images or synthetic availability.',
    `<div class="grid g-3">${(b.properties || b.items || []).map(p => `<article class="card card-pad">${icon('building')}<h2 style="margin-top:12px">${esc(p.title)}</h2><p class="muted small">${esc(p.city)} · ${esc(p.type || p.category || '')}</p><div class="num b" style="margin:12px 0">${fmtMoney(p.price, false)}</div><a class="btn sm" href="#/units/${encodeURIComponent(p.id)}" data-nav>View units ${icon('arrowRight')}</a></article>`).join('') || '<div class="empty">No listings yet.</div>'}</div>`);
};
RE.views.units = async params => {
  const b = await api('/api/v1/properties');
  const properties = b.properties || b.items || [];
  const pid = params?.id || properties[0]?.id;
  const data = pid ? await api('/api/v1/properties/' + encodeURIComponent(pid) + '/units') : { units: [] };
  RE.state.unitRows = data.units || [];
  return workspacePage('Plot & unit matrix', 'Status is labelled as well as colour-coded. Holds and bookings are validated on the server.',
    `<div class="row wrap" style="margin-bottom:16px">${properties.map(p => `<a class="btn sm ${p.id === pid ? 'primary' : ''}" href="#/units/${encodeURIComponent(p.id)}" data-nav>${esc(p.title)}</a>`).join('')}</div>
    <div class="unit-matrix">${(data.units || []).map(u => `<article class="unit-tile ${esc(u.status)}"><h2>${esc(u.unit_no || u.id)}</h2>${stageChip(u.status)}<div class="num b" style="margin:10px 0">${fmtMoney(u.price, false)}</div><div class="muted xs">${esc(u.area_sqft || '—')} sq ft · ${esc(u.facing || '')}</div><div class="row wrap" style="margin-top:12px">${['available','blocked'].includes(u.status) ? `<button class="btn sm" data-act="unit-transaction" data-id="${esc(u.id)}" data-op="block">48h hold</button><button class="btn sm" data-act="unit-transaction" data-id="${esc(u.id)}" data-op="book">Book</button>` : ''}${['blocked','booked'].includes(u.status) ? `<button class="btn sm" data-act="unit-transaction" data-id="${esc(u.id)}" data-op="release">Release</button>` : ''}</div></article>`).join('') || '<div class="empty">This listing has no units.</div>'}</div>`);
};
RE.actions['unit-transaction'] = async el => {
  const op = el.dataset.op;
  if (!['block', 'book', 'release'].includes(op)) return;
  const unitId = el.dataset.id;
  const p = await api('/api/v1/pipeline');
  const deals = Object.values(p.columns || {}).flat().filter(d => !['lost','won'].includes(d.stage));
  modal({ title: op === 'release' ? 'Release unit' : op === 'book' ? 'Book unit' : 'Hold for 48 hours',
    body: `<form data-form="unit-transaction" id="unit-transaction-form"><input type="hidden" name="unit_id" value="${esc(unitId)}"><input type="hidden" name="op" value="${op}">${op !== 'release' ? `<div class="field"><label for="unit-deal">Associated deal</label><select class="input" name="deal_id" id="unit-deal" required>${deals.map(d => `<option value="${esc(d.id)}">${esc(d.title)}</option>`).join('')}</select></div>` : '<p>Release this unit back to available inventory?</p>'}<p class="muted small">Availability, permissions and business rules are rechecked before saving.</p><button type="submit" class="btn primary">Confirm ${op}</button></form>` });
};
RE.actions['form:unit-transaction'] = async payload => {
  const op = payload.op;
  if (!['block','book','release'].includes(op)) return;
  await api('/api/v1/units/' + encodeURIComponent(payload.unit_id) + '/' + op, { method: 'POST', body: op === 'release' ? {} : { deal_id: payload.deal_id, hours: 48 } });
  closeModal(); toast('Inventory updated', 'ok'); await route({ force: true });
};
RE.views.vault = async () => workspacePage('7/12 & RERA vault', 'Review title, land revenue and statutory checks before moving a transaction forward.',
  '<div class="grid g-2"><article class="card card-pad"><h2>Revenue & title verification</h2><p class="muted small" style="margin:12px 0">7/12, NA clearance and title documentation tools.</p><button class="btn primary" data-act="govt-revenue-vault-modal">Open verification workspace</button></article><article class="card card-pad"><h2>Agreement readiness</h2><p class="muted small" style="margin:12px 0">Inspect deal-specific registration and handover requirements.</p><a class="btn" href="#/closing" data-nav>Open closing desk</a></article></div>');
RE.views.closing = async () => {
  const p = await api('/api/v1/pipeline');
  RE.state.pipeline = p;
  const deals = Object.entries(p.columns || {}).flatMap(([stage, rows]) => rows.map(d => ({...d, stage})));
  return workspacePage('Closing & e-Reg desk', 'Statutory estimates and registration evidence remain subject to verification.',
    workspaceTable(['Deal', 'Stage', 'Consideration', 'Next action'], deals.map(d => `<tr><td><button class="btn ghost sm" data-act="open-deal" data-id="${esc(d.id)}">${esc(d.title)}</button></td><td>${stageChip(d.stage)}</td><td class="num">${fmtMoney(d.value, false)}</td><td><button class="btn sm" data-act="govt-cost-sheet-modal" data-value="${esc(d.value)}">Cost sheet</button> <button class="btn sm" data-act="deal-govt-desk" data-id="${esc(d.id)}" data-value="${esc(d.value)}">e-Reg desk</button></td></tr>`)));
};
RE.actions['set-stage'] = async el => {
  await api('/api/v1/deals/' + encodeURIComponent(el.dataset.id), {method:'PATCH', body:{stage:el.dataset.stage}});
  RE.state.pipeline = null;
  closeDrawer(); await route({ force:true });
};
RE.views.ailab = async () => workspacePage('Voice & AI sandbox', 'Advanced tools are opt-in. Simulations do not imply a live provider is configured.',
  `<div class="grid g-3">${[
    ['Voice simulation','phone','voice-test-quick','Test a Marathi-language AI call.'],
    ['Inbound receptionist','inbox','inbound-receptionist','Inspect inbound call handling.'],
    ['On-device pre-screen','sparkles','ondevice-prescreen-modal','Redact supported PII patterns locally.'],
    ['Specialist delegation','bot','a2a-delegate','Delegate a scoped specialist task.'],
    ['Quarantined document intake','shield','dual-llm-intake','Inspect document handling boundaries.'],
    ['Pack exchange','layers','pack-exchange','Review and import governed agent packs.'],
    ['AP2 commerce','lock','ap2-commerce-modal','Review commerce authorization controls.'],
  ].map(([title,ico,act,desc]) => `<article class="card card-pad">${icon(ico)}<h2 style="margin-top:12px">${title}</h2><p class="muted small" style="margin:12px 0">${desc}</p><button class="btn" data-act="${act}">Open tool</button></article>`).join('')}</div>`);

/* Keep visit planning inside the persistent shell. All transitions remain server gated. */
RE.actions['schedule-visit'] = async () => {
  const [contacts, listings] = await Promise.all([
    api('/api/v1/contacts?limit=200'), api('/api/v1/properties'),
  ]);
  modal({title:'Schedule a site visit', body:`<form data-form="schedule-visit">
    <div class="field"><label for="visit-buyer">Buyer</label><select class="input" id="visit-buyer" name="contact_id" required>${(contacts.contacts || []).map(c => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></div>
    <div class="field"><label for="visit-listing">Property</label><select class="input" id="visit-listing" name="property_id" required>${(listings.properties || listings.items || []).map(p => `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join('')}</select></div>
    <div class="field"><label for="visit-at">Visit date &amp; time (your device timezone)</label><input class="input" type="datetime-local" id="visit-at" name="scheduled_at" required></div>
    <div class="field"><label for="visit-duration">Duration in minutes</label><input class="input" type="number" id="visit-duration" name="duration_min" min="15" max="240" value="45" required></div>
    <div class="field"><label for="visit-pickup">Pickup instructions (optional)</label><input class="input" id="visit-pickup" name="pickup" maxlength="300"></div>
    <p class="muted small">Calendar conflicts and permissions are checked on save.</p>
    <button class="btn primary" type="submit">Schedule visit</button></form>`});
};
RE.actions['form:schedule-visit'] = async (payload, form) => {
  const when = new Date(payload.scheduled_at).getTime();
  if (!Number.isFinite(when) || when <= Date.now()) return toast('Choose a future date and time.', 'err');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  try {
    await api('/api/v1/site-visits', {method:'POST', body:{
      contact_id:payload.contact_id, property_id:payload.property_id,
      scheduled_at:Math.floor(when/1000), duration_min:Number(payload.duration_min), pickup:payload.pickup,
    }});
    closeModal(); toast('Visit scheduled', 'ok'); await route({force:true});
  } catch (_) { button.disabled = false; }
};
RE.actions['visit-status'] = async el => {
  if (!confirm('Change this visit to ' + el.dataset.status + '?')) return;
  try {
    await api('/api/v1/site-visits/' + encodeURIComponent(el.dataset.id), {method:'PATCH', body:{status:el.dataset.status}});
    toast('Visit updated', 'ok'); await route({force:true});
  } catch (_) {}
};

RE.views.partners = async () => {
  const [p,r,x] = await Promise.all([api('/api/v1/cp/partners'),api('/api/v1/cp/registrations'),api('/api/v1/cp/payouts')]);
  const role = RE.state.me?.user?.role;
  const decide = ['owner','admin','manager'].includes(role);
  const pay = ['owner','admin'].includes(role);
  const claims = r.registrations || [];
  const payouts = x.payouts || [];
  return workspacePage('Channel partners', 'Protected buyer registrations and audited payout reconciliation, without leaving your workspace.',
    workspaceTable(['Partner','Commission','Registrations','Protection'], (p.partners || []).map(v => `<tr><td>${esc(v.firm || v.name)}</td><td class="num">${esc(v.commission_pct)}%</td><td>${esc(v.approved || 0)} approved</td><td>${esc(v.protection_days)} days</td></tr>`)) +
    `<h2 style="margin:20px 0 12px">Registration claims</h2>` + workspaceTable(['Buyer','Partner','State','Decision'],claims.map(v => `<tr><td>${esc(v.contact_name || v.contact_id)}</td><td>${esc(v.cp_name)}</td><td>${stageChip(v.state)}</td><td>${v.state === 'pending' && decide ? `<button class="btn sm" data-act="partner-claim" data-id="${esc(v.id)}" data-decision="approve">Approve</button> <button class="btn sm" data-act="partner-claim" data-id="${esc(v.id)}" data-decision="reject">Reject</button>` : '—'}</td></tr>`)) +
    `<h2 style="margin:20px 0 12px">Payout ledger</h2>` + workspaceTable(['Partner / deal','Amount','Status','Action'],payouts.map(v => `<tr><td>${esc(v.partner)}<div class="muted xs">${esc(v.title || v.deal_id)}</div></td><td class="num">${fmtMoney(v.amount,false)}</td><td>${stageChip(v.status)}</td><td>${v.status === 'pending' && pay ? `<button class="btn sm" data-act="partner-pay" data-id="${esc(v.deal_id)}">Reconcile payment</button>` : '—'}</td></tr>`)));
};
RE.actions['partner-claim'] = el => {
  const decision = el.dataset.decision;
  if (!['approve','reject'].includes(decision)) return;
  modal({title:decision === 'approve' ? 'Approve protected claim' : 'Reject partner claim',body:`<form data-form="partner-claim"><input type="hidden" name="id" value="${esc(el.dataset.id)}"><input type="hidden" name="decision" value="${decision}"><div class="field"><label for="claim-reason">Reason ${decision === 'reject' ? '(required)' : '(optional)'}</label><textarea class="input" id="claim-reason" name="reason" maxlength="500" ${decision === 'reject' ? 'required' : ''}></textarea></div><p class="muted small">This decision is audited and may affect partner protection.</p><button class="btn primary" type="submit">Confirm ${decision}</button></form>`});
};
RE.actions['form:partner-claim'] = async (payload,form) => {
  const button = form.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    await api('/api/v1/cp/registrations/' + encodeURIComponent(payload.id) + '/decide',{method:'POST',body:{decision:payload.decision,reason:payload.reason}});
    closeModal(); toast('Claim decision saved','ok'); await route({force:true});
  } catch (_) { button.disabled = false; }
};
RE.actions['partner-pay'] = el => modal({title:'Reconcile an existing partner payment',body:`<form data-form="partner-pay"><input type="hidden" name="id" value="${esc(el.dataset.id)}"><p class="muted small">This records a payment already made. It does not initiate a bank transfer.</p><div class="field"><label for="partner-reference">UPI / NEFT / cheque reference</label><input class="input" id="partner-reference" name="reference" required maxlength="200" autocomplete="off"></div><button class="btn primary" type="submit">Record payment</button></form>`});
RE.actions['form:partner-pay'] = async (payload,form) => {
  const button = form.querySelector('button[type="submit"]'); button.disabled = true;
  try {
    await api('/api/v1/cp/payouts/' + encodeURIComponent(payload.id) + '/pay',{method:'POST',body:{reference:payload.reference}});
    closeModal(); toast('Payout reconciled','ok'); await route({force:true});
  } catch (_) { button.disabled = false; }
};
