/* XuiFactor panel front end. No dependencies, no innerHTML: every value is inserted as text. */
(() => {
  'use strict';

  // ---------------------------------------------------------------- language
  // The Persian text is the translation key; English lives in i18n-en.js (window.XFP_EN).
  const LANG = document.documentElement.getAttribute('lang') === 'en' ? 'en' : 'fa';
  const EN = window.XFP_EN || {};
  const t = (s) => (LANG === 'en' && EN[s]) || s;
  const switchLanguage = () => {
    try { localStorage.setItem('xfp-lang', LANG === 'en' ? 'fa' : 'en'); } catch (e) { /* storage blocked */ }
    location.reload();
  };
  document.title = 'XuiFactor | ' + (LANG === 'en' ? 'Traffic factor panel' : 'پنل ضریب مصرف');

  // ------------------------------------------------------------------ helpers
  const h = (tag, attrs, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'value') el.value = v;
      else if (k === 'checked' || k === 'disabled' || k === 'selected') el[k] = !!v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  };
  // Native append() would print "null"/"false" for skipped children, so conditional parts go through put().
  const put = (el, ...kids) => { for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) el.append(k); return el; };
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const ICONS = {
    dashboard: [['rect', { x: 3, y: 3, width: 7, height: 7, rx: 1 }], ['rect', { x: 14, y: 3, width: 7, height: 7, rx: 1 }], ['rect', { x: 14, y: 14, width: 7, height: 7, rx: 1 }], ['rect', { x: 3, y: 14, width: 7, height: 7, rx: 1 }]],
    inbounds: [['rect', { x: 2, y: 3, width: 20, height: 8, rx: 2 }], ['rect', { x: 2, y: 13, width: 20, height: 8, rx: 2 }], ['path', { d: 'M6 7h.01M6 17h.01' }]],
    clients: [['path', { d: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2' }], ['circle', { cx: 9, cy: 7, r: 4 }], ['path', { d: 'M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75' }]],
    events: [['polyline', { points: '22 12 18 12 15 21 9 3 6 12 2 12' }]],
    backup: [['path', { d: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4' }], ['polyline', { points: '7 10 12 15 17 10' }], ['line', { x1: 12, y1: 15, x2: 12, y2: 3 }]],
    maintenance: [['ellipse', { cx: 12, cy: 5, rx: 9, ry: 3 }], ['path', { d: 'M21 12c0 1.66-4 3-9 3s-9-1.34-9-3M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5' }]],
    account: [['path', { d: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2' }], ['circle', { cx: 12, cy: 7, r: 4 }]],
    sun: [['circle', { cx: 12, cy: 12, r: 5 }], ['path', { d: 'M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42' }]],
    moon: [['path', { d: 'M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z' }]],
    globe: [['circle', { cx: 12, cy: 12, r: 10 }], ['path', { d: 'M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z' }]],
    logout: [['path', { d: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4' }], ['polyline', { points: '16 17 21 12 16 7' }], ['line', { x1: 21, y1: 12, x2: 9, y2: 12 }]],
  };
  const icon = (name) => {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('class', 'ico'); svg.setAttribute('aria-hidden', 'true');
    for (const [tag, attrs] of ICONS[name] || []) {
      const node = document.createElementNS(SVG_NS, tag);
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      svg.append(node);
    }
    return svg;
  };
  const currentTheme = () => document.documentElement.getAttribute('data-theme') || 'light';
  const setTheme = (t) => {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('xfp-theme', t); } catch (e) { /* storage blocked: theme just won't persist */ }
  };
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  const num = (n) => Number(n || 0).toLocaleString('en-US');
  const bytes = (n) => {
    let v = Number(n || 0);
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v.toFixed(0) : v.toFixed(2)) + ' ' + units[i];
  };
  const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString(LANG === 'en' ? 'en-GB' : 'fa-IR') : '—');

  const SOURCE = {
    single: [t('ضریب اختصاصی کلاینت'), 'ok'],
    scope: [t('ضریب اینباند'), ''],
    override: [t('اختصاصی (روی ضریب دیگر)'), 'ok'],
    override_idle: [t('اختصاصی (بدون پوشش فعال)'), 'warn'],
    excluded: [t('مستثنی از ضریب'), 'warn'],
    none: ['—', ''],
  };
  const EVENT_LABEL = {
    rule_enabled: t('فعال‌سازی قانون'), rule_disabled: t('غیرفعال‌سازی قانون'), rule_paused: t('توقف قانون'),
    rule_resumed: t('ازسرگیری قانون'), rule_consolidated: t('ادغام قانون'), rule_reconciled: t('اصلاح قانون'),
    client_adopted_into_scope: t('عضویت کلاینت در اینباند'), client_relinked: t('اتصال مجدد کلاینت'),
    rule_skipped_not_compatible: t('ردشده (ناسازگار)'), traffic_applied: t('اعمال ضریب روی ترافیک'),
    client_rebaseline: t('ریست مبنای مصرف'), client_missing: t('کلاینت ناپیدا'), client_tracking_pruned: t('پاکسازی ردیابی'),
    scope_auto_enroll: t('ثبت خودکار کلاینت'), scope_auto_enroll_skipped: t('ثبت خودکار ردشد'), cleanup: t('پاکسازی متادیتا'),
    exclude_enabled: t('افزودن استثنا'), exclude_disabled: t('حذف استثنا'), override_enabled: t('ثبت ضریب اختصاصی'),
    override_updated: t('ویرایش ضریب اختصاصی'), override_disabled: t('حذف ضریب اختصاصی'),
  };

  // --------------------------------------------------------------------- API
  class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
  const api = async (method, path, body) => {
    const opts = { method, credentials: 'same-origin', headers: { 'X-Requested-With': 'XFPanel', 'X-Lang': LANG } };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, opts); } catch (e) { throw new ApiError(t('ارتباط با سرور برقرار نشد.'), 0); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty body */ }
    if (res.status === 401 && path !== '/api/login') { state.user = null; showLogin(); throw new ApiError(t('نشست منقضی شده است.'), 401); }
    if (!res.ok) throw new ApiError((data && data.error) || t('خطای ناشناخته (') + res.status + ')', res.status);
    return data;
  };
  const query = (obj) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(obj)) if (v !== '' && v !== null && v !== undefined) p.set(k, v);
    return p.toString();
  };

  // ---------------------------------------------------------------- UI pieces
  const toast = (msg, kind) => {
    const t = h('div', { class: 'toast ' + (kind || '') }, msg);
    document.getElementById('toasts').append(t);
    setTimeout(() => t.remove(), kind === 'bad' ? 7000 : 3800);
  };
  const fail = (e) => { if (!(e instanceof ApiError && e.status === 401)) toast(e.message || String(e), 'bad'); };

  const modal = ({ title, body, actions }) => new Promise((resolve) => {
    const close = (v) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); };
    const foot = h('div', { class: 'foot' }, actions.map((a) => h('button', {
      class: 'btn ' + (a.kind || ''), type: 'button',
      onclick: () => {
        if (!a.run) { close(a.value); return; }
        const r = a.run();
        if (r !== false) close(r === undefined ? a.value : r); // run() === false keeps the dialog open
      },
    }, a.label)));
    const overlay = h('div', { class: 'overlay', onmousedown: (e) => { if (e.target === overlay) close(null); } },
      h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' }, h('h2', {}, title), body, foot));
    document.body.append(overlay);
    document.addEventListener('keydown', onKey);
    const first = overlay.querySelector('input,select,button.primary,button');
    if (first) first.focus();
  });
  const confirmBox = (text, { danger = false, ok = t('تأیید') } = {}) =>
    modal({ title: t('تأیید عملیات'), body: h('p', {}, text), actions: [
      { label: ok, kind: danger ? 'danger solid' : 'primary', value: true },
      { label: t('انصراف'), value: false }] }).then((v) => v === true);
  const askFactor = (title, initial, hint) => {
    const input = h('input', { class: 'ltr', inputmode: 'decimal', value: initial || '', placeholder: t('مثلاً 1.5'), autocomplete: 'off' });
    return modal({ title, body: h('div', { class: 'field' },
      h('label', {}, t('ضریب (حداقل ۱، حداکثر ۶ رقم اعشار)')), input, hint ? h('span', { class: 'muted' }, hint) : null),
      actions: [{ label: t('ثبت'), kind: 'primary', run: () => (input.value.trim() ? input.value.trim() : false) },
        { label: t('انصراف'), value: null }] });
  };
  const showFailures = (title, result) => {
    if (!result.failed.length) return;
    modal({ title, body: h('div', {}, h('p', {}, result.failed.length + t(' مورد انجام نشد:')),
      h('table', {}, result.failed.map((f) => h('tr', {},
        h('td', { class: 'ltr' }, typeof f.item === 'object' ? (f.item.email || JSON.stringify(f.item)) : String(f.item)),
        h('td', { class: 'wrap' }, f.error))))),
      actions: [{ label: t('بستن'), kind: 'primary', value: true }] });
  };
  const summary = (label, r) => {
    if (r.ok) toast(label + ': ' + r.ok + t(' مورد موفق') + (r.failed.length ? t('، ') + r.failed.length + t(' ناموفق') : ''), r.failed.length ? '' : 'ok');
    else if (r.failed.length) toast(label + t(' ناموفق بود.'), 'bad');
  };

  const pager = (payload, onChange) => {
    const go = (p) => onChange({ page: p });
    const sizeSel = h('select', { onchange: (e) => onChange({ size: Number(e.target.value), page: 1 }) },
      [10, 25, 50, 100, 200].map((s) => h('option', { value: s, selected: s === payload.size }, s + t(' ردیف'))));
    return h('div', { class: 'pager' },
      h('span', {}, t('مجموع: ') + num(payload.total)),
      h('div', { class: 'pages' },
        h('button', { class: 'btn small', disabled: payload.page <= 1, onclick: () => go(1) }, '«'),
        h('button', { class: 'btn small', disabled: payload.page <= 1, onclick: () => go(payload.page - 1) }, t('قبلی')),
        h('span', {}, payload.page + ' / ' + payload.pages),
        h('button', { class: 'btn small', disabled: payload.page >= payload.pages, onclick: () => go(payload.page + 1) }, t('بعدی')),
        h('button', { class: 'btn small', disabled: payload.page >= payload.pages, onclick: () => go(payload.pages) }, '»')),
      sizeSel);
  };
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  const factorBadge = (factor, st) => (factor
    ? h('span', { class: 'tag factor' }, '×' + factor + (st === 'paused' ? ' ⏸' : ''))
    : h('span', { class: 'muted' }, '—'));

  // -------------------------------------------------------------------- state
  const state = { user: null, timer: null };
  const root = document.getElementById('app');

  // -------------------------------------------------------------------- login
  const showLogin = () => {
    clearInterval(state.timer);
    const user = h('input', { class: 'ltr', autocomplete: 'username', placeholder: t('نام کاربری') });
    const pass = h('input', { class: 'ltr', type: 'password', autocomplete: 'current-password', placeholder: t('رمز عبور') });
    const err = h('div', { class: 'err' });
    const submit = async (e) => {
      e.preventDefault();
      err.textContent = '';
      try {
        const r = await api('POST', '/api/login', { username: user.value, password: pass.value });
        state.user = r.user;
        boot();
      } catch (ex) { err.textContent = ex.message; pass.value = ''; }
    };
    clear(root).append(h('div', { class: 'login' }, h('div', { class: 'card' },
      h('h1', {}, 'XuiFactor'), h('p', { class: 'muted', style: 'text-align:center' }, t('ورود به پنل مدیریت ضریب مصرف')),
      h('form', { onsubmit: submit }, h('div', { class: 'field' }, user), h('div', { class: 'field' }, pass), err,
        h('button', { class: 'btn primary', style: 'width:100%', type: 'submit' }, t('ورود'))))));
    user.focus();
  };

  // ------------------------------------------------------------------- layout
  const ROUTES = [
    ['dashboard', t('داشبورد'), viewDashboard],
    ['inbounds', t('ضریب اینباندها'), viewInbounds],
    ['clients', t('ضریب کلاینت‌ها'), viewClients],
    ['events', t('بررسی و گزارش'), viewEvents],
    ['backup', t('بکاپ و بازیابی'), viewBackup],
    ['maintenance', t('نگهداری و حذف'), viewMaintenance],
    ['account', t('حساب کاربری'), viewAccount],
  ];
  const boot = () => {
    const main = h('main', { class: 'main' });
    const nav = h('nav', { class: 'nav' }, ROUTES.map(([id, label]) =>
      h('a', { href: '#/' + id, 'data-id': id, title: label }, icon(id), h('span', { class: 'label' }, label))));
    const themeLabel = h('span', { class: 'label' });
    const themeIcon = h('span', { style: 'display:inline-flex' });
    const themeBtn = h('button', { class: 'side-btn', type: 'button', title: t('تغییر تم'), onclick: () => { setTheme(currentTheme() === 'dark' ? 'light' : 'dark'); paintTheme(); } }, themeIcon, themeLabel);
    const paintTheme = () => {
      const dark = currentTheme() === 'dark';
      clear(themeIcon).append(icon(dark ? 'sun' : 'moon'));
      themeLabel.textContent = dark ? t('حالت روز') : t('حالت شب');
    };
    paintTheme();
    const langBtn = h('button', { class: 'side-btn', type: 'button', title: 'Language / زبان', onclick: switchLanguage }, icon('globe'), h('span', { class: 'label' }, LANG === 'en' ? 'فارسی' : 'English'));
    clear(root).append(h('div', { class: 'layout' },
      h('div', { class: 'sider-wrap' }, h('aside', { class: 'sider' },
        h('div', { class: 'brand' }, h('i', {}, '×'), h('span', { class: 'label' }, 'XuiFactor')), nav,
        h('div', { class: 'side-foot' }, h('div', { class: 'side-user label' }, state.user), langBtn, themeBtn,
          h('button', { class: 'side-btn', type: 'button', onclick: logout, title: t('خروج') }, icon('logout'), h('span', { class: 'label' }, t('خروج')))))),
      main));
    const render = () => {
      clearInterval(state.timer);
      const id = (location.hash.replace('#/', '') || 'dashboard');
      const route = ROUTES.find((r) => r[0] === id) || ROUTES[0];
      nav.querySelectorAll('a').forEach((a) => a.classList.toggle('active', a.dataset.id === route[0]));
      clear(main);
      route[2](main).catch(fail);
    };
    window.onhashchange = render;
    render();
  };
  const logout = async () => { try { await api('POST', '/api/logout', {}); } catch (e) { /* ignore */ } state.user = null; showLogin(); };

  // ---------------------------------------------------------------- dashboard
  // Shows the update banner; the version check itself is cached on the server (no polling from here).
  const updateBox = h('div');
  const paintUpdate = (u, manual) => {
    clear(updateBox);
    if (!u) return;
    if (u.error) { if (manual) put(updateBox, h('div', { class: 'note warn' }, u.error)); return; }
    if (u.newer) {
      put(updateBox, h('div', { class: 'note' },
        h('div', { class: 'row' }, h('strong', {}, t('نسخه‌ی جدید ') + u.tag + t(' منتشر شده است.')), h('span', { class: 'grow' }),
          h('button', { class: 'btn primary small', onclick: () => applyUpdate(u) }, t('بروزرسانی به این نسخه'))),
        u.url ? h('a', { href: u.url, target: '_blank', rel: 'noopener noreferrer' }, t('تغییرات این نسخه')) : null));
    } else if (manual) {
      put(updateBox, h('div', { class: 'note' }, t('نسخه‌ی شما به‌روز است.')));
    }
  };
  const waitForVersion = (u) => {
    clearInterval(state.timer);
    const msg = h('p', {}, t('پنل چند ثانیه‌ی دیگر دوباره بالا می‌آید؛ این صفحه خودکار بارگذاری مجدد می‌شود.'));
    clear(root).append(h('div', { class: 'login' }, h('div', { class: 'card' }, h('h1', {}, t('در حال بروزرسانی…')), msg)));
    const started = Date.now();
    let sawDown = false;
    const timer = setInterval(async () => {
      try {
        const d = await api('GET', '/api/overview');
        if (d.version === u.latest) { clearInterval(timer); location.reload(); return; }
        if (sawDown || Date.now() - started > 120000) {
          clearInterval(timer);
          clear(msg).append(t('بروزرسانی کامل نشد؛ نسخه‌ی قبلی حفظ شد. لاگ روی سرور: '), h('code', {}, u.log || '/var/log/xfp-update.log'));
        }
      } catch (e) { sawDown = true; }
      if (Date.now() - started > 120000) clearInterval(timer);
    }, 3000);
  };
  const applyUpdate = async (u) => {
    const ok = await modal({ title: t('بروزرسانی پنل'), body: h('div', {},
      h('p', {}, t('بروزرسانی به ریلیز ') + u.tag + t(' تأیید می‌شود؟ پنل چند ثانیه ری‌استارت می‌شود و قبل از آن بکاپ گرفته می‌شود.')),
      u.notes ? h('pre', {}, u.notes) : null),
      actions: [{ label: t('بروزرسانی به این نسخه'), kind: 'primary', value: true }, { label: t('انصراف'), value: false }] });
    if (ok !== true) return;
    try { await api('POST', '/api/update/apply', { confirm: true, tag: u.tag }); } catch (e) { fail(e); return; }
    waitForVersion(u);
  };

  async function viewDashboard(main) {
    const body = h('div');
    const checkBtn = h('button', { class: 'btn small', onclick: async () => {
      checkBtn.disabled = true; checkBtn.textContent = t('در حال بررسی…');
      try { state.update = await api('GET', '/api/update/check?force=1'); paintUpdate(state.update, true); } catch (e) { fail(e); }
      checkBtn.disabled = false; checkBtn.textContent = t('بررسی بروزرسانی');
    } }, t('بررسی بروزرسانی'));
    // passive check (server-cached for hours); a failure here stays silent
    api('GET', '/api/update/check').then((u) => { state.update = u; paintUpdate(u, false); }).catch(() => {});
    main.append(h('h1', {}, t('داشبورد')), body);
    const load = async () => {
      const d = await api('GET', '/api/overview');
      const svc = d.service;
      const svcBadge = h('span', { class: 'tag ' + (svc.active === 'active' ? 'ok' : svc.active === 'unknown' ? 'warn' : 'bad') },
        svc.active === 'active' ? t('سرویس فعال') : svc.active === 'unknown' ? t('وضعیت سرویس نامشخص') : t('سرویس غیرفعال'));
      const stat = (label, value, sub) => h('div', { class: 'stat' }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value), sub ? h('div', { class: 'sub' }, sub) : null);
      put(clear(body),
        d.backend !== 'sqlite' ? h('div', { class: 'note warn' }, t('دیتابیس PostgreSQL شناسایی شد؛ پنل وب فقط SQLite را پشتیبانی می‌کند.')) : null,
        !d.tables ? h('div', { class: 'note warn' }, t('جدول‌های فاکتور در دیتابیس وجود ندارد (فاکتور حذف شده یا هنوز ضریبی ثبت نشده است). با ثبت اولین ضریب ساخته می‌شود.')) : null,
        svc.active === 'inactive' || svc.active === 'failed' ? h('div', { class: 'note warn' }, t('سرویس فاکتور اجرا نمی‌شود؛ تا زمانی که فعال نشود ضریبی روی ترافیک اعمال نخواهد شد. از بخش «نگهداری» فعالش کنید.')) : null,
        updateBox,
        h('div', { class: 'row' }, svcBadge, h('span', { class: 'muted' }, t('نسخه پنل ') + d.version), checkBtn, h('span', { class: 'grow' }),
          h('button', { class: 'btn small', onclick: () => load().catch(fail) }, t('بروزرسانی'))),
        h('div', { class: 'grid' },
          stat(t('ضریب اینباند (فعال)'), num(d.inbound_scopes_active), d.inbound_scopes_paused ? num(d.inbound_scopes_paused) + t(' متوقف') : null),
          stat(t('ضریب سراسری'), num(d.global_scopes)),
          stat(t('ضریب اختصاصی کلاینت'), num(d.client_rules_active + d.overrides_active), d.client_rules_paused ? num(d.client_rules_paused) + t(' متوقف') : null),
          stat(t('کلاینت‌های دارای ضریب'), num(d.factored_clients), t('از ') + num(d.clients_total) + t(' کلاینت')),
          stat(t('کلاینت‌های تحت پایش'), num(d.tracked_clients)),
          stat(t('ترافیک اضافه محاسبه‌شده'), bytes(d.extra_bytes), t('۲۴ ساعت اخیر: ') + bytes(d.extra_bytes_24h)),
          stat(t('آخرین اعمال'), d.last_applied_at ? when(d.last_applied_at) : '—')),
        h('div', { class: 'card' }, h('h2', {}, t('آخرین رویدادها')),
          d.recent_events.length ? eventsTable(d.recent_events) : h('div', { class: 'empty' }, t('رویدادی ثبت نشده است.'))));
    };
    await load();
    // slow polling, and none at all while the tab is hidden (the server also caches these numbers)
    state.timer = setInterval(() => { if (!document.hidden) load().catch(() => {}); }, 30000);
  }

  // ----------------------------------------------------------------- inbounds
  async function viewInbounds(main) {
    const view = { page: 1, size: 25, q: '' };
    const selected = new Set();
    const list = h('div');
    const idIn = h('input', { class: 'ltr', inputmode: 'numeric', placeholder: t('شناسه اینباند'), style: 'width:150px' });
    const fIn = h('input', { class: 'ltr', inputmode: 'decimal', placeholder: t('ضریب مثلاً 1.5'), style: 'width:140px' });
    const search = h('input', { placeholder: t('جستجو (نام، پورت، پروتکل)'), class: 'grow', oninput: debounce((e) => { view.q = e.target.value; view.page = 1; load().catch(fail); }, 300) });
    const bulkSet = h('button', { class: 'btn', disabled: true, onclick: async () => {
      const f = await askFactor(t('ثبت ضریب برای ') + selected.size + t(' اینباند'));
      if (!f) return;
      try { const r = await api('POST', '/api/inbounds/set', { ids: [...selected], factor: f }); summary(t('ثبت ضریب'), r); showFailures(t('خطا در ثبت ضریب'), r); selected.clear(); await load(); } catch (e) { fail(e); }
    } }, t('ثبت ضریب برای انتخاب‌شده‌ها'));
    const bulkDel = h('button', { class: 'btn danger', disabled: true, onclick: async () => {
      if (!await confirmBox(t('ضریب ') + selected.size + t(' اینباند حذف شود؟ ضریب‌های اختصاصی کلاینت‌ها حفظ می‌شوند.'), { danger: true, ok: t('حذف') })) return;
      try { const r = await api('POST', '/api/inbounds/remove', { ids: [...selected] }); summary(t('حذف ضریب'), r); showFailures(t('خطا در حذف ضریب'), r); selected.clear(); await load(); } catch (e) { fail(e); }
    } }, t('حذف ضریب انتخاب‌شده‌ها'));
    const syncBulk = () => { bulkSet.disabled = bulkDel.disabled = selected.size === 0; bulkSet.textContent = t('ثبت ضریب برای انتخاب‌شده‌ها') + (selected.size ? ' (' + selected.size + ')' : ''); };

    const addForm = h('div', { class: 'card' }, h('h2', {}, t('ثبت ضریب برای کل اینباند با شناسه')),
      h('p', {}, t('ضریب روی همه کلاینت‌های اینباند (و کلاینت‌هایی که بعداً اضافه می‌شوند) اعمال می‌شود.')),
      h('div', { class: 'row' }, idIn, fIn, h('button', { class: 'btn primary', onclick: async () => {
        const f = fIn.value.trim();
        if (!idIn.value.trim() || !f) { toast(t('شناسه اینباند و ضریب را وارد کنید.'), 'bad'); return; }
        try { const r = await api('POST', '/api/inbounds/set', { ids: [idIn.value.trim()], factor: f }); summary(t('ثبت ضریب'), r); showFailures(t('خطا در ثبت ضریب'), r); if (!r.failed.length) { idIn.value = ''; fIn.value = ''; } await load(); } catch (e) { fail(e); }
      } }, t('ثبت ضریب'))));

    const load = async () => {
      const p = await api('GET', '/api/inbounds?' + query(view));
      const pageKeys = p.items.map((i) => i.id);
      const all = h('input', { type: 'checkbox', checked: pageKeys.length > 0 && pageKeys.every((k) => selected.has(k)), onchange: (e) => { pageKeys.forEach((k) => (e.target.checked ? selected.add(k) : selected.delete(k))); syncBulk(); load().catch(fail); } });
      const rows = p.items.map((i) => h('tr', { class: selected.has(i.id) ? 'selected' : '' },
        h('td', {}, h('input', { type: 'checkbox', checked: selected.has(i.id), onchange: (e) => { e.target.checked ? selected.add(i.id) : selected.delete(i.id); syncBulk(); e.target.closest('tr').classList.toggle('selected', e.target.checked); } })),
        h('td', { class: 'ltr' }, i.id), h('td', {}, i.remark || '—', i.enable ? null : h('span', { class: 'tag bad', style: 'margin-inline-start:6px' }, t('غیرفعال'))),
        h('td', { class: 'ltr' }, i.protocol + ':' + i.port), h('td', {}, num(i.clients)),
        h('td', {}, factorBadge(i.factor, i.state)),
        h('td', {}, i.client_rules + i.client_overrides ? num(i.client_rules + i.client_overrides) + t(' کلاینت') : '—'),
        h('td', {}, h('div', { class: 'actions' },
          h('button', { class: 'btn small', onclick: async () => {
            const f = await askFactor(i.factor ? t('ویرایش ضریب اینباند ') + i.id : t('ثبت ضریب اینباند ') + i.id, i.factor || '');
            if (!f) return;
            try { const r = await api('POST', '/api/inbounds/set', { ids: [i.id], factor: f }); summary(t('ثبت ضریب'), r); showFailures(t('خطا در ثبت ضریب'), r); await load(); } catch (e) { fail(e); }
          } }, i.factor ? t('ویرایش') : t('ثبت ضریب')),
          i.factor ? h('button', { class: 'btn small danger', onclick: async () => {
            if (!await confirmBox(t('ضریب اینباند ') + i.id + t(' حذف شود؟'), { danger: true, ok: t('حذف') })) return;
            try { const r = await api('POST', '/api/inbounds/remove', { ids: [i.id] }); summary(t('حذف ضریب'), r); showFailures(t('خطا در حذف ضریب'), r); selected.delete(i.id); await load(); } catch (e) { fail(e); }
          } }, t('حذف')) : null))));
      clear(list).append(
        h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, all), [t('شناسه'), t('نام'), t('پروتکل:پورت'), t('کلاینت‌ها'), t('ضریب اینباند'), t('ضریب اختصاصی'), t('عملیات')].map((t) => h('th', {}, t)))),
          h('tbody', {}, rows.length ? rows : h('tr', {}, h('td', { colspan: 8, class: 'empty' }, t('اینباندی پیدا نشد.')))))),
        pager(p, (c) => { Object.assign(view, c); load().catch(fail); }));
      syncBulk();
    };
    main.append(h('h1', {}, t('ضریب اینباندها')), addForm, h('div', { class: 'row' }, search, bulkSet, bulkDel), list);
    await load();
  }

  // ------------------------------------------------------------------ clients
  async function viewClients(main) {
    const view = { page: 1, size: 25, q: '', inbound_id: '', state: 'all' };
    const selected = new Map(); // email -> inbound_id
    const list = h('div');
    const mailIn = h('input', { class: 'ltr', placeholder: t('ایمیل کلاینت'), style: 'width:220px' });
    const idIn = h('input', { class: 'ltr', inputmode: 'numeric', placeholder: t('شناسه اینباند (اختیاری)'), style: 'width:190px' });
    const fIn = h('input', { class: 'ltr', inputmode: 'decimal', placeholder: t('ضریب مثلاً 1.5'), style: 'width:140px' });
    const items = () => [...selected].map(([email, inbound_id]) => ({ email, inbound_id }));
    const bulkSet = h('button', { class: 'btn', disabled: true, onclick: async () => {
      const f = await askFactor(t('ثبت ضریب برای ') + selected.size + t(' کلاینت'));
      if (!f) return;
      try { const r = await api('POST', '/api/clients/set', { items: items(), factor: f }); summary(t('ثبت ضریب'), r); showFailures(t('خطا در ثبت ضریب'), r); selected.clear(); await load(); } catch (e) { fail(e); }
    } }, t('ثبت ضریب'));
    const bulkDel = h('button', { class: 'btn danger', disabled: true, onclick: async () => {
      if (!await confirmBox(t('ضریب اختصاصی ') + selected.size + t(' کلاینت حذف شود؟'), { danger: true, ok: t('حذف') })) return;
      try { const r = await api('POST', '/api/clients/remove', { items: items() }); summary(t('حذف ضریب'), r); showFailures(t('خطا در حذف ضریب'), r); selected.clear(); await load(); } catch (e) { fail(e); }
    } }, t('حذف ضریب'));
    const clearSel = h('button', { class: 'btn small', disabled: true, onclick: () => { selected.clear(); load().catch(fail); } }, t('لغو انتخاب'));
    const syncBulk = () => {
      const n = selected.size;
      bulkSet.disabled = bulkDel.disabled = clearSel.disabled = n === 0;
      bulkSet.textContent = t('ثبت ضریب برای انتخاب‌شده‌ها') + (n ? ' (' + n + ')' : '');
      bulkDel.textContent = t('حذف ضریب انتخاب‌شده‌ها') + (n ? ' (' + n + ')' : '');
    };
    const reload = debounce(() => load().catch(fail), 300);
    const search = h('input', { class: 'grow', placeholder: t('جستجوی ایمیل'), oninput: (e) => { view.q = e.target.value; view.page = 1; reload(); } });
    const ibFilter = h('input', { class: 'ltr', inputmode: 'numeric', placeholder: t('اینباند'), style: 'width:100px', oninput: (e) => { view.inbound_id = e.target.value.trim(); view.page = 1; reload(); } });
    const stateSel = h('select', { onchange: (e) => { view.state = e.target.value; view.page = 1; load().catch(fail); } },
      [['all', t('همه')], ['factored', t('دارای ضریب')], ['single', t('ضریب اختصاصی')], ['scope', t('فقط ضریب اینباند')], ['override', t('اختصاصی روی اینباند')], ['none', t('بدون ضریب')], ['excluded', t('مستثنی')]]
        .map(([v, l]) => h('option', { value: v }, l)));

    const addForm = h('div', { class: 'card' }, h('h2', {}, t('ثبت ضریب برای یک کلاینت')),
      h('p', {}, t('ایمیل کلاینت و (در صورت نیاز) شناسه اینباند را وارد کنید. اگر شناسه اینباند خالی باشد از روی ایمیل پیدا می‌شود.')),
      h('div', { class: 'row' }, mailIn, idIn, fIn, h('button', { class: 'btn primary', onclick: async () => {
        const f = fIn.value.trim();
        if (!mailIn.value.trim() || !f) { toast(t('ایمیل و ضریب را وارد کنید.'), 'bad'); return; }
        try {
          const r = await api('POST', '/api/clients/set', { items: [{ email: mailIn.value.trim(), inbound_id: idIn.value.trim() }], factor: f });
          summary(t('ثبت ضریب'), r); showFailures(t('خطا در ثبت ضریب'), r);
          if (!r.failed.length) { mailIn.value = ''; idIn.value = ''; fIn.value = ''; }
          await load();
        } catch (e) { fail(e); }
      } }, t('ثبت ضریب'))));

    const load = async () => {
      const p = await api('GET', '/api/clients?' + query(view));
      const keys = p.items.map((c) => c.email);
      const all = h('input', { type: 'checkbox', checked: keys.length > 0 && keys.every((k) => selected.has(k)), onchange: (e) => { p.items.forEach((c) => (e.target.checked ? selected.set(c.email, c.inbound_id) : selected.delete(c.email))); syncBulk(); load().catch(fail); } });
      const rows = p.items.map((c) => {
        const [label, kind] = SOURCE[c.source] || [c.source, ''];
        const own = c.source === 'single' || c.source === 'override' || c.source === 'override_idle';
        return h('tr', { class: selected.has(c.email) ? 'selected' : '' },
          h('td', {}, h('input', { type: 'checkbox', checked: selected.has(c.email), onchange: (e) => { e.target.checked ? selected.set(c.email, c.inbound_id) : selected.delete(c.email); syncBulk(); e.target.closest('tr').classList.toggle('selected', e.target.checked); } })),
          h('td', { class: 'ltr' }, c.email, c.enable ? null : h('span', { class: 'tag bad', style: 'margin-inline-start:6px' }, t('غیرفعال'))),
          h('td', { class: 'ltr' }, c.inbound_id), h('td', { class: 'ltr' }, bytes((c.up || 0) + (c.down || 0))),
          h('td', {}, factorBadge(c.factor, c.state)),
          h('td', {}, h('span', { class: 'tag ' + kind }, label), c.source === 'override' && c.base_factor ? h('span', { class: 'muted', style: 'margin-inline-start:6px' }, t('(پایه ×') + c.base_factor + ')') : null),
          h('td', {}, h('div', { class: 'actions' },
            h('button', { class: 'btn small', onclick: async () => {
              const f = await askFactor((own ? t('ویرایش') : t('ثبت')) + t(' ضریب ') + c.email, own ? c.factor : '', c.source === 'scope' ? t('این کلاینت اکنون ضریب اینباند را دارد؛ ثبت ضریب، ضریب اختصاصی روی آن می‌گذارد.') : '');
              if (!f) return;
              try { const r = await api('POST', '/api/clients/set', { items: [{ email: c.email, inbound_id: c.inbound_id }], factor: f }); summary(t('ثبت ضریب'), r); showFailures(t('خطا در ثبت ضریب'), r); await load(); } catch (e) { fail(e); }
            } }, own ? t('ویرایش') : t('ثبت ضریب')),
            own ? h('button', { class: 'btn small danger', onclick: async () => {
              if (!await confirmBox(t('ضریب اختصاصی ') + c.email + t(' حذف شود؟'), { danger: true, ok: t('حذف') })) return;
              try { const r = await api('POST', '/api/clients/remove', { items: [{ email: c.email, inbound_id: c.inbound_id }] }); summary(t('حذف ضریب'), r); showFailures(t('خطا در حذف ضریب'), r); selected.delete(c.email); await load(); } catch (e) { fail(e); }
            } }, t('حذف')) : null)));
      });
      clear(list).append(
        h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, all), [t('ایمیل'), t('اینباند'), t('مصرف'), t('ضریب مؤثر'), t('منبع ضریب'), t('عملیات')].map((t) => h('th', {}, t)))),
          h('tbody', {}, rows.length ? rows : h('tr', {}, h('td', { colspan: 7, class: 'empty' }, t('کلاینتی پیدا نشد.')))))),
        pager(p, (c) => { Object.assign(view, c); load().catch(fail); }));
      syncBulk();
    };
    main.append(h('h1', {}, t('ضریب کلاینت‌ها')), addForm,
      h('div', { class: 'row' }, search, ibFilter, stateSel, bulkSet, bulkDel, clearSel), list);
    await load();
  }

  // ------------------------------------------------------------------- events
  function eventsTable(items) {
    return h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, [t('زمان'), t('نوع'), t('قانون'), t('کلاینت'), t('اینباند'), t('جزئیات')].map((t) => h('th', {}, t)))),
      h('tbody', {}, items.map((e) => h('tr', {},
        h('td', {}, when(e.created_at)), h('td', {}, EVENT_LABEL[e.type] || e.type), h('td', { class: 'ltr' }, e.rule_id || '—'),
        h('td', { class: 'ltr' }, e.email || '—'), h('td', { class: 'ltr' }, e.inbound_id || '—'),
        h('td', { class: 'wrap ltr' }, e.extra_bytes !== null ? 'extra ' + bytes(e.extra_bytes) : e.message))))));
  }
  async function viewEvents(main) {
    const view = { page: 1, size: 25, kind: 'lifecycle', q: '' };
    const list = h('div');
    const kindSel = h('select', { onchange: (e) => { view.kind = e.target.value; view.page = 1; load().catch(fail); } });
    const search = h('input', { class: 'grow', placeholder: t('جستجو در جزئیات (ایمیل، شماره قانون، …)'), oninput: debounce((e) => { view.q = e.target.value; view.page = 1; load().catch(fail); }, 300) });
    const load = async () => {
      const p = await api('GET', '/api/events?' + query(view));
      const opts = [['lifecycle', t('رویدادهای مدیریتی')], ['all', t('همه (شامل اعمال ترافیک)')]].concat(p.types.map((t) => [t, EVENT_LABEL[t] || t]));
      clear(kindSel).append(...opts.map(([v, l]) => h('option', { value: v, selected: v === view.kind }, l)));
      clear(list).append(p.items.length ? eventsTable(p.items) : h('div', { class: 'card empty' }, t('رویدادی پیدا نشد.')), pager(p, (c) => { Object.assign(view, c); load().catch(fail); }));
    };
    main.append(h('h1', {}, t('بررسی و گزارش رویدادها')), h('div', { class: 'row' }, kindSel, search, h('button', { class: 'btn', onclick: () => load().catch(fail) }, t('بروزرسانی'))), list);
    await load();
  }

  // ------------------------------------------------------------------- backup
  const uploadFile = async (file) => {
    let res;
    try {
      res = await fetch('/api/backups/upload', { method: 'POST', credentials: 'same-origin', body: file,
        headers: { 'X-Requested-With': 'XFPanel', 'X-Lang': LANG, 'Content-Type': 'application/octet-stream' } });
    } catch (e) { throw new ApiError(t('ارسال فایل ناموفق بود (ارتباط قطع شد).'), 0); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (res.status === 401) { state.user = null; showLogin(); throw new ApiError(t('نشست منقضی شده است.'), 401); }
    if (!res.ok) throw new ApiError((data && data.error) || t('خطای ناشناخته (') + res.status + ')', res.status);
    return data;
  };
  const KIND_LABEL = { factor: [t('بکاپ فاکتور'), 'info'], full: [t('بکاپ کامل دیتابیس'), 'warn'] };

  async function viewBackup(main) {
    const list = h('div');
    const info = h('div');
    const histBox = h('input', { type: 'checkbox' });
    const fileIn = h('input', { type: 'file', hidden: true, accept: '.gz,.db' });
    const busy = h('span', { class: 'muted' });

    const confirmRestore = async (name, meta) => {
      const rebase = h('input', { type: 'checkbox', checked: true });
      const ok = await modal({ title: t('بازیابی بکاپ فاکتور'), body: h('div', {},
        h('p', {}, t('همه‌ی ضریب‌های فعلی با محتوای این بکاپ جایگزین می‌شوند. قبل از آن یک بکاپ از وضعیت فعلی گرفته می‌شود.')),
        h('p', {}, t('فایل: '), h('code', {}, name), meta ? h('span', {}, ' — ' + num(meta.rules) + t(' قانون')) : null),
        h('label', {}, rebase, t(' مبنای مصرف به شمارنده‌ی فعلی بازنشانی شود (پیشنهادی؛ از ضرب دوباره‌ی ترافیک گذشته جلوگیری می‌کند)'))),
        actions: [{ label: t('بازیابی'), kind: 'primary', run: () => ({ rebase: rebase.checked }) }, { label: t('انصراف'), value: null }] });
      if (!ok) return;
      busy.textContent = t('در حال بازیابی…');
      try {
        const r = await api('POST', '/api/backups/restore', { name, rebase: ok.rebase });
        const counts = Object.entries(r.restored).map(([k, v]) => k.replace('xui_factor_', '') + ': ' + num(v)).join(t('، '));
        await modal({ title: t('بازیابی انجام شد'), body: h('div', {},
          h('p', {}, counts),
          r.safety_backup ? h('p', {}, t('وضعیت قبلی ذخیره شد: '), h('code', {}, r.safety_backup)) : null,
          r.warnings.length ? h('div', { class: 'note warn' }, r.warnings.map((w) => h('div', {}, w))) : null,
          r.doctor ? h('pre', {}, r.doctor) : null),
          actions: [{ label: t('بستن'), kind: 'primary', value: true }] });
      } catch (e) { fail(e); }
      busy.textContent = '';
      await load();
    };

    const handleFile = async (file) => {
      if (!file) return;
      busy.textContent = t('در حال ارسال و بررسی فایل…');
      try {
        const meta = await uploadFile(file);
        busy.textContent = '';
        await load();
        await confirmRestore(meta.name, meta);
      } catch (e) { busy.textContent = ''; fail(e); }
    };
    fileIn.addEventListener('change', () => { const f = fileIn.files && fileIn.files[0]; fileIn.value = ''; handleFile(f); });
    const zone = h('div', { class: 'dropzone', tabindex: 0, role: 'button',
      onclick: () => fileIn.click(), onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') fileIn.click(); },
      ondragover: (e) => { e.preventDefault(); zone.classList.toggle('over', true); },
      ondragleave: () => zone.classList.toggle('over', false),
      ondrop: (e) => { e.preventDefault(); zone.classList.toggle('over', false); handleFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]); } },
      h('strong', {}, t('فایل بکاپ را اینجا رها کنید یا برای انتخاب کلیک کنید')),
      h('span', {}, t('فایل‌های xui-factor-backup-….db.gz (بکاپ فاکتور). پس از ارسال، قبل از بازیابی تأیید می‌گیرد.')));

    const load = async () => {
      const d = await api('GET', '/api/backups');
      clear(info).append(h('div', { class: 'note' },
        h('div', {}, t('بکاپ فاکتور روی سرور در این مسیر ذخیره می‌شود: '), h('code', {}, d.factor_dir)),
        h('div', {}, t('بکاپ کامل دیتابیس 3x-ui در این مسیر: '), h('code', {}, d.full_dir)),
        h('div', { class: 'muted' }, t('برای بازیابی روی همین سرور هم می‌توانید از فهرست زیر استفاده کنید، یا از ترمینال: '), h('code', {}, 'xfp restore'))));
      const rows = d.items.map((b) => {
        const [label, kind] = KIND_LABEL[b.kind] || [b.kind, ''];
        const dl = '/api/backups/download?' + query({ kind: b.kind, name: b.name });
        return h('tr', {}, h('td', { class: 'ltr' }, b.name), h('td', {}, h('span', { class: 'tag ' + kind }, label)),
          h('td', { class: 'ltr' }, bytes(b.size)), h('td', {}, when(b.mtime)),
          h('td', {}, h('div', { class: 'actions' },
            h('a', { class: 'btn small', href: dl, download: b.name }, t('دانلود')),
            b.kind === 'factor' ? h('button', { class: 'btn small', onclick: () => confirmRestore(b.name, null) }, t('بازیابی')) : null,
            h('button', { class: 'btn small danger', onclick: async () => {
              if (!await confirmBox(t('فایل ') + b.name + t(' از سرور حذف شود؟'), { danger: true, ok: t('حذف') })) return;
              try { await api('POST', '/api/backups/delete', { name: b.name, kind: b.kind }); toast(t('حذف شد.'), 'ok'); await load(); } catch (e) { fail(e); }
            } }, t('حذف')))));
      });
      clear(list).append(h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, [t('فایل'), t('نوع'), t('حجم'), t('تاریخ'), t('عملیات')].map((t) => h('th', {}, t)))),
        h('tbody', {}, rows.length ? rows : h('tr', {}, h('td', { colspan: 5, class: 'empty' }, t('هنوز بکاپی ساخته نشده است.')))))));
    };

    main.append(h('h1', {}, t('بکاپ و بازیابی')),
      h('div', { class: 'card' }, h('h2', {}, t('ساخت بکاپ')),
        h('p', {}, t('بکاپ فاکتور فقط ضریب‌ها و مبنای مصرف را نگه می‌دارد و بسیار کوچک است. بکاپ کامل، کپی کل دیتابیس 3x-ui است (فاکتور هم داخلش هست) و برای بازیابی باید فایل دیتابیس را دستی جایگزین کنید.')),
        h('div', { class: 'row' },
          h('button', { class: 'btn primary', onclick: async () => {
            busy.textContent = t('در حال ساخت بکاپ…');
            try { const r = await api('POST', '/api/backups/create', { history: histBox.checked }); toast(t('ذخیره شد: ') + r.path, 'ok'); await load(); } catch (e) { fail(e); }
            busy.textContent = '';
          } }, t('ساخت بکاپ فاکتور')),
          h('label', {}, histBox, t(' شامل تاریخچه‌ی اعمال ترافیک (حجم زیاد)')),
          h('span', { class: 'grow' }),
          h('button', { class: 'btn', onclick: async () => {
            busy.textContent = t('در حال کپی دیتابیس…');
            try { const r = await api('POST', '/api/backups/full', {}); toast(t('ذخیره شد: ') + r.path, 'ok'); await load(); } catch (e) { fail(e); }
            busy.textContent = '';
          } }, t('بکاپ کامل دیتابیس 3x-ui')), busy)),
      h('div', { class: 'card' }, h('h2', {}, t('انتقال به سرور دیگر')),
        h('ol', {}, h('li', {}, t('روی سرور جدید 3x-ui (و دیتابیسش) را منتقل یا بازیابی کنید.')),
          h('li', {}, t('همین پروژه را با install.sh نصب کنید.')),
          h('li', {}, t('فایل بکاپ فاکتور را از همین صفحه دانلود کنید و در سرور جدید در بخش زیر رها کنید.'))),
        h('p', {}, t('کلاینت‌ها با ایمیل تطبیق داده می‌شوند (شناسه‌ها می‌توانند فرق کنند). کلاینتی که در سرور جدید نیست نادیده گرفته می‌شود.'))),
      h('div', { class: 'card' }, h('h2', {}, t('بازیابی از فایل روی سیستم شما')), zone, fileIn),
      h('h2', {}, t('بکاپ‌های روی سرور')), info, list);
    await load();
  }

  // -------------------------------------------------------------- maintenance
  async function viewMaintenance(main) {
    const out = h('pre', { style: 'display:none' });
    const show = (text) => { out.style.display = 'block'; out.textContent = text; };
    const act = (label, path, body, after) => async () => {
      try { const r = await api('POST', path, body || {}); toast(label + t(' انجام شد.'), 'ok'); if (r.output !== undefined) show(r.output); if (after) after(r); } catch (e) { fail(e); }
    };
    const purgeFlow = async () => {
      const conf = h('input', { class: 'ltr', placeholder: 'DELETE', autocomplete: 'off' });
      const keep = h('input', { type: 'checkbox' });
      const v = await modal({ title: t('حذف کامل فاکتور از دیتابیس'), body: h('div', {},
        h('p', {}, t('همه جدول‌های xui_factor_* از دیتابیس x-ui حذف و سرویس xui-factor متوقف و حذف می‌شود. ترافیک ثبت‌شده‌ی قبلی تغییر نمی‌کند، اما ضریب‌ها دیگر اعمال نخواهند شد. قبل از حذف یک بکاپ گرفته می‌شود.')),
        h('div', { class: 'field' }, h('label', {}, t('برای تأیید، DELETE را تایپ کنید')), conf),
        h('label', {}, keep, t(' فایل سرویس حذف نشود (فقط جدول‌ها پاک شوند)'))),
        actions: [{ label: t('حذف کامل'), kind: 'danger solid', run: () => (conf.value === 'DELETE' ? { remove_service: !keep.checked } : (toast(t('عبارت تأیید درست نیست.'), 'bad'), false)) },
          { label: t('انصراف'), value: null }] });
      if (!v) return;
      try { const r = await api('POST', '/api/actions/purge', { confirm: 'DELETE', remove_service: v.remove_service }); toast(t('حذف شد: ') + r.dropped.length + t(' جدول'), 'ok'); show(t('جدول‌های حذف‌شده:\n') + r.dropped.join('\n') + (r.backup ? '\n\n' + r.backup : '')); } catch (e) { fail(e); }
    };
    const uninstallFlow = async (mode) => {
      const conf = h('input', { class: 'ltr', placeholder: 'DELETE', autocomplete: 'off' });
      const all = mode === 'all';
      const v = await modal({ title: all ? t('حذف کامل فاکتور و پنل وب') : t('حذف پنل وب'), body: h('div', {},
        h('p', {}, all ? t('رکوردهای فاکتور از دیتابیس پاک و سرویس xui-factor و پنل حذف می‌شود؛ ضریب‌ها دیگر اعمال نخواهند شد. قبل از آن یک بکاپ در پوشه‌ی بکاپ‌ها ذخیره می‌شود و بکاپ‌ها پاک نمی‌شوند.')
          : t('فقط پنل وب حذف می‌شود. سرویس xui-factor و ضریب‌ها دست‌نخورده می‌مانند و با دستور xfp قابل مدیریت‌اند.')),
        h('div', { class: 'field' }, h('label', {}, t('برای تأیید، DELETE را تایپ کنید')), conf)),
        actions: [{ label: t('حذف'), kind: 'danger solid', run: () => (conf.value === 'DELETE' ? true : (toast(t('عبارت تأیید درست نیست.'), 'bad'), false)) },
          { label: t('انصراف'), value: null }] });
      if (!v) return;
      try {
        await api('POST', '/api/actions/uninstall', { mode, confirm: 'DELETE' });
        clearInterval(state.timer);
        clear(root).append(h('div', { class: 'login' }, h('div', { class: 'card' }, h('h1', {}, t('در حال حذف…')),
          h('p', {}, t('عملیات حذف روی سرور شروع شد و چند ثانیه‌ی دیگر این پنل از دسترس خارج می‌شود.') + (all ? '' : t(' سرویس xui-factor همچنان فعال می‌ماند.'))))));
      } catch (e) { fail(e); }
    };
    const card = (title, text, buttons, extra) => h('div', { class: 'card ' + (extra || '') }, h('h2', {}, title), h('p', {}, text), h('div', { class: 'row' }, buttons));
    main.append(h('h1', {}, t('نگهداری و حذف')),
      card(t('فعال / غیرفعال‌سازی کلی فاکتور'), t('غیرفعال‌سازی: همه ضریب‌ها متوقف (pause) و سرویس خاموش می‌شود؛ ضریب‌ها در دیتابیس می‌مانند. فعال‌سازی: سرویس روشن و ضریب‌ها از مصرف فعلی ادامه پیدا می‌کنند.'), [
        h('button', { class: 'btn', onclick: async () => { if (await confirmBox(t('فاکتور به‌طور کامل غیرفعال شود؟'), { danger: true, ok: t('غیرفعال‌سازی') })) act(t('غیرفعال‌سازی'), '/api/actions/disable')(); } }, t('غیرفعال‌سازی کامل فاکتور')),
        h('button', { class: 'btn primary', onclick: act(t('فعال‌سازی'), '/api/actions/enable') }, t('فعال‌سازی مجدد'))]),
      card(t('پاکسازی همه ضریب‌ها'), t('همه ضریب‌های اینباند، کلاینت و سراسری حذف می‌شود. سرویس روشن می‌ماند و می‌توانید دوباره ضریب ثبت کنید.'), [
        h('button', { class: 'btn danger', onclick: async () => { if (await confirmBox(t('همه ضریب‌ها پاک شود؟ این کار قابل بازگشت نیست.'), { danger: true, ok: t('پاکسازی') })) act(t('پاکسازی ضریب‌ها'), '/api/actions/clear-all')(); } }, t('پاکسازی کل ضریب‌ها'))]),
      card(t('ابزارها'), t('بررسی سلامت، گرفتن بکاپ و پاکسازی متادیتای قدیمی.'), [
        h('button', { class: 'btn', onclick: act(t('بررسی سلامت'), '/api/actions/doctor') }, t('بررسی سلامت (doctor)')),
        h('button', { class: 'btn', onclick: act(t('بکاپ'), '/api/actions/backup') }, t('گرفتن بکاپ')),
        h('button', { class: 'btn', onclick: act(t('پیش‌نمایش پاکسازی'), '/api/actions/cleanup', { dry_run: true }) }, t('پیش‌نمایش پاکسازی متادیتا')),
        h('button', { class: 'btn', onclick: async () => { if (await confirmBox(t('پاکسازی متادیتای قدیمی اجرا شود؟'), { ok: t('اجرا') })) act(t('پاکسازی متادیتا'), '/api/actions/cleanup', { dry_run: false })(); } }, t('اجرای پاکسازی'))]),
      out,
      card(t('حذف فاکتور از دیتابیس'), t('همه رکوردهای فاکتور از دیتابیس x-ui حذف و سرویس فاکتور برای همیشه خاموش و حذف می‌شود.'), [
        h('button', { class: 'btn danger solid', onclick: purgeFlow }, t('حذف کامل فاکتور از دیتابیس'))], 'danger-zone'),
      card(t('حذف پنل وب'), t('حذف فقط پنل وب: سرویس xui-factor روی سرور به کار خودش ادامه می‌دهد و ضریب‌ها اعمال می‌شوند (مدیریت از ترمینال با دستور xfp). حذف کامل: بعد از بکاپ خودکار، رکوردهای فاکتور از دیتابیس پاک می‌شود و سرویس xui-factor و پنل وب برای همیشه از سرور برداشته می‌شوند (پایان پروژه روی این سرور).'), [
        h('button', { class: 'btn danger', onclick: () => uninstallFlow('web') }, t('حذف فقط پنل وب')),
        h('button', { class: 'btn danger solid', onclick: () => uninstallFlow('all') }, t('حذف کامل (فاکتور + پنل)'))], 'danger-zone'));
  }

  // ------------------------------------------------------------------ account
  async function viewAccount(main) {
    const cur = h('input', { class: 'ltr', type: 'password', autocomplete: 'current-password' });
    const user = h('input', { class: 'ltr', autocomplete: 'username', value: state.user || '' });
    const pass = h('input', { class: 'ltr', type: 'password', autocomplete: 'new-password', placeholder: t('خالی = بدون تغییر') });
    const pass2 = h('input', { class: 'ltr', type: 'password', autocomplete: 'new-password' });
    const field = (label, input) => h('div', { class: 'field' }, h('label', {}, label), input);
    main.append(h('h1', {}, t('حساب کاربری')), h('div', { class: 'card', style: 'max-width:440px' },
      field(t('رمز عبور فعلی'), cur), field(t('نام کاربری'), user), field(t('رمز عبور جدید (حداقل ۸ نویسه)'), pass), field(t('تکرار رمز جدید'), pass2),
      h('button', { class: 'btn primary', onclick: async () => {
        if (pass.value !== pass2.value) { toast(t('تکرار رمز عبور یکسان نیست.'), 'bad'); return; }
        try {
          await api('POST', '/api/account', { current_password: cur.value, username: user.value.trim(), password: pass.value });
          toast(t('ذخیره شد؛ دوباره وارد شوید.'), 'ok'); state.user = null; showLogin();
        } catch (e) { fail(e); }
      } }, t('ذخیره'))));
  }

  // --------------------------------------------------------------------- init
  api('GET', '/api/me').then((r) => { state.user = r.user; boot(); }).catch(() => showLogin());
})();
