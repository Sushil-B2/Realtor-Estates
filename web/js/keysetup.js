/* ==========================================================================
   Operator credential intake (/keysetup.html).
   External script so Content-Security-Policy `script-src 'self'` allows it,
   and Bearer-authenticated so cross-site preview iframes work even when the
   browser blocks SameSite=Strict cookies.
   ========================================================================== */
'use strict';

(function () {
  const $ = (id) => document.getElementById(id);
  let token = '';
  let csrf = '';
  let loginChallenge = '';
  let activePassword = '';

  const warn = $('static-viewer-warn');
  if (warn && location.protocol.startsWith('http')) {
    warn.style.display = 'none';
  }

  function show(el, kind, text) {
    if (!el) return;
    el.className = 'msg on ' + kind;
    el.textContent = text;
  }
  function hide(el) {
    if (el) el.className = 'msg';
  }

  function api(method, path, body, extraHeaders) {
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (token) headers.Authorization = 'Bearer ' + token;
    else if (csrf && method !== 'GET') headers['X-CSRF-Token'] = csrf;
    if (extraHeaders) {
      Object.keys(extraHeaders).forEach((k) => { headers[k] = extraHeaders[k]; });
    }
    return fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then((res) =>
      res.json().catch(() => ({})).then((json) => ({ status: res.status, json }))
    );
  }

  function errText(r, fallback) {
    if (r && r.json && r.json.error) {
      if (typeof r.json.error === 'string') return r.json.error;
      if (r.json.error.message) return r.json.error.message;
    }
    return fallback + (r && r.status ? ' (HTTP ' + r.status + ')' : '');
  }

  function renderStatus(s) {
    const el = $('status');
    if (!el) return;
    el.innerHTML = '';
    const rows = [
      ['NEON_API_KEY', s.configured ? 'present (' + (s.masked || 'masked') + ')' : 'not set yet'],
      ['Key fingerprint', s.fingerprint || '—'],
      ['Key file', s.file + ' (' + s.mode + ')'],
      ['Runtime .env.local', s.stored_in_runtime_env ? 'also in .env.local — remove that copy' : 'clean (not in .env.local)'],
      ['Test branch URL', s.branch_configured ? 'present (' + (s.branch_masked || 'masked') + ')' : 'not set yet'],
      ['Branch fingerprint', s.branch_fingerprint || '—'],
    ];
    rows.forEach((row) => {
      const k = document.createElement('div');
      k.textContent = row[0];
      const v = document.createElement('div');
      v.textContent = row[1];
      el.appendChild(k);
      el.appendChild(v);
    });
  }

  function loadStatus() {
    return api('GET', '/api/v1/setup/neon-key').then((r) => {
      if (r.status === 200) renderStatus(r.json);
      return r;
    });
  }

  function finishLogin(r, usedPassword) {
    token = r.json.token || '';
    csrf = r.json.csrf || '';
    if ((r.json.user && r.json.user.role) !== 'owner') {
      show($('login-err'), 'err', 'Signed in as ' + ((r.json.user && r.json.user.role) || 'non-owner') + '; this page requires the owner role.');
      token = '';
      csrf = '';
      return;
    }
    activePassword = usedPassword || '';
    if (r.json.user && r.json.user.mfa_enrolled) {
      $('mfa-stepup-wrap').style.display = 'block';
    }
    $('confirm-pw').value = activePassword;
    $('step-login').className = 'step';
    $('step-key').className = 'step on';
    $('apikey').focus();
    loadStatus();
  }

  function doLogin(email, password, btn) {
    hide($('login-err'));
    if (!email || !password) {
      show($('login-err'), 'err', 'Email and password are required.');
      return;
    }
    if (btn) btn.disabled = true;

    if (loginChallenge) {
      const rawCode = $('login-code').value.trim();
      if (!rawCode) {
        if (btn) btn.disabled = false;
        show($('login-err'), 'err', 'Enter your 6-digit authenticator code or recovery code.');
        return;
      }
      const verifyBody = { challenge: loginChallenge };
      if (/^\d{6}$/.test(rawCode)) verifyBody.code = rawCode;
      else verifyBody.recovery_code = rawCode;
      api('POST', '/api/v1/auth/login/mfa', verifyBody)
        .then((r) => {
          if (btn) btn.disabled = false;
          if (r.status !== 200) {
            show($('login-err'), 'err', errText(r, 'MFA verification failed'));
            return;
          }
          loginChallenge = '';
          finishLogin(r, password);
        })
        .catch(() => {
          if (btn) btn.disabled = false;
          show($('login-err'), 'err', 'Network error — open this page inside the Realtor Estates App Live Preview.');
        });
      return;
    }

    api('POST', '/api/v1/auth/login', { email, password })
      .then((r) => {
        if (btn) btn.disabled = false;
        if (r.status !== 200) {
          show($('login-err'), 'err', errText(r, 'Sign-in failed'));
          return;
        }
        if (r.json.mfa_required && r.json.challenge) {
          loginChallenge = r.json.challenge;
          $('mfa-login-wrap').style.display = 'block';
          $('login-code').focus();
          show($('login-err'), 'ok', 'Password accepted — enter your 6-digit code and click Sign in again.');
          return;
        }
        finishLogin(r, password);
      })
      .catch(() => {
        if (btn) btn.disabled = false;
        show($('login-err'), 'err', 'Network error — open this page inside the Realtor Estates App Live Preview.');
      });
  }

  const demoBtn = $('btn-demo-owner');
  if (demoBtn) {
    demoBtn.addEventListener('click', function () {
      $('email').value = 'owner@realtorestates.app';
      $('password').value = 'Demo@12345!';
      doLogin('owner@realtorestates.app', 'Demo@12345!', this);
    });
  }

  const loginBtn = $('btn-login');
  if (loginBtn) {
    loginBtn.addEventListener('click', function () {
      doLogin($('email').value.trim(), $('password').value, this);
    });
  }

  const saveBtn = $('btn-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', function () {
      hide($('save-msg'));
      const key = $('apikey').value.trim();
      const branchUrl = $('branchurl').value.trim();
      const pw = $('confirm-pw').value || activePassword;
      if (!key && !branchUrl) {
        show($('save-msg'), 'err', 'Paste your Neon API key and/or test branch URL first.');
        return;
      }
      if (!pw) {
        show($('save-msg'), 'err', 'Enter the owner password to confirm saving a secret.');
        return;
      }
      const stepBody = { action: 'secret_write', target: 'neon_api_key', password: pw };
      const factor = $('stepup-code').value.trim();
      if (factor) {
        if (/^\d{6}$/.test(factor)) stepBody.code = factor;
        else stepBody.recovery_code = factor;
      }
      const btn = this;
      btn.disabled = true;
      api('POST', '/api/v1/auth/step-up', stepBody)
        .then((sr) => {
          if (sr.status !== 200 || !sr.json.ticket) {
            btn.disabled = false;
            if (sr.json.error && sr.json.error.code === 'FACTOR_REQUIRED') {
              $('mfa-stepup-wrap').style.display = 'block';
            }
            show($('save-msg'), 'err', errText(sr, 'Step-up confirmation failed'));
            return;
          }
          const payload = {};
          if (key) payload.api_key = key;
          if (branchUrl) payload.branch_url = branchUrl;
          return api('POST', '/api/v1/setup/neon-key', payload, { 'X-Step-Up': sr.json.ticket }).then((r) => {
            btn.disabled = false;
            if (r.status !== 200) {
              show($('save-msg'), 'err', errText(r, 'Save failed'));
              return;
            }
            $('apikey').value = '';
            $('branchurl').value = '';
            $('stepup-code').value = '';
            const savedParts = [];
            if (r.json.configured) savedParts.push('NEON_API_KEY (' + r.json.fingerprint + ')');
            if (r.json.branch_configured) savedParts.push('branch URL (' + r.json.branch_fingerprint + ')');
            show($('save-msg'), 'ok', 'Saved ' + savedParts.join(' + ') + ' in .secrets/ (0600). Tell the assistant "key saved" in chat!');
            loadStatus();
          });
        })
        .catch(() => {
          btn.disabled = false;
          show($('save-msg'), 'err', 'Network error — credential was not saved.');
        });
    });
  }
})();
