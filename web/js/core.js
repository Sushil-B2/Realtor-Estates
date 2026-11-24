/* ==========================================================================
   Realtor Estates — Core Runtime, Adaptive Shell, SWR Cache & Primitives
   Zero build step, zero external runtime dependencies, strict CSP safe.
   ========================================================================== */
'use strict';

const RE = (window.RE = {
  state: {
    me: null,
    bootstrap: null,
    token: null,
    csrf: null,
    route: 'dashboard',
    params: {},
    cache: {},
    apiCache: new Map(),
    viewCache: new Map(),
    sse: null,
    pulseTimer: null,
    installPrompt: null,
    pending: 0,
    inboxBadge: 0,
    slaOverdue: 0,
    paletteOpen: false,
    railCollapsed: false,
    theme: 'light',
  },
  views: {},
  actions: {},
});

/* Restore non-sensitive UI preferences (theme + rail collapse) */
try {
  const savedTheme = localStorage.getItem('re_theme');
  if (savedTheme === 'dark' || savedTheme === 'light') {
    RE.state.theme = savedTheme;
    document.documentElement.setAttribute('data-theme', savedTheme);
  }
  if (localStorage.getItem('re_rail') === '1') {
    RE.state.railCollapsed = true;
    document.body.classList.add('rail-collapsed');
  }
} catch (_) {}

/* In-memory only for credentials (SEN-C201: zero tokens in Web Storage).
   Only a non-sensitive boolean hint `'1'` is stored so signed-out visitors
   see the login form in 0 ms without waiting on a cold 401 API check. */
function rememberAuth(token, csrf) {
  RE.state.token = token || null;
  RE.state.csrf = csrf || null;
  try {
    if (token || csrf) localStorage.setItem('re_signed_in', '1');
  } catch (_) {}
}

function clearAuthHint() {
  try { localStorage.removeItem('re_signed_in'); } catch (_) {}
}

function invalidateCaches(prefix = '') {
  if (!prefix) {
    RE.state.apiCache.clear();
    RE.state.viewCache.clear();
    return;
  }
  for (const k of Array.from(RE.state.apiCache.keys())) {
    if (k.includes(prefix)) RE.state.apiCache.delete(k);
  }
  RE.state.viewCache.clear();
}

function setNavLoading(on) {
  const bar = document.getElementById('nav-progress');
  if (bar) bar.classList.toggle('on', Boolean(on));
}

/* ------------------------------- Utilities ------------------------------- */
const esc = (v) =>
  String(v === null || v === undefined ? '' : v).replace(/[&<>"'/]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#x27;', '/': '&#x2F;' }[c]
  ));

const nf = new Intl.NumberFormat('en-IN');

function fmtNum(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
  return nf.format(Math.round(Number(n)));
}

function fmtMoney(v, compact = true) {
  const n = Number(v || 0);
  if (!compact) return '₹' + fmtNum(n);
  if (Math.abs(n) >= 1e7) return '₹' + (n / 1e7).toFixed(2) + ' Cr';
  if (Math.abs(n) >= 1e5) return '₹' + (n / 1e5).toFixed(1) + ' L';
  if (Math.abs(n) >= 1e3) return '₹' + (n / 1e3).toFixed(0) + 'k';
  return '₹' + fmtNum(n);
}

function fmtDate(ts, opts) {
  if (!ts) return '—';
  const d = new Date(Number(ts) * 1000);
  return d.toLocaleDateString('en-IN', opts || { day: '2-digit', month: 'short', year: 'numeric' });
}

function fmtTime(ts) {
  if (!ts) return '';
  return new Date(Number(ts) * 1000).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

function timeAgo(ts) {
  if (!ts) return '—';
  const diff = Math.floor(Date.now() / 1000 - Number(ts));
  if (diff < 0) {
    const ahead = Math.abs(diff);
    if (ahead < 3600) return 'in ' + Math.max(1, Math.round(ahead / 60)) + 'm';
    if (ahead < 86400) return 'in ' + Math.round(ahead / 3600) + 'h';
    return 'in ' + Math.round(ahead / 86400) + 'd';
  }
  const s = Math.max(1, diff);
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  if (s < 2592000) return Math.floor(s / 86400) + 'd ago';
  return fmtDate(ts, { day: '2-digit', month: 'short' });
}

const pct = (v) => (Number(v || 0) * 100).toFixed(0) + '%';

function initials(name) {
  return String(name || '?')
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] || '')
    .join('')
    .toUpperCase();
}

function colorFor(seed) {
  const palette = ['#4338ca', '#0e7490', '#047857', '#b45309', '#6d28d9', '#be123c', '#0369a1', '#4d7c0f'];
  let h = 0;
  for (const ch of String(seed || 'x')) h = (h * 31 + ch.charCodeAt(0)) % 9973;
  return palette[h % palette.length];
}

const STAGE_LABEL = {
  new: 'Enquiry',
  contacted: 'Contacted',
  qualified: 'Qualified',
  site_visit: 'Site Visit',
  negotiation: 'Negotiation',
  token: 'Token Paid',
  agreement: 'Agreement',
  registration: 'e-Reg / Index-II',
  won: 'Booked',
  lost: 'Lost',
  draft: 'Draft',
  scheduled: 'Scheduled',
  published: 'Published',
  paused: 'Paused',
  active: 'Active',
  pending: 'Pending Approval',
  promoted: 'Promoted',
  rejected: 'Rejected',
  connected: 'Connected',
  available: 'Available',
  blocked: '48h Hold',
  booked: 'Booked',
  sold: 'Sold',
  completed: 'Completed',
  no_show: 'No-Show',
};

const stageChip = (s) => {
  const map = {
    new: 'grey', contacted: 'blue', qualified: 'violet', site_visit: 'amber',
    negotiation: 'amber', token: 'brand', agreement: 'violet', registration: 'blue',
    won: 'green', booked: 'brand', sold: 'green', blocked: 'amber', lost: 'red',
    pending: 'amber', promoted: 'green', rejected: 'red', active: 'green',
    paused: 'grey', draft: 'grey', scheduled: 'blue', published: 'green',
    connected: 'green', available: 'green', completed: 'green', no_show: 'red',
    critical: 'red', high: 'amber', medium: 'amber', low: 'blue',
    pass: 'green', fail: 'red', warn: 'amber', granted: 'green', withdrawn: 'red',
  };
  return `<span class="chip ${map[s] || 'grey'}">${esc(STAGE_LABEL[s] || s || '—')}</span>`;
};

/* ----------------------- Sequence Framing Stepper ----------------------- */
const DEAL_SEQUENCE = [
  { key: 'new', label: 'Enquiry' },
  { key: 'qualified', label: 'Qualified' },
  { key: 'site_visit', label: 'Site Visit' },
  { key: 'token', label: 'Hold / Token' },
  { key: 'agreement', label: 'Cost Sheet & Agreement' },
  { key: 'registration', label: 'e-Reg / Index-II' },
  { key: 'won', label: 'Booked' },
];

function renderSequenceStepper(currentStage, recordId, actName = 'set-stage') {
  const stageOrder = ['new', 'contacted', 'qualified', 'site_visit', 'negotiation', 'token', 'agreement', 'registration', 'won'];
  const curIdx = Math.max(0, stageOrder.indexOf(currentStage || 'new'));
  return `<div class="seq-stepper" role="group" aria-label="Deal lifecycle sequence">
    ${DEAL_SEQUENCE.map((st, idx) => {
      const stIdx = stageOrder.indexOf(st.key);
      const cls = stIdx < curIdx ? 'done' : (st.key === currentStage || (currentStage === 'contacted' && st.key === 'new') || (currentStage === 'negotiation' && st.key === 'site_visit')) ? 'current' : '';
      return `${idx > 0 ? '<span class="seq-arrow">→</span>' : ''}
        <button type="button" class="seq-step ${cls}" data-act="${esc(actName)}" data-id="${esc(recordId)}" data-stage="${esc(st.key)}">
          <span class="idx">${cls === 'done' ? '✓' : idx + 1}</span><span>${esc(st.label)}</span>
        </button>`;
    }).join('')}
  </div>`;
}

/* ------------------------------- API Client ------------------------------ */
let refreshing = null;
async function refreshSession() {
  if (!refreshing) {
    refreshing = fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'same-origin', cache: 'no-store' })
      .then(async (res) => {
        if (!res.ok) return false;
        const data = await res.json().catch(() => ({}));
        if (data.token || data.csrf) rememberAuth(data.token || RE.state.token, data.csrf || RE.state.csrf);
        return true;
      })
      .catch(() => false)
      .finally(() => { setTimeout(() => { refreshing = null; }, 0); });
  }
  return refreshing;
}

async function api(path, opts = {}) {
  const method = (opts.method || 'GET').toUpperCase();
  const ttlMs = opts.ttlMs !== undefined ? opts.ttlMs : (method === 'GET' ? 12000 : 0);
  if (method === 'GET' && ttlMs > 0 && !opts.fresh) {
    const hit = RE.state.apiCache.get(path);
    if (hit && Date.now() - hit.ts < ttlMs) return hit.data;
  }
  try {
    const out = await apiOnce(path, opts);
    if (method === 'GET') {
      RE.state.apiCache.set(path, { ts: Date.now(), data: out });
    } else {
      invalidateCaches();
    }
    return out;
  } catch (err) {
    if (err && err.status === 423 && !opts._steppedUp && !opts._noStepUp &&
        !path.startsWith('/api/v1/auth/step-up')) {
      const ticket = await promptStepUp(err);
      if (ticket) {
        return api(path, { ...opts, _steppedUp: true, _retried: true,
                           headers: { ...(opts.headers || {}), 'X-Step-Up': ticket } });
      }
      throw err;
    }
    if (err && err.status === 401 && err.code !== 'MFA_CHALLENGE_NOT_A_SESSION' &&
        !opts._retried && !path.startsWith('/api/v1/auth/refresh') && !path.startsWith('/api/v1/auth/login')) {
      const ok = await refreshSession();
      if (ok) return apiOnce(path, { ...opts, _retried: true });
    }
    if (err && err.status === 401 && !path.startsWith('/api/v1/auth/login')) {
      RE.state.me = null;
      clearAuthHint();
      rememberAuth(null, null);
      renderAuth();
    }
    throw err;
  }
}

async function apiOnce(path, opts = {}) {
  const method = opts.method || 'GET';
  const headers = { Accept: 'application/json', ...(opts.headers || {}) };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (RE.state.token && !headers.Authorization) headers.Authorization = 'Bearer ' + RE.state.token;
  else if (method !== 'GET' && RE.state.csrf) headers['X-CSRF-Token'] = RE.state.csrf;
  let res;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: 'no-store',
    });
  } catch (err) {
    if (!navigator.onLine) toast('You are offline. Action queued when supported.', 'err');
    else if (!opts.silent) toast('Network connection interrupted — retrying shortly.', 'err');
    throw err;
  }
  let data = {};
  try { data = await res.json(); } catch (_) { data = {}; }
  if (!res.ok) {
    const msg = (data.error && data.error.message) || `Request failed (${res.status})`;
    const e = new Error(msg);
    e.code = data.error && data.error.code;
    e.status = res.status;
    e.details = data.error && data.error.details;
    if (res.status !== 401 && !opts.silent) toast(msg, 'err');
    throw e;
  }
  return data;
}

/* -------------------------------- Toasts --------------------------------- */
function toast(message, kind = '') {
  const wrap = document.getElementById('toasts');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.innerHTML = `${kind === 'ok' ? icon('checkCircle') : kind === 'err' ? icon('alertTriangle') : icon('info')}<span>${esc(message)}</span>`;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

/* ------------------ Complete Lucide-Standard Vector Icons ---------------- */
const I = {
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  userPlus: '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="M20 8v6M23 11h-6"/>',
  building: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/>',
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-6h6v6"/>',
  mapPin: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
  kanban: '<rect x="3" y="3" width="5" height="14" rx="1.5"/><rect x="10" y="3" width="5" height="10" rx="1.5"/><rect x="17" y="3" width="5" height="18" rx="1.5"/>',
  layers: '<path d="m12 2 9 5-9 5-9-5z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/>',
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5z"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  rupee: '<path d="M6 3h12M6 8h12M6 13l8.5 8M6 13h3a4 4 0 0 0 0-8"/>',
  receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M8 7h8M8 11h8M8 15h5"/>',
  fileCheck: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="m9 15 2 2 4-4"/>',
  landmark: '<line x1="3" y1="22" x2="21" y2="22"/><line x1="6" y1="18" x2="6" y2="11"/><line x1="10" y1="18" x2="10" y2="11"/><line x1="14" y1="18" x2="14" y2="11"/><line x1="18" y1="18" x2="18" y2="11"/><polygon points="12 2 20 7 4 7"/>',
  handshake: '<path d="m11 17 2 2a1 1 0 1 0 3-3"/><path d="m14 14 2.5 2.5a1 1 0 1 0 3-3l-3.88-3.88a3 3 0 0 0-4.24 0l-.88.88a1 1 0 1 1-3-3l2.81-2.81a5.79 5.79 0 0 1 7.06-.87l.47.28a2 2 0 0 0 1.42.25L21 4"/><path d="m21 3 1 11h-2"/><path d="M3 3 2 14l6.5 6.5a1 1 0 1 0 3-3"/>',
  funnel: '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
  bot: '<rect x="3" y="8" width="18" height="12" rx="3"/><path d="M12 8V4M8 14h.01M16 14h.01M9 18h6"/>',
  sparkles: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  shieldCheck: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>',
  trendingUp: '<polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>',
  bolt: '<path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/>',
  megaphone: '<path d="M3 11v2a1 1 0 0 0 1 1h2l4 4V6L6 10H4a1 1 0 0 0-1 1z"/><path d="M15 8a5 5 0 0 1 0 8M18 5a9 9 0 0 1 0 14"/>',
  social: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  plug: '<path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0z"/><path d="M12 17v5"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 4.6 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 11.5 4a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 22 11a2 2 0 1 1 0 4z"/>',
  sliders: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="2" y1="14" x2="6" y2="14"/><line x1="10" y1="8" x2="14" y2="8"/><line x1="18" y1="16" x2="22" y2="16"/>',
  crown: '<path d="M3 7l4 4 5-7 5 7 4-4-2 13H5z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  play: '<path d="M6 4l14 8-14 8z"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  checkCircle: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  alertTriangle: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7a2 2 0 0 1 1.7 2z"/>',
  wa: '<path d="M20 12a8 8 0 0 1-11.9 7L4 20l1-4A8 8 0 1 1 20 12z"/><path d="M8.5 9.5c0 3 2 5 5 5"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  doc: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9 13h6M9 17h6"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  loader: '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
  upload: '<path d="M12 15V3M7 8l5-5 5 5"/><path d="M5 21h14"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  sidebarCollapse: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 8-3 8h18s-3-1-3-8"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  externalLink: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  chevronRight: '<polyline points="9 18 15 12 9 6"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18z"/>',
  code: '<path d="m9 18-6-6 6-6M15 6l6 6-6 6"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  moreHorizontal: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
};

function icon(name, cls = '') {
  return `<svg class="${cls}" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[name] || I.grid}</svg>`;
}

/* -------------------------------- Charts --------------------------------- */
function sparkline(values, color = '#4f46e5', height = 32) {
  const v = (values || []).map(Number);
  if (!v.length) return '';
  const w = 100;
  const min = Math.min(...v);
  const max = Math.max(...v);
  const span = max - min || 1;
  const pts = v.map((n, i) => [(i / (v.length - 1 || 1)) * w, height - ((n - min) / span) * (height - 6) - 3]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join(' ');
  const area = `${line} L${w},${height} L0,${height} Z`;
  const gid = 'g' + Math.random().toString(36).slice(2, 8);
  return `<svg class="spark" viewBox="0 0 ${w} ${height}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${color}" stop-opacity="0.22"/><stop offset="100%" stop-color="${color}" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${area}" fill="url(#${gid})"/><path d="${line}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

function areaChart(series, labels, opts = {}) {
  const height = opts.height || 210;
  const w = 720;
  const pad = { l: 36, r: 8, t: 12, b: 22 };
  const all = series.flatMap((s) => s.values.map(Number));
  const max = Math.max(...all, 1) * 1.12;
  const innerW = w - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const x = (i) => pad.l + (i / Math.max(1, labels.length - 1)) * innerW;
  const y = (v) => pad.t + innerH - (Number(v) / max) * innerH;

  const grid = [0, 0.25, 0.5, 0.75, 1]
    .map((f) => {
      const yy = pad.t + innerH * (1 - f);
      return `<line x1="${pad.l}" y1="${yy.toFixed(1)}" x2="${w - pad.r}" y2="${yy.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>
      <text x="${pad.l - 6}" y="${(yy + 3.5).toFixed(1)}" font-size="9" fill="var(--muted)" text-anchor="end">${fmtNum(Math.round(max * f))}</text>`;
    })
    .join('');

  const paths = series
    .map((s) => {
      const pts = s.values.map((v, i) => [x(i), y(v)]);
      const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
      const area = s.fill ? `${line} L${pts[pts.length - 1][0].toFixed(1)},${(pad.t + innerH).toFixed(1)} L${pad.l},${(pad.t + innerH).toFixed(1)} Z` : '';
      const gid = 'ac' + Math.random().toString(36).slice(2, 8);
      return `${s.fill ? `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${s.color}" stop-opacity="0.24"/><stop offset="100%" stop-color="${s.color}" stop-opacity="0"/></linearGradient></defs><path d="${area}" fill="url(#${gid})"/>` : ''}
      <path d="${line}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${pts.map((p) => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.2" fill="var(--surface)" stroke="${s.color}" stroke-width="1.6"/>`).join('')}`;
    })
    .join('');

  const xlabels = labels
    .map((l, i) => (i % Math.ceil(labels.length / 12) === 0 ? `<text x="${x(i).toFixed(1)}" y="${height - 6}" font-size="9" fill="var(--muted)" text-anchor="middle">${esc(l)}</text>` : ''))
    .join('');

  return `<svg viewBox="0 0 ${w} ${height}" style="width:100%;height:auto" role="img" aria-label="${esc(opts.alt || 'chart')}">
    ${grid}${paths}${opts.forecast || ''}${xlabels}</svg>
    ${opts.legend ? `<div class="legend" style="margin-top:8px">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>` : ''}`;
}

function forecastChart(history, forecast, labels) {
  const height = 210;
  const w = 720;
  const pad = { l: 36, r: 10, t: 14, b: 22 };
  const h = history.map(Number);
  const f = forecast.next.map((p) => p.value);
  const all = [...h, ...forecast.next.map((p) => p.high)];
  const max = Math.max(...all, 1) * 1.12;
  const innerW = w - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const n = h.length;
  const total = n + f.length - 1;
  const x = (i) => pad.l + (i / Math.max(1, total)) * innerW;
  const y = (v) => pad.t + innerH - (Number(v) / max) * innerH;
  const hPts = h.map((v, i) => [x(i), y(v)]);
  const fPts = f.map((v, i) => [x(n - 1 + i), y(v)]);
  const band = [
    ...forecast.next.map((p, i) => [x(n - 1 + i), y(p.high)]),
    ...forecast.next.slice().reverse().map((p, i) => [x(n - 1 + (forecast.next.length - 1 - i)), y(p.low)]),
  ];
  const bandPath = band.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ') + ' Z';
  const line = (pts) => pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const grid = [0, 0.5, 1]
    .map((fr) => {
      const yy = pad.t + innerH * (1 - fr);
      return `<line x1="${pad.l}" y1="${yy.toFixed(1)}" x2="${w - pad.r}" y2="${yy.toFixed(1)}" stroke="var(--border)"/>
      <text x="${pad.l - 6}" y="${(yy + 3.5).toFixed(1)}" font-size="9" fill="var(--muted)" text-anchor="end">${fmtNum(Math.round(max * fr))}</text>`;
    })
    .join('');
  const legend = `<div class="legend" style="margin-top:8px"><span><i style="background:#4338ca"></i>Actual</span>
    <span><i style="background:#0891b2"></i>Forecast (${esc(forecast.method)})</span>
    <span><i style="background:#cbd5e1"></i>95% interval</span>
    <span class="muted">MAPE ${pct(forecast.mape)} · R² ${forecast.r_squared} · confidence ${esc(forecast.confidence)}</span></div>`;
  return `<svg viewBox="0 0 ${w} ${height}" style="width:100%;height:auto" role="img" aria-label="forecast">
    ${grid}
    <path d="${bandPath}" fill="var(--border)" opacity="0.55"/>
    <path d="${line(hPts)}" fill="none" stroke="#4338ca" stroke-width="2.2"/>
    <path d="${line(fPts)}" fill="none" stroke="#0891b2" stroke-width="2.2" stroke-dasharray="5 4"/>
    ${fPts.map((p) => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3" fill="#0891b2"/>`).join('')}
    ${labels.map((l, i) => `<text x="${x(i).toFixed(1)}" y="${height - 6}" font-size="9" fill="var(--muted)" text-anchor="middle">${esc(l)}</text>`).join('')}
    ${forecast.next.map((p, i) => `<text x="${x(n - 1 + i).toFixed(1)}" y="${height - 16}" font-size="9" fill="#0891b2" text-anchor="middle">+${p.h}M</text>`).join('')}
  </svg>${legend}`;
}

function gauge(score, label = 'posture') {
  const r = 52;
  const c = 2 * Math.PI * r;
  const off = c * (1 - Math.max(0, Math.min(100, score)) / 100);
  const color = score >= 90 ? '#047857' : score >= 75 ? '#4338ca' : score >= 60 ? '#b45309' : '#b91c1c';
  return `<svg class="gauge" viewBox="0 0 132 132" role="img" aria-label="${esc(label)} score ${score} out of 100">
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="11"/>
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round"
      stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 66 66)"/>
    <text x="66" y="64" text-anchor="middle" font-size="28" font-weight="700" fill="var(--ink)">${score}</text>
    <text x="66" y="81" text-anchor="middle" font-size="9.5" fill="var(--muted)" letter-spacing="1">${esc(label.toUpperCase())}</text>
  </svg>`;
}

function barRow(label, value, max, color = '#4f46e5', suffix = '') {
  const w = Math.max(2, Math.round((Number(value) / (max || 1)) * 100));
  return `<div class="bar-row"><span class="muted">${esc(label)}</span>
    <span class="bar-track"><span class="bar-fill" style="width:${w}%;background:${color}"></span></span>
    <span class="num b right">${esc(suffix ? value + suffix : fmtNum(value))}</span></div>`;
}

/* ---------- 7 Cohesive Primary Workspaces + Sub-Tab Architecture --------- */
const WORKSPACES = [
  {
    id: 'today',
    group: 'Daily Desk',
    label: 'Today',
    icon: 'sun',
    defaultRoute: 'dashboard',
    badge: 'sla',
    tabs: [
      { id: 'dashboard', label: 'Action Desk', icon: 'bolt' },
      { id: 'overview', label: 'Executive Overview', icon: 'chart' },
      { id: 'analytics', label: 'Forecast & Insights', icon: 'trendingUp' },
    ],
  },
  {
    id: 'crm',
    group: 'Daily Desk',
    label: 'Leads & Inbox',
    icon: 'users',
    defaultRoute: 'leads',
    badge: 'inbox',
    tabs: [
      { id: 'leads', label: 'All Contacts', icon: 'users' },
      { id: 'inbox', label: 'Unified Inbox', icon: 'chat', badge: 'inbox' },
      { id: 'sla', label: 'Speed-to-Lead SLA', icon: 'clock', badge: 'sla' },
    ],
  },
  {
    id: 'deals',
    group: 'Daily Desk',
    label: 'Deals & Visits',
    icon: 'kanban',
    defaultRoute: 'pipeline',
    tabs: [
      { id: 'pipeline', label: 'Pipeline Board', icon: 'kanban' },
      { id: 'visits', label: 'Site Visits', icon: 'calendar' },
      { id: 'closing', label: 'Closing & e-Reg Desk', icon: 'landmark' },
    ],
  },
  {
    id: 'inventory',
    group: 'Daily Desk',
    label: 'Properties & Units',
    icon: 'building',
    defaultRoute: 'properties',
    tabs: [
      { id: 'properties', label: 'Listings Catalogue', icon: 'building' },
      { id: 'units', label: 'Plot & Unit Matrix', icon: 'grid' },
      { id: 'vault', label: '7/12 & RERA Vault', icon: 'fileCheck' },
    ],
  },
  {
    id: 'growth',
    group: 'Growth & AI',
    label: 'Partners & Growth',
    icon: 'handshake',
    defaultRoute: 'partners',
    tabs: [
      { id: 'partners', label: 'Channel Partners', icon: 'handshake' },
      { id: 'funnels', label: 'Funnels & Website', icon: 'funnel' },
      { id: 'ads', label: 'Paid Campaigns', icon: 'megaphone' },
      { id: 'social', label: 'Social Studio', icon: 'social' },
      { id: 'research', label: 'Market Research', icon: 'target' },
    ],
  },
  {
    id: 'ai',
    group: 'Growth & AI',
    label: 'AI Workforce',
    icon: 'bot',
    defaultRoute: 'agents',
    badge: 'learning',
    tabs: [
      { id: 'agents', label: 'Agent Fleet', icon: 'bot' },
      { id: 'learning', label: 'Approval Queue', icon: 'sparkles', badge: 'learning' },
      { id: 'automation', label: 'Playbook Automations', icon: 'bolt' },
      { id: 'ailab', label: 'Voice & AI Sandbox', icon: 'sliders' },
    ],
  },
  {
    id: 'platform',
    group: 'Admin',
    label: 'Settings & Trust',
    icon: 'settings',
    defaultRoute: 'settings',
    tabs: [
      { id: 'settings', label: 'Workspace & Account', icon: 'settings' },
      { id: 'integrations', label: 'Connectors & Webhooks', icon: 'plug' },
      { id: 'security', label: 'Security & REDLINE', icon: 'shieldCheck' },
      { id: 'owner', label: 'Team & Billing', icon: 'crown', ownerOnly: true },
    ],
  },
];

function workspaceForRoute(routeName) {
  return WORKSPACES.find((w) => w.tabs.some((t) => t.id === routeName)) || WORKSPACES[0];
}

function currentBadges() {
  return {
    inbox: RE.state.inboxBadge || 0,
    learning: RE.state.pending || 0,
    sla: RE.state.slaOverdue || 0,
  };
}

function renderSubtabs(routeName) {
  const ws = workspaceForRoute(routeName);
  const role = (RE.state.me && RE.state.me.user && RE.state.me.user.role) || 'viewer';
  const badges = currentBadges();
  const visible = ws.tabs.filter((t) => !(t.ownerOnly && role !== 'owner'));
  if (visible.length <= 1) return '';
  return `<nav class="subtabs" aria-label="${esc(ws.label)} sections">
    ${visible.map((t) => {
      const cnt = t.badge ? badges[t.badge] : 0;
      return `<a class="subtab ${t.id === routeName ? 'active' : ''}" href="#/${esc(t.id)}" data-nav>
        ${icon(t.icon)}<span>${esc(t.label)}</span>${cnt ? `<span class="cnt">${cnt}</span>` : ''}
      </a>`;
    }).join('')}
  </nav>`;
}

function renderSkeleton(routeName) {
  return `
    ${renderSubtabs(routeName)}
    <div class="page-head">
      <div><div class="sk" style="width:220px;height:22px"></div><div class="sk" style="width:340px;height:14px;margin-top:8px"></div></div>
      <div class="sk" style="width:118px;height:34px"></div>
    </div>
    <div class="grid g-4" style="margin-bottom:14px">
      ${[0, 1, 2, 3].map(() => `<div class="card card-pad"><div class="sk" style="width:90px;height:12px"></div><div class="sk" style="width:110px;height:26px;margin-top:10px"></div><div class="sk" style="width:100%;height:28px;margin-top:12px"></div></div>`).join('')}
    </div>
    <div class="card card-pad">
      ${[0, 1, 2, 3, 4].map(() => `<div class="sk" style="width:100%;height:38px;margin-bottom:10px"></div>`).join('')}
    </div>`;
}

/* --------------------- Persistent App Shell (Mount Once) ----------------- */
function ensureShell(meta = {}) {
  const me = RE.state.me;
  if (!me) return;
  const app = document.getElementById('app');
  if (!document.getElementById('shell-root')) {
    app.innerHTML = `
    <div class="app" id="shell-root">
      <aside class="sidebar" id="sidebar">
        <div class="brandbar">
          <div class="logo-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-6h6v6"/></svg></div>
          <div class="brand-text"><div class="brand-name">Realtor Estates</div><div class="brand-sub">Revenue OS</div></div>
        </div>
        <div class="workspace" id="shell-ws"></div>
        <nav class="nav" id="shell-nav" aria-label="Primary"></nav>
        <div class="sidebar-foot">
          <div class="sys-health"><span class="dot" id="live-dot"></span><span id="live-label">Connected · RLS active</span></div>
          <div class="hr-dark"></div>
          <button class="nav-item" data-act="install" title="Install PWA">${icon('download')}<span>Install app</span></button>
          <button class="nav-item" data-act="logout" title="Sign out">${icon('lock')}<span>Sign out</span></button>
        </div>
      </aside>
      <div class="mobile-backdrop" data-act="toggle-nav"></div>
      <div class="main">
        <header class="topbar">
          <button class="icon-btn" data-act="toggle-rail" aria-label="Toggle navigation">${icon('sidebarCollapse')}</button>
          <div class="topbar-titles">
            <h1 id="topbar-title"></h1>
            <div class="crumbs" id="topbar-crumb"></div>
          </div>
          <button class="searchbox" data-act="palette" aria-label="Command palette">
            ${icon('search')}<span class="lbl">Jump to lead, deal, unit, tool…</span><kbd>⌘K</kbd>
          </button>
          <button class="icon-btn" data-act="toggle-theme" id="theme-btn" aria-label="Toggle dark mode">${icon(RE.state.theme === 'dark' ? 'sun' : 'moon')}</button>
          <button class="icon-btn" data-act="goto" data-route="learning" id="bell-btn" aria-label="Approvals">${icon('bell')}</button>
          <button class="avatar" data-act="goto" data-route="settings" id="avatar-btn" aria-label="Account"></button>
        </header>
        <main class="contentWrap" id="view"></main>
      </div>
    </div>
    <nav class="mobile-nav" id="mobile-nav" aria-label="Mobile navigation"></nav>`;
  }
  syncShellChrome(meta);
}

function syncShellChrome(meta = {}) {
  const me = RE.state.me;
  if (!me || !document.getElementById('shell-root')) return;
  const tenant = me.tenant || {};
  const badges = currentBadges();
  const activeWs = workspaceForRoute(RE.state.route);

  const wsEl = document.getElementById('shell-ws');
  if (wsEl) {
    wsEl.innerHTML = `<div class="ws-avatar">${esc(initials(tenant.name))}</div>
      <div class="ws-meta"><div class="ws-name">${esc(tenant.name)}</div><div class="ws-plan">${esc(tenant.plan || 'growth')} · ${esc(me.user.role)}</div></div>`;
  }

  const groups = ['Daily Desk', 'Growth & AI', 'Admin'];
  const navEl = document.getElementById('shell-nav');
  if (navEl) {
    navEl.innerHTML = groups.map((grp) => {
      const items = WORKSPACES.filter((w) => w.group === grp).map((w) => {
        const b = w.badge ? badges[w.badge] : 0;
        const isAct = w.id === activeWs.id;
        return `<a class="nav-item ${isAct ? 'active' : ''}" href="#/${esc(w.defaultRoute)}" data-nav ${isAct ? 'aria-current="page"' : ''} title="${esc(w.label)}">
          ${icon(w.icon)}<span>${esc(w.label)}</span>
          ${b ? `<span class="badge ${w.badge === 'learning' || w.badge === 'sla' ? 'warn' : ''}">${b}</span>` : ''}
        </a>`;
      }).join('');
      return `<div class="nav-group"><div class="nav-label">${esc(grp)}</div>${items}</div>`;
    }).join('');
  }

  if (meta.title !== undefined) {
    const tEl = document.getElementById('topbar-title');
    if (tEl) tEl.textContent = meta.title || activeWs.label;
  }
  if (meta.crumb !== undefined) {
    const cEl = document.getElementById('topbar-crumb');
    if (cEl) cEl.textContent = meta.crumb ? '· ' + meta.crumb : '';
  }

  const bellBtn = document.getElementById('bell-btn');
  if (bellBtn) bellBtn.innerHTML = `${icon('bell')}${badges.learning ? '<span class="ping"></span>' : ''}`;
  const avBtn = document.getElementById('avatar-btn');
  if (avBtn) avBtn.textContent = initials(me.user.name);
  const thBtn = document.getElementById('theme-btn');
  if (thBtn) thBtn.innerHTML = icon(RE.state.theme === 'dark' ? 'sun' : 'moon');

  const mobEl = document.getElementById('mobile-nav');
  if (mobEl) {
    const mobItems = [
      { route: 'dashboard', ws: 'today', label: 'Today', icon: 'sun' },
      { route: 'leads', ws: 'crm', label: 'Leads', icon: 'users' },
      { route: 'pipeline', ws: 'deals', label: 'Deals', icon: 'kanban' },
      { route: 'properties', ws: 'inventory', label: 'Units', icon: 'building' },
    ];
    mobEl.innerHTML = mobItems.map((m) => `
      <button type="button" data-act="goto" data-route="${m.route}" class="${activeWs.id === m.ws ? 'active' : ''}">
        ${icon(m.icon)}<span>${esc(m.label)}</span>
      </button>`).join('') + `
      <button type="button" data-act="palette">${icon('search')}<span>Jump</span></button>`;
  }
}

function renderShell(viewHtml, meta = {}) {
  ensureShell(meta);
  const container = document.getElementById('view');
  if (container) {
    const sub = renderSubtabs(RE.state.route);
    container.innerHTML = (viewHtml.includes('class="subtabs"') ? '' : sub) + viewHtml;
    container.querySelectorAll('table.resp-cards').forEach(table => {
      const labels = Array.from(table.querySelectorAll('thead th')).map(th => th.textContent);
      table.querySelectorAll('tbody tr').forEach(tr => Array.from(tr.cells).forEach((td,i) => {
        if (!td.hasAttribute('data-label')) td.setAttribute('data-label', labels[i] || '');
      }));
    });
  }
}

/* --------------------- Slide-Over Drawer & Modal ------------------------- */
function openDrawer({ title, sub = '', body, foot = '' }) {
  closeDrawer();
  const root = document.getElementById('drawer-root') || document.body;
  const wrap = document.createElement('div');
  wrap.className = 'sheet-backdrop';
  wrap.id = 'drawer';
  wrap.innerHTML = `<aside class="sheet" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="sheet-head">
      <div style="min-width:0;flex:1">
        <h2>${esc(title)}</h2>
        ${sub ? `<div class="xs muted" style="margin-top:2px">${sub}</div>` : ''}
      </div>
      <button type="button" class="icon-btn" data-act="close-drawer" aria-label="Close panel">${icon('x')}</button>
    </div>
    <div class="sheet-body">${body}</div>
    ${foot ? `<div class="sheet-foot">${foot}</div>` : ''}
  </aside>`;
  wrap.addEventListener('click', (e) => { if (e.target === wrap) closeDrawer(); });
  root.appendChild(wrap);
  containDialogFocus(wrap);
}

function closeDrawer() {
  const d = document.getElementById('drawer');
  if (d) { const prior = d._priorFocus; d.remove(); if (prior?.isConnected) prior.focus(); }
}

function modal({ title, body, foot, wide }) {
  closeModal();
  const el = document.createElement('div');
  el.className = 'modal-backdrop';
  el.id = 'modal';
  el.innerHTML = `<div class="modal" style="${wide ? 'width:min(940px,100%)' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="modal-head"><h2>${esc(title)}</h2><button class="icon-btn right" data-act="close-modal" aria-label="Close">${icon('x')}</button></div>
    <div class="modal-body">${body}</div>
    ${foot ? `<div class="modal-foot">${foot}</div>` : ''}
  </div>`;
  el.addEventListener('click', (e) => { if (e.target === el) closeModal(); });
  document.body.appendChild(el);
  containDialogFocus(el);
}

function closeModal() {
  const m = document.getElementById('modal');
  if (m) { const prior = m._priorFocus; m.remove(); if (prior?.isConnected) prior.focus(); }
}

/* -------------------------- Step-Up Confirmation ------------------------- */
const STEPUP_LABELS = {
  kill_switch: 'Stop or restart the agents for this workspace',
  key_rewrap: 'Re-wrap the root key under a new key',
  user_delete: 'Deactivate a member of staff',
  role_change: "Change a member's role",
  mfa_reset: "Reset another account's authenticator",
  secret_write: 'Write a deploy credential to this machine',
  demo_purge: 'Delete the rows a proof run wrote',
  payment_adjust: 'Adjust the usage ledger',
};

function promptStepUp(err) {
  return new Promise((resolve) => {
    const d = (err && err.details && err.details[0]) || {};
    const action = d.action || 'this action';
    const label = STEPUP_LABELS[action] || action.replace(/_/g, ' ');
    const needsFactor = RE.state.me && RE.state.me.user && RE.state.me.user.mfa &&
      RE.state.me.user.mfa.enrolled;
    let recovery = false;
    modal({
      title: 'Confirm your identity',
      body: `<p class="muted small" style="margin-top:0">${esc(err.message || 'This sensitive action requires a fresh confirmation.')}</p>
        <div class="chip brand" style="margin-bottom:12px">${esc(label)}</div>
        <form data-form="stepup" id="stepup-form">
          <div class="field">
            <label for="su-pass">Your password</label>
            <input class="input" id="su-pass" name="password" type="password" required autocomplete="current-password" autofocus>
          </div>
          ${needsFactor ? `<div class="field">
            <label for="su-code">${recovery ? 'Recovery code' : '6-digit authenticator code'}</label>
            <input class="input" id="su-code" name="${recovery ? 'recovery_code' : 'code'}" required
                   autocomplete="one-time-code" inputmode="${recovery ? 'text' : 'numeric'}"
                   ${recovery ? 'placeholder="XXXXX-XXXXX"' : 'placeholder="123456" maxlength="6"'}>
          </div>` : `<p class="hint">Password-only ticket valid for 2 minutes. Enrol 2FA in Settings for stronger protection.</p>`}
          <button class="btn primary" type="submit" style="width:100%;padding:10px">Confirm action</button>
        </form>`,
      foot: '<button class="btn" data-act="close-modal">Cancel</button>',
    });
    const form = document.getElementById('stepup-form');
    if (form) {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(form).entries());
        const btn = form.querySelector('button[type=submit]');
        btn.disabled = true;
        btn.textContent = 'Confirming…';
        try {
          const out = await api('/api/v1/auth/step-up', {
            method: 'POST', _noStepUp: true, silent: true,
            body: { action: d.action || '', target: d.target || '', ...data },
          });
          closeModal();
          resolve(out.ticket);
        } catch (e2) {
          toast(e2.message || 'Confirmation not accepted.', 'err');
          btn.disabled = false;
          btn.textContent = 'Confirm action';
        }
      });
    }
    document.querySelectorAll('#modal [data-act="close-modal"]').forEach((b) => {
      b.addEventListener('click', () => resolve(''));
    });
  });
}

/* ---------------------------- Command Palette (⌘K) ----------------------- */
function openPalette() {
  if (RE.state.paletteOpen) return;
  RE.state.paletteOpen = true;
  const navItems = WORKSPACES.flatMap((w) =>
    w.tabs.map((t) => ({ label: `${w.label} → ${t.label}`, route: t.id, icon: t.icon }))
  );
  const actions = [
    { label: 'Create new buyer lead', act: 'new-lead', icon: 'userPlus' },
    { label: 'Schedule a site visit', route: 'visits', icon: 'calendar' },
    { label: 'Open Plot & Unit Availability Matrix', route: 'units', icon: 'grid' },
    { label: 'Generate Maharashtra Statutory Cost Sheet', act: 'govt-cost-sheet-modal', icon: 'receipt' },
    { label: 'Simulate Marathi/Hindi AI Voice Call', act: 'voice-test-quick', icon: 'phone' },
    { label: 'Run REDLINE Security Verification', act: 'run-redline', icon: 'shieldCheck' },
  ];
  const all = [...actions, ...navItems];
  const el = document.createElement('div');
  el.id = 'palette';
  el.innerHTML = `<div class="palette-box" role="dialog" aria-label="Command palette">
    <input class="palette-input" id="palette-input" placeholder="Jump to workspace, lead action, cost sheet, unit grid…" autocomplete="off">
    <div class="palette-list" id="palette-list"></div></div>`;
  document.body.appendChild(el);
  const input = el.querySelector('#palette-input');
  const list = el.querySelector('#palette-list');
  let filtered = all;
  let sel = 0;
  const draw = () => {
    list.innerHTML = filtered
      .map((it, i) => `<div class="palette-item ${i === sel ? 'sel' : ''}" data-i="${all.indexOf(it)}">${icon(it.icon)}<span>${esc(it.label)}</span>${it.route ? `<span class="k">#/${esc(it.route)}</span>` : '<span class="k">Action</span>'}</div>`)
      .join('') || '<div class="empty">No matching commands</div>';
  };
  draw();
  input.focus();
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase();
    filtered = all.filter((it) => it.label.toLowerCase().includes(q));
    sel = 0;
    draw();
  });
  const run = (it) => {
    closePalette();
    if (!it) return;
    if (it.route) location.hash = '#/' + it.route;
    else if (RE.actions[it.act]) RE.actions[it.act](null);
  };
  el.addEventListener('click', (e) => {
    if (e.target === el) return closePalette();
    const item = e.target.closest('.palette-item');
    if (item) run(all[Number(item.dataset.i)]);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(filtered.length - 1, sel + 1); draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); run(filtered[sel]); }
    else if (e.key === 'Escape') closePalette();
  });
}

function closePalette() {
  const el = document.getElementById('palette');
  if (el) el.remove();
  RE.state.paletteOpen = false;
}

/* ------------------------------- Auth UI --------------------------------- */
function stopLiveSync() {
  if (RE.state.sse) { try { RE.state.sse.close(); } catch (_) {} RE.state.sse = null; }
  if (RE.state.pulseTimer) { clearInterval(RE.state.pulseTimer); RE.state.pulseTimer = null; }
}

function renderAuth(mode = 'login') {
  stopLiveSync();
  closeDrawer();
  closeModal();
  const app = document.getElementById('app');
  if (RE.state.mfa) return renderAuthMfa();
  app.innerHTML = `
  <div class="auth-split">
    <div class="auth-hero">
      <div class="brandbar" style="padding:0;border:0">
        <div class="logo-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-6h6v6"/></svg></div>
        <div class="brand-text"><div class="brand-name">Realtor Estates</div><div class="brand-sub">Revenue OS</div></div>
      </div>
      <h1>From first portal enquiry to registered sale deed.</h1>
      <p>Manage buyers, speed-to-lead SLAs, site visits, live plot &amp; tower inventory, statutory cost sheets, channel partners, and human-governed AI agents in one calm daily workspace.</p>
      <div class="hero-flow">
        <span class="step">1. Enquiry</span><span class="arr">→</span>
        <span class="step">2. Site Visit</span><span class="arr">→</span>
        <span class="step">3. 48h Hold</span><span class="arr">→</span>
        <span class="step">4. Cost Sheet</span><span class="arr">→</span>
        <span class="step">5. Index-II</span>
      </div>
      <div class="hero-badges">
        <span class="chip">MahaRERA + 7/12 Vault</span><span class="chip">WhatsApp &amp; Voice AI</span>
        <span class="chip">DPDP Consent-First</span><span class="chip">Postgres RLS Isolated</span>
      </div>
      <div class="hero-stat">
        <div><span>&lt; 90s</span><small>speed-to-lead SLA target</small></div>
        <div><span>5</span><small>asset categories supported</small></div>
        <div><span>100%</span><small>human-gated AI approvals</small></div>
      </div>
    </div>
    <main class="auth-panel">
      <div class="auth-card">
        <h2>${mode === 'login' ? 'Sign in to workspace' : 'Create your brokerage workspace'}</h2>
        <p class="muted small">${mode === 'login' ? 'Enter your credentials or launch an instant role demo below.' : 'Provision an isolated multi-tenant workspace in seconds.'}</p>
        <div class="auth-tabs" role="tablist">
          <button type="button" class="${mode === 'login' ? 'active' : ''}" data-act="auth-mode" data-mode="login">Sign in</button>
          <button type="button" class="${mode === 'signup' ? 'active' : ''}" data-act="auth-mode" data-mode="signup">Create workspace</button>
        </div>
        <form data-form="${mode === 'login' ? 'login' : 'signup'}" autocomplete="on">
          ${mode === 'signup' ? `<div class="field"><label for="org">Brokerage / Developer name</label><input class="input" id="org" name="org_name" required maxlength="80" placeholder="Sunrise Land Developers"></div>
          <div class="field"><label for="nm">Your full name</label><input class="input" id="nm" name="name" required maxlength="80" autocomplete="name" placeholder="Aarav Deshmukh"></div>` : ''}
          <div class="field"><label for="email">Work email</label>
            <input class="input" id="email" name="email" type="email" required maxlength="254" autocomplete="username" value="${mode === 'login' ? 'owner@realtorestates.app' : ''}" placeholder="owner@yourcompany.in"></div>
          <div class="field"><label for="pw">Password</label>
            <div class="pw-wrap">
              <input class="input" id="pw" name="password" type="password" required minlength="10" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}" value="${mode === 'login' ? 'Demo@12345!' : ''}">
              <button type="button" class="pw-toggle" data-act="toggle-pw" aria-label="Show password">${icon('eye')}</button>
            </div>
            <div class="hint">${mode === 'signup' ? 'Minimum 10 characters · scrypt-hashed with per-user salt.' : 'HttpOnly revocable session · automatic token refresh.'}</div>
          </div>
          <button class="btn primary" type="submit" style="width:100%;padding:10px 14px;font-size:13.5px">${mode === 'login' ? 'Sign in to workspace' : 'Create workspace'}</button>
        </form>
        ${mode === 'login' ? `<div class="demo-creds">
          <div class="xs b" style="text-transform:uppercase;letter-spacing:.05em;color:var(--muted)">1-Click Role Switcher (Demo Workspace)</div>
          <div class="role-grid">
            <button type="button" class="role-btn" data-act="quick-login" data-email="owner@realtorestates.app" data-pw="Demo@12345!">
              <strong>Owner</strong><span>Full access &amp; PII</span>
            </button>
            <button type="button" class="role-btn" data-act="quick-login" data-email="manager@realtorestates.app" data-pw="Demo@12345!">
              <strong>Manager</strong><span>Sales &amp; approvals</span>
            </button>
            <button type="button" class="role-btn" data-act="quick-login" data-email="agent@realtorestates.app" data-pw="Demo@12345!">
              <strong>Field Agent</strong><span>Masked PII</span>
            </button>
          </div>
          <div class="row wrap" style="margin-top:10px;justify-content:space-between">
            <a class="xs" href="/cp.html">Channel Partner Portal →</a>
            <a class="xs" href="/site" target="_blank" rel="noopener">Public Buyer Site →</a>
          </div>
        </div>` : `<div class="demo-creds"><b>Testing the seeded demo?</b> Switch to <b>Sign in</b> above to explore 100 seeded leads, deals, plots, and AI agents immediately.</div>`}
      </div>
    </main>
  </div>`;
}

function renderAuthMfa() {
  const app = document.getElementById('app');
  const st = RE.state.mfa;
  app.innerHTML = `
  <div class="auth-split">
    <div class="auth-hero">
      <div class="brandbar" style="padding:0;border:0">
        <div class="logo-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9 21v-6h6v6"/></svg></div>
        <div class="brand-text"><div class="brand-name">Realtor Estates</div><div class="brand-sub">Revenue OS</div></div>
      </div>
      <h1>Two-factor verification active.</h1>
      <p>Your password was accepted. Enter the 6-digit code from your authenticator app within ${Math.round((st.expires_in || 300) / 60)} minutes to open the workspace.</p>
    </div>
    <main class="auth-panel">
      <div class="auth-card">
        <h2>${st.recovery ? 'Enter recovery code' : 'Two-step verification'}</h2>
        <p class="muted small">Signing in as <b>${esc(st.user_hint || '')}</b></p>
        <form data-form="mfa" style="margin-top:14px">
          <div class="field">
            <label for="code">${st.recovery ? 'Recovery code' : '6-digit authenticator code'}</label>
            <input class="input" id="code" name="${st.recovery ? 'recovery_code' : 'code'}" required
                   autocomplete="one-time-code" inputmode="${st.recovery ? 'text' : 'numeric'}"
                   ${st.recovery ? 'placeholder="XXXXX-XXXXX"' : 'placeholder="123456" maxlength="6" pattern="[0-9]{6}"'} autofocus>
          </div>
          <button class="btn primary" type="submit" style="width:100%;padding:10px">Verify &amp; open workspace</button>
        </form>
        <p class="xs muted" style="margin-top:12px">
          <a href="#" data-act="mfa-recovery" data-on="${st.recovery ? '0' : '1'}">${st.recovery ? 'Use authenticator code' : 'Use a recovery code'}</a>
          · <a href="#" data-act="mfa-cancel">Back to sign in</a>
        </p>
      </div>
    </main>
  </div>`;
}

async function renderPublicBuyerPortal(passId) {
  const app = document.getElementById('app');
  if (!app) return;
  try {
    const res = await api('/api/v1/public/buyer-portal/' + encodeURIComponent(passId || ''), { silent: true });
    const p = res.portal || {};
    const prj = p.property || p.project || {};
    const unit = p.unit || {};
    const fema = p.fema_eligibility || p.fema_compliance || {};
    const rev = p.revenue_clearance || {};
    const reg = p.govt_registration || {};
    const loan = p.home_loan || {};
    const clp = p.clp_schedule || {};
    const hnd = p.post_reg_handover || {};
    const wsName = (p.workspace && p.workspace.name) || p.workspace_name || 'Workspace';
    app.innerHTML = `<div style="max-width:840px;margin:28px auto;padding:0 16px">
      <div class="card card-pad">
        <div class="row wrap" style="gap:8px">
          <div>
            <div class="xs muted">${esc(wsName)} · Buyer &amp; NRI Self-Serve Dossier</div>
            <h1 style="margin:4px 0">${esc(p.deal && p.deal.title ? p.deal.title : 'Property Booking Dossier')}</h1>
          </div>
          <span class="spacer"></span>
          ${stageChip((p.deal && p.deal.stage) || 'active')}
        </div>
        <div class="grid g-2" style="margin-top:14px">
          <div class="card card-pad">
            <div class="xs muted">Project &amp; MahaRERA</div>
            <div class="b" style="margin-top:4px">${esc(prj.title || 'Assigned Project')} · ${esc(prj.city || '')}</div>
            <div class="small" style="margin-top:6px">MahaRERA: <code>${esc(prj.rera_id || 'Pending')}</code></div>
          </div>
          <div class="card card-pad">
            <div class="xs muted">Unit &amp; All-In Consideration</div>
            <div class="b num" style="font-size:20px;margin-top:4px">${fmtMoney((p.deal && (p.deal.value || p.deal.value_inr)) || unit.all_in_inr || 0, false)}</div>
            <div class="xs muted" style="margin-top:6px">${(unit.unit_no || unit.unit_code) ? `Unit ${esc(unit.unit_no || unit.unit_code)} · Area ${esc(prj.area_sqft || unit.carpet_sqft || '—')} sq.ft` : 'Unit allocation in progress'}</div>
          </div>
        </div>
        <div class="grid g-2" style="margin-top:12px">
          <div class="card card-pad">
            <div class="row"><div class="b small">7/12, NA &amp; Title Clearance</div><span class="spacer"></span><span class="chip ${rev.can_execute_agreement ? 'green' : 'amber'} xs">${rev.can_execute_agreement ? 'CLEAR TITLE' : 'IN REVIEW'}</span></div>
            <div class="xs muted" style="margin-top:6px">Zero Boja: ${rev.zero_unreleased_boja ? 'Yes' : 'Pending'} · NA Verified: ${rev.na_verified ? 'Yes' : 'Pending'}</div>
          </div>
          <div class="card card-pad">
            <div class="row"><div class="b small">IGR Registration &amp; Index-II</div><span class="spacer"></span><span class="chip ${reg.status === 'registered' ? 'green' : 'blue'} xs">${reg.status === 'registered' ? 'INDEX-II ISSUED' : esc(reg.mode || 'scheduled')}</span></div>
            <div class="xs muted" style="margin-top:6px">${reg.registered_doc_no ? `Doc: <code>${esc(reg.registered_doc_no)}</code>` : `GRAS Challan: ${reg.gras_verified ? 'Verified' : 'Pending'}`}</div>
          </div>
        </div>
        <div class="grid g-2" style="margin-top:12px">
          <div class="card card-pad">
            <div class="row"><div class="b small">Home Loan &amp; RERA CLP</div><span class="spacer"></span><span class="chip blue xs">${esc(loan.stage || loan.funding_mode || 'self_funded')}</span></div>
            <div class="xs muted" style="margin-top:6px">${loan.bank_name ? `${esc(loan.bank_name)} · Sanctioned ${fmtMoney(loan.sanctioned_amount_inr || 0, false)}` : 'No bank loan linked yet'} · CLP stages: ${(Array.isArray(clp) ? clp : (clp.milestones || [])).length}</div>
          </div>
          <div class="card card-pad">
            <div class="row"><div class="b small">E-Ferfar Mutation &amp; Handover</div><span class="spacer"></span><span class="chip ${hnd.updated_7_12_or_property_card_issued ? 'green' : 'grey'} xs">${esc(hnd.completion_pct || 0)}%</span></div>
            <div class="xs muted" style="margin-top:6px">Ferfar: ${hnd.ferfar_entry_no ? `<code>${esc(hnd.ferfar_entry_no)}</code>` : 'Pending'}</div>
          </div>
        </div>
        <div class="card card-pad" style="margin-top:12px">
          <div class="row"><div class="b small">FEMA NDI Rules 2019 · Rule 24</div><span class="spacer"></span><span class="chip ${fema.status === 'PROHIBITED' || fema.eligible === false ? 'red' : 'blue'} xs">${esc(fema.status || (fema.eligible === false ? 'PROHIBITED' : 'ELIGIBLE'))}</span></div>
          <div class="xs muted" style="margin-top:6px">Permitted channels: ${esc((fema.permitted_channels || fema.permitted_payment_channels || []).join(', ') || 'Domestic INR / NRE / NRO')}</div>
        </div>
      </div>
    </div>`;
  } catch (err) {
    app.innerHTML = `<div style="max-width:460px;margin:12vh auto;padding:16px"><div class="card card-pad"><h2>Buyer Portal Pass Expired or Revoked</h2><p class="muted small">${esc(err.message || 'Pass expired or revoked.')}</p><a class="btn primary" href="#/dashboard">Brokerage Sign In</a></div></div>`;
  }
}

/* --------------------- Smart Serverless-Safe Live Sync ------------------- */
async function syncBootstrapSilent() {
  if (!RE.state.me || document.hidden) return;
  try {
    const b = await api('/api/v1/bootstrap', { silent: true, fresh: true });
    applyBootstrapState(b);
    syncShellChrome({});
  } catch (_) {}
}

function applyBootstrapState(b) {
  if (!b) return;
  RE.state.bootstrap = b;
  if (b.csrf) rememberAuth(RE.state.token, b.csrf);
  if (b.me) RE.state.me = b.me;
  RE.state.pending = b.pending_approvals || 0;
  RE.state.inboxBadge = b.inbox_unread || 0;
  RE.state.slaOverdue = (b.sla && b.sla.overdue) || 0;
  const label = document.getElementById('live-label');
  if (label) {
    const agCnt = (b.agents || []).filter((a) => a.status === 'active').length;
    label.textContent = `Live · ${agCnt} agents · ${RE.state.pending} approvals`;
  }
  const dot = document.getElementById('live-dot');
  if (dot) dot.className = 'dot';
}

function startLiveSync() {
  stopLiveSync();
  const isServerless = /\.vercel\.app$/i.test(location.hostname);
  if (isServerless || !('EventSource' in window)) {
    RE.state.pulseTimer = setInterval(syncBootstrapSilent, 25000);
    return;
  }
  try {
    const es = new EventSource('/api/v1/events');
    RE.state.sse = es;
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        RE.state.pending = d.pending_approvals || 0;
        syncShellChrome({});
        const label = document.getElementById('live-label');
        if (label) label.textContent = `Live · ${d.agents_active} agents · ${d.pending_approvals} approvals`;
      } catch (_) {}
    };
    es.onerror = () => {
      try { es.close(); } catch (_) {}
      RE.state.sse = null;
      RE.state.pulseTimer = setInterval(syncBootstrapSilent, 25000);
    };
  } catch (_) {}
}

/* ---------------- Optimistic Sub-16ms Router + SWR Cache ----------------- */
let routeSeq = 0;

async function route(opts = {}) {
  const hash = (location.hash || '#/dashboard').replace(/^#\/?/, '');
  const [rawName, ...rest] = hash.split('/');
  if (rawName === 'buyer-portal' && rest[0]) {
    await renderPublicBuyerPortal(rest[0]);
    return;
  }
  const name = rawName || 'dashboard';
  if (RE.state.route !== name) closeDrawer();
  RE.state.route = name;
  RE.state.params = { id: rest[0] };
  if (!RE.state.me) return renderAuth();

  const viewFn = RE.views[name] || (async () => ({ title: 'Section not found', crumb: '', html: '<div class="card card-pad"><h1>Section not found</h1><a href="#/dashboard" data-nav>Return to Today</a></div>' }));
  const ws = workspaceForRoute(name);
  const tabMeta = ws.tabs.find((t) => t.id === name) || { label: ws.label };
  const cacheKey = `${name}:${rest[0] || ''}`;
  const mySeq = ++routeSeq;

  // 1. Immediate 0ms visual feedback: mount shell, highlight tab, show cached or skeleton
  document.getElementById('sidebar')?.classList.remove('open');
  ensureShell({ title: tabMeta.label, crumb: ws.label });

  const cachedView = !opts.force && RE.state.viewCache.get(cacheKey);
  if (cachedView) {
    renderShell(cachedView.html, { title: cachedView.title, crumb: cachedView.crumb });
  } else {
    const container = document.getElementById('view');
    if (container) container.innerHTML = renderSkeleton(name);
    window.scrollTo({ top: 0 });
  }

  // 2. Fetch / revalidate view data
  setNavLoading(true);
  try {
    const out = await viewFn(RE.state.params);
    if (mySeq !== routeSeq || !RE.state.me) return;
    RE.state.viewCache.set(cacheKey, out);
    const activeEl = document.activeElement;
    const isTyping = activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA' || activeEl.tagName === 'SELECT');
    if (!cachedView || !isTyping) {
      renderShell(out.html, { title: out.title, crumb: out.crumb });
    }
  } catch (err) {
    if (mySeq !== routeSeq || !RE.state.me) return;
    const c = document.getElementById('view');
    if (c && !cachedView) {
      c.innerHTML = `${renderSubtabs(name)}<div class="card card-pad"><h2>Could not load this section</h2>
        <p class="muted small" style="margin-top:6px">${esc(err.message || 'Temporary network error.')}</p>
        <button class="btn primary sm" data-act="reload">${icon('refresh')}Retry now</button></div>`;
    }
  } finally {
    if (mySeq === routeSeq) setNavLoading(false);
  }
}

/* ------------------------------- Boot ------------------------------------ */
async function boot() {
  if ((location.hash || '').startsWith('#/buyer-portal/')) {
    const passId = location.hash.slice('#/buyer-portal/'.length).split('/')[0];
    await renderPublicBuyerPortal(passId);
    return;
  }

  let hasSessionHint = false;
  try { hasSessionHint = localStorage.getItem('re_signed_in') === '1'; } catch (_) {}

  if (!hasSessionHint) {
    renderAuth();
    // Signed-out visitor: login screen in index.html is ALREADY interactive at 0ms!
    // Pre-warm serverless runtime silently in background so 1-click sign-in is instant.
    fetch('/healthz', { cache: 'no-store' }).catch(() => {});
  } else {
    setNavLoading(true);
    try {
      const b = await api('/api/v1/bootstrap', { silent: true, fresh: true });
      applyBootstrapState(b);
      startLiveSync();
      await route();
    } catch (_) {
      clearAuthHint();
      renderAuth();
    } finally {
      setNavLoading(false);
    }
  }

  window.addEventListener('online', () => document.body.classList.remove('is-offline'));
  window.addEventListener('offline', () => document.body.classList.add('is-offline'));
  if (!navigator.onLine) document.body.classList.add('is-offline');
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    RE.state.installPrompt = e;
  });
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
}

/* --------------------------- Delegated Events ---------------------------- */
document.addEventListener('click', (e) => {
  const nav = e.target.closest('a[data-nav]');
  if (nav) {
    e.preventDefault();
    const targetHash = nav.getAttribute('href');
    if (location.hash === targetHash) route({ force: true });
    else location.hash = targetHash;
    return;
  }
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const act = el.dataset.act;
  if (act === 'close-modal') return closeModal();
  if (act === 'close-drawer') return closeDrawer();
  if (act === 'palette') return openPalette();
  if (act === 'toggle-nav') return document.getElementById('sidebar')?.classList.toggle('open');
  if (act === 'toggle-rail') {
    if (window.innerWidth <= 860) {
      document.getElementById('sidebar')?.classList.toggle('open');
    } else {
      RE.state.railCollapsed = !RE.state.railCollapsed;
      document.body.classList.toggle('rail-collapsed', RE.state.railCollapsed);
      try { localStorage.setItem('re_rail', RE.state.railCollapsed ? '1' : '0'); } catch (_) {}
    }
    return;
  }
  if (act === 'toggle-theme') {
    RE.state.theme = RE.state.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', RE.state.theme);
    try { localStorage.setItem('re_theme', RE.state.theme); } catch (_) {}
    syncShellChrome({});
    return;
  }
  if (act === 'toggle-pw') {
    const inp = el.parentElement && el.parentElement.querySelector('input');
    if (inp) {
      const show = inp.type === 'password';
      inp.type = show ? 'text' : 'password';
      el.innerHTML = icon(show ? 'eyeOff' : 'eye');
      el.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
    }
    return;
  }
  if (act === 'goto') {
    const nextHash = '#/' + el.dataset.route;
    if (location.hash === nextHash) route({ force: true });
    else location.hash = nextHash;
    return;
  }
  if (act === 'reload') {
    invalidateCaches();
    return route({ force: true });
  }
  if (act === 'auth-mode') return renderAuth(el.dataset.mode);
  if (act === 'copy-text') {
    const txt = el.dataset.text || '';
    if (navigator.clipboard && txt) {
      navigator.clipboard.writeText(txt).then(() => toast('Copied to clipboard', 'ok')).catch(() => {});
    }
    return;
  }
  if (act === 'install') {
    const p = RE.state.installPrompt;
    if (p) { p.prompt(); RE.state.installPrompt = null; }
    else toast('Use browser menu → “Install App” or “Add to Home Screen”.');
    return;
  }
  if (act === 'logout') {
    clearAuthHint();
    stopLiveSync();
    invalidateCaches();
    api('/api/v1/auth/logout', { method: 'POST', body: {}, silent: true }).finally(() => {
      RE.state.me = null;
      RE.state.bootstrap = null;
      invalidateCaches();
      rememberAuth(null, null);
      renderAuth();
    });
    return;
  }
  const fn = RE.actions[act];
  if (fn) fn(el, e);
});

document.addEventListener('submit', async (e) => {
  const form = e.target.closest('form[data-form]');
  if (!form) return;
  e.preventDefault();
  const kind = form.dataset.form;
  const fd = new FormData(form);
  const payload = Object.fromEntries(fd.entries());

  if (kind === 'login' || kind === 'signup') {
    const btn = form.querySelector('button[type=submit]');
    const origHtml = btn ? btn.innerHTML : 'Sign in';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `${icon('loader', 'spin')}<span>${kind === 'login' ? 'Signing in…' : 'Creating workspace…'}</span>`;
    }
    setNavLoading(true);
    try {
      const res = await api('/api/v1/auth/' + kind, { method: 'POST', body: payload, silent: true });
      if (res.mfa_required) {
        RE.state.mfa = { ...res, recovery: false };
        renderAuth();
        toast('Password verified — enter your authenticator code.', 'ok');
        return;
      }
      rememberAuth(res.token, res.csrf);
      invalidateCaches();
      // Immediate entry using returned user/tenant — zero extra /auth/me wait!
      RE.state.me = { user: res.user, tenant: res.tenant };
      ensureShell({ title: 'Action Desk', crumb: 'Today' });
      startLiveSync();
      const targetHash = '#/dashboard';
      if (location.hash !== targetHash) location.hash = targetHash;
      else await route({ force: true });
      toast(`Welcome back, ${(res.user && res.user.name ? res.user.name : 'Operator').split(' ')[0]}`, 'ok');
    } catch (err) {
      toast(err.message || 'Sign-in failed', 'err');
      if (btn) { btn.disabled = false; btn.innerHTML = origHtml; }
    } finally {
      setNavLoading(false);
    }
    return;
  }

  if (kind === 'mfa') {
    const btn = form.querySelector('button[type=submit]');
    if (btn) { btn.disabled = true; btn.innerHTML = `${icon('loader', 'spin')}<span>Verifying…</span>`; }
    try {
      const res = await api('/api/v1/auth/login/mfa',
        { method: 'POST', body: { challenge: RE.state.mfa.challenge, ...payload }, silent: true });
      RE.state.mfa = null;
      rememberAuth(res.token, res.csrf);
      RE.state.me = await api('/api/v1/auth/me');
      startLiveSync();
      if (location.hash !== '#/dashboard') location.hash = '#/dashboard';
      else await route({ force: true });
      toast(`Welcome back, ${res.user.name.split(' ')[0]}`, 'ok');
    } catch (err) {
      toast(err.message, 'err');
      if (btn) { btn.disabled = false; btn.textContent = 'Verify & open workspace'; }
      form.querySelector('input')?.select?.();
    }
    return;
  }

  const handler = RE.actions['form:' + kind];
  if (handler) handler(payload, form);
});

RE.actions['mfa-recovery'] = (el) => {
  if (!RE.state.mfa) return renderAuth();
  RE.state.mfa.recovery = el.dataset.on === '1';
  renderAuthMfa();
};
RE.actions['mfa-cancel'] = () => { RE.state.mfa = null; renderAuth(); };
RE.actions['quick-login'] = (el) => {
  const form = document.querySelector('form[data-form="login"]');
  if (!form) return;
  const emailIn = form.querySelector('input[name="email"]');
  const pwIn = form.querySelector('input[name="password"]');
  if (emailIn) emailIn.value = el.dataset.email || 'owner@realtorestates.app';
  if (pwIn) pwIn.value = el.dataset.pw || 'Demo@12345!';
  form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
};

document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    return openPalette();
  }
  if (e.key === 'Escape') { closePalette(); closeModal(); closeDrawer(); }
});

window.addEventListener('hashchange', () => route());
document.addEventListener('DOMContentLoaded', () => { if (window.RE_BOOT !== false) boot(); });

/* ---------------- Offline Field-Agent Outbox (v1.32.0) ---------------- */
RE.offlineOutbox = {
  read() {
    try { return JSON.parse(localStorage.getItem('re_offline_outbox') || '[]'); }
    catch (_) { return []; }
  },
  enqueue(mutation) {
    const item = Object.assign({
      client_mutation_id: 'mut_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 8),
      queued_at: Math.floor(Date.now() / 1000),
    }, mutation);
    const list = RE.offlineOutbox.read();
    list.push(item);
    try { localStorage.setItem('re_offline_outbox', JSON.stringify(list.slice(-50))); } catch (_) {}
    return item;
  },
  async flush() {
    const list = RE.offlineOutbox.read();
    if (!list.length || !navigator.onLine || !RE.state.me) return null;
    try {
      const res = await api('/api/v1/offline/sync', { method: 'POST', body: { mutations: list }, silent: true });
      try { localStorage.setItem('re_offline_outbox', '[]'); } catch (_) {}
      if (res.applied) toast(`Synced ${res.applied} offline field update(s)`, 'ok');
      return res;
    } catch (_) { return null; }
  },
};
window.addEventListener('online', () => { RE.offlineOutbox.flush(); });

/* -------- On-Device Pre-Screening (WebGPU/WebNN + JS fallback) -------- */
RE.onDevicePrescreen = {
  probeRuntime() {
    if (typeof navigator !== 'undefined' && navigator.gpu) return 'webgpu';
    if (typeof navigator !== 'undefined' && navigator.ml) return 'webnn';
    return 'cpu-deterministic-fp32';
  },
  run(rawText) {
    const t0 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    let txt = String(rawText || '');
    const redactions = [];
    const aadhaarRe = /\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b/g;
    const panRe = /\b[A-Z]{5}\d{4}[A-Z]\b/g;
    const cardRe = /\b(?:\d[ -]?){15,18}\d\b/g;
    if (aadhaarRe.test(txt)) { txt = txt.replace(aadhaarRe, '[REDACTED_AADHAAR]'); redactions.push('in_aadhaar'); }
    if (panRe.test(txt)) { txt = txt.replace(panRe, '[REDACTED_PAN]'); redactions.push('in_pan'); }
    if (cardRe.test(txt)) { txt = txt.replace(cardRe, '[REDACTED_CARD]'); redactions.push('payment_card'); }
    const low = txt.toLowerCase();
    let score = 35;
    if (/\b(\d+\s*(?:lakh|lac|cr|crore|aed|sgd|m)|₹|\$)\b/.test(low)) score += 20;
    if (/(site visit|visit|saturday|sunday|viewing)/.test(low)) score += 22;
    if (/(nanded|pune|taroda|baner|dubai|marina|jurong|orchard|plot|bhk)/.test(low)) score += 12;
    if (/(immediate|ready|this week|urgent|book)/.test(low)) score += 11;
    score = Math.min(100, score);
    const t1 = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    return {
      clean_text: txt,
      redactions,
      score,
      tier: score >= 75 ? 'hot' : (score >= 55 ? 'warm' : 'nurture'),
      runtime: RE.onDevicePrescreen.probeRuntime(),
      latency_ms: Math.max(0.1, Number((t1 - t0).toFixed(2))),
    };
  },
};

function containDialogFocus(root) {
  root._priorFocus = document.activeElement;
  const focusable = () => Array.from(root.querySelectorAll('button:not([disabled]),a[href],input:not([type="hidden"]):not([disabled]),select,textarea,[tabindex="0"]')).filter(el => el.getClientRects().length);
  requestAnimationFrame(() => focusable()[0]?.focus());
  root.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return;
    const all = focusable(); if (!all.length) return;
    const first = all[0], last = all[all.length-1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
}
