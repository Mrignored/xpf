// Drives the real app.js in a minimal DOM shim against a live panel. Run: node tests/ui_smoke.js
'use strict';
const { spawn } = require('child_process');
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

class Node {}
class TextNode extends Node { constructor(t) { super(); this.nodeType = 3; this.data = String(t); this.parentNode = null; } get textContent() { return this.data; } set textContent(v) { this.data = String(v); } }
class El extends Node {
  constructor(tag) { super(); this.nodeType = 1; this.tagName = tag.toUpperCase(); this.attrs = {}; this.childNodes = []; this.parentNode = null; this.listeners = {}; this.style = {}; this.value = ''; this.checked = false; this.disabled = false; this.className = ''; this.dataset = {}; this.selected = false;
    const self = this; this.classList = { toggle(c, on) { const s = new Set(self.className.split(' ').filter(Boolean)); (on ? s.add(c) : s.delete(c)); self.className = [...s].join(' '); }, contains(c) { return self.className.split(' ').includes(c); } }; }
  setAttribute(k, v) { this.attrs[k] = v; if (k.startsWith('data-')) this.dataset[k.slice(5)] = v; if (k === 'value') this.value = v; }
  append(...kids) { for (const k of kids) { const n = (k instanceof El || k instanceof TextNode) ? k : new TextNode(k); if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.childNodes.push(n); } }
  removeChild(n) { this.childNodes = this.childNodes.filter((c) => c !== n); n.parentNode = null; return n; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get firstChild() { return this.childNodes[0] || null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  set textContent(v) { this.childNodes = [new TextNode(v)]; this.childNodes[0].parentNode = this; }
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  focus() {}
  closest(tag) { let n = this; while (n) { if (n.tagName === tag.toUpperCase()) return n; n = n.parentNode; } return null; }
  querySelectorAll(sel) { const tags = sel.split(',').map((s) => s.trim().split('.')[0].toUpperCase()); const out = []; const walk = (n) => { for (const c of n.childNodes) { if (c.nodeType === 1) { if (tags.includes(c.tagName)) out.push(c); walk(c); } } }; walk(this); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const all = (root, pred, out = []) => { for (const c of root.childNodes) { if (c.nodeType === 1) { if (pred(c)) out.push(c); all(c, pred, out); } } return out; };
const text = (el) => el.textContent.replace(/\s+/g, ' ');
const byText = (root, tag, t) => all(root, (e) => e.tagName === tag.toUpperCase() && text(e).trim() === t);
// Handlers are not awaited: many open a modal whose promise only settles once the test answers it.
const fire = async (el, type, extra) => { for (const fn of el.listeners[type] || []) Promise.resolve(fn({ target: el, preventDefault() {}, key: '', ...extra })).catch((e) => errors.push(e)); await tick(); };
const click = (el) => fire(el, 'click');
const errors = [];
const step = (m) => console.log('•', m);
const tick = (ms = 400) => new Promise((r) => setTimeout(r, ms));
const set = async (el, v) => { el.value = v; await fire(el, 'input'); };

let py;
const stop = (code) => { try { py.kill('SIGKILL'); } catch (e) { /* gone */ } process.exit(code); };
(async () => {
  py = spawn('python3', [path.join(__dirname, 'fixture_server.py')], { cwd: path.join(__dirname, '..'), stdio: ['pipe', 'pipe', 'inherit'] });
  let seen = '';
  const port = await new Promise((res) => py.stdout.on('data', (d) => { seen += String(d); const m = /PORT (\d+)/.exec(seen); if (m) res(m[1]); })); // output may arrive in pieces
  const base = 'http://127.0.0.1:' + port;
  let cookie = '';
  const doc = { body: new El('body'), documentElement: new El('html'), hidden: false, listeners: {}, createElement: (t) => new El(t), createElementNS: (ns, t) => new El(t), createTextNode: (t) => new TextNode(t), addEventListener() {}, removeEventListener() {} };
  const app = new El('div'); const toasts = new El('div');
  doc.getElementById = (id) => (id === 'app' ? app : toasts);
  const win = {}; const loc = { hash: '' };
  const fetchShim = async (url, opts = {}) => {
    const headers = { ...(opts.headers || {}) }; if (cookie) headers.Cookie = cookie;
    const res = await fetch(base + url, { ...opts, headers });
    const sc = res.headers.get('set-cookie'); if (sc) cookie = sc.split(';')[0].endsWith('=') ? '' : sc.split(';')[0];
    return res;
  };
  const ctx = vm.createContext({ Node, document: doc, window: win, location: loc, fetch: fetchShim, console, setTimeout, clearTimeout, setInterval, clearInterval, URLSearchParams, Date, Set, Map, Promise, Error, JSON, Object, Number, String, Array });
  win.document = doc;
  process.on('unhandledRejection', (e) => errors.push(e));
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../xfpanel/web/app.js'), 'utf8'), ctx);
  await tick();

  step('login');
  if (!text(app).includes('ورود به پنل')) { console.log('APP TEXT:', JSON.stringify(text(app)), 'ERRORS', errors); }
  assert.ok(text(app).includes('ورود به پنل'), 'login screen');
  const [user, pass] = all(app, (e) => e.tagName === 'INPUT');
  user.value = 'admin'; pass.value = 'wrong';
  await fire(all(app, (e) => e.tagName === 'FORM')[0], 'submit');
  assert.ok(text(app).includes('اشتباه'), 'bad login message');
  pass.value = 'secret-pass-1';
  await fire(all(app, (e) => e.tagName === 'FORM')[0], 'submit');
  await tick(600);
  assert.ok(text(app).includes('داشبورد') && text(app).includes('ترافیک اضافه محاسبه‌شده'), 'dashboard rendered');
  assert.ok(!text(app).includes('تعداد دفعات اعمال ضریب'), 'applications card removed');
  await tick(600);
  assert.ok(!/null|undefined|false/.test(text(all(app, (e) => e.tagName === 'MAIN')[0])), 'no stray null/undefined text on dashboard');
  assert.ok(text(app).includes('نسخه‌ی جدید v9.0.0 منتشر شده است.'), 'update banner shown');
  // theme toggle (day <-> night)
  const themeBtn = all(app, (e) => e.tagName === 'BUTTON' && e.attrs.title === 'تغییر تم')[0];
  await click(themeBtn); assert.strictEqual(doc.documentElement.getAttribute('data-theme'), 'dark');
  await click(themeBtn); assert.strictEqual(doc.documentElement.getAttribute('data-theme'), 'light');

  const go = async (route) => { loc.hash = '#/' + route; await win.onhashchange(); await tick(600); };
  const main = () => all(app, (e) => e.tagName === 'MAIN')[0];
  // dialogs are appended last, so the last matching button belongs to the open dialog
  const modalBtn = async (label) => { const b = byText(doc.body, 'button', label).pop(); assert.ok(b, 'modal button ' + label); await click(b); await tick(600); };

  step('inbounds: add factor by id');
  await go('inbounds');
  assert.ok(text(main()).includes('vless-443'), 'inbound list');
  let inputs = all(main(), (e) => e.tagName === 'INPUT' && e.attrs.placeholder);
  const idIn = inputs.find((i) => i.attrs.placeholder === 'شناسه اینباند'); const fIn = inputs.find((i) => i.attrs.placeholder.startsWith('ضریب'));
  idIn.value = '1'; fIn.value = '2.5';
  await click(byText(main(), 'button', 'ثبت ضریب')[0]); await tick(800);
  assert.ok(text(main()).includes('×2.5'), 'inbound factor visible: ' + text(main()).slice(0, 300));

  // edit through modal
  await click(byText(main(), 'button', 'ویرایش')[0]); await tick(100);
  const dlgInput = all(doc.body, (e) => e.tagName === 'INPUT' && e.attrs.placeholder === 'مثلاً 1.5')[0];
  dlgInput.value = '3'; await modalBtn('ثبت');
  assert.ok(text(main()).includes('×3'), 'inbound factor edited');

  step('clients');
  await go('clients');
  assert.ok(text(main()).includes('user1') && text(main()).includes('ضریب اینباند'), 'clients show inbound-sourced factor');
  inputs = all(main(), (e) => e.tagName === 'INPUT' && e.attrs.placeholder);
  inputs.find((i) => i.attrs.placeholder === 'ایمیل کلاینت').value = 'user5';
  inputs.find((i) => i.attrs.placeholder.startsWith('ضریب')).value = '1.7';
  await click(byText(main(), 'button', 'ثبت ضریب')[0]); await tick(800);
  assert.ok(text(main()).includes('×1.7'), 'client factor visible');

  // select two rows, bulk delete
  const rowBoxes = all(main(), (e) => e.tagName === 'INPUT' && e.attrs.type === 'checkbox').slice(1, 3);
  for (const b of rowBoxes) { b.checked = true; await fire(b, 'change'); }
  const bulkDel = all(main(), (e) => e.tagName === 'BUTTON' && text(e).startsWith('حذف ضریب انتخاب‌شده‌ها'))[0];
  assert.ok(!bulkDel.disabled && text(bulkDel).includes('(2)'), 'bulk delete enabled with count');

  // search + pagination controls render
  const search = all(main(), (e) => e.tagName === 'INPUT' && e.attrs.placeholder === 'جستجوی ایمیل')[0];
  await set(search, 'user6'); await tick(800);
  assert.ok(text(main()).includes('user6') && !text(main()).includes('user7'), 'search filters');
  assert.ok(text(main()).includes('1 / 1'), 'pager shown');

  step('backup and restore');
  await go('backup');
  assert.ok(text(main()).includes('xui-factor-panel') || text(main()).includes('/factor-backups') || text(main()).includes('ذخیره می‌شود'), 'backup paths shown');
  await click(byText(main(), 'button', 'ساخت بکاپ فاکتور')[0]); await tick(1200);
  assert.ok(text(main()).includes('xui-factor-backup-') && text(main()).includes('فاکتور'), 'backup listed');
  assert.ok(all(main(), (e) => e.tagName === 'A' && String(e.attrs.href).startsWith('/api/backups/download')).length >= 1, 'download link');
  await click(byText(main(), 'button', 'بازیابی')[0]); await tick(200);
  await modalBtn('بازیابی'); await tick(1500);
  assert.ok(text(doc.body).includes('بازیابی انجام شد'), 'restore finished: ' + text(doc.body).slice(-300));
  await modalBtn('بستن');
  step('events, maintenance, account');
  await go('events');
  assert.ok(text(main()).includes('فعال‌سازی قانون') || text(main()).includes('ثبت ضریب اختصاصی'), 'events listed');
  await go('maintenance');
  assert.ok(text(main()).includes('حذف کامل فاکتور از دیتابیس') && text(main()).includes('حذف فقط پنل وب'), 'purge and uninstall options');
  await click(byText(main(), 'button', 'حذف فقط پنل وب')[0]); await tick(200);
  assert.ok(text(doc.body).includes('DELETE'), 'uninstall asks for confirmation');
  await modalBtn('انصراف');
  await click(byText(main(), 'button', 'بررسی سلامت (doctor)')[0]); await tick(1500);
  assert.ok(text(main()).includes('Doctor'), 'doctor output shown');
  // clear all via confirm dialog
  await click(byText(main(), 'button', 'پاکسازی کل ضریب‌ها')[0]); await tick(100);
  await modalBtn('پاکسازی');
  await go('dashboard');
  assert.ok(/کلاینت‌های دارای ضریب\s*0/.test(text(main())), 'all factors cleared: ' + text(main()).slice(0, 400));
  await go('account');
  assert.ok(text(main()).includes('رمز عبور فعلی'));


  // ---- English UI: same app, language switched (the page reloads in the browser; here we mount a fresh copy)
  step('english UI and update flow');
  const doc2 = { body: new El('body'), documentElement: new El('html'), hidden: false, createElement: (t) => new El(t), createElementNS: (ns, t) => new El(t), createTextNode: (t) => new TextNode(t), addEventListener() {}, removeEventListener() {} };
  doc2.documentElement.setAttribute('lang', 'en'); doc2.documentElement.setAttribute('dir', 'ltr');
  const app2 = new El('div'); const toasts2 = new El('div');
  doc2.getElementById = (id) => (id === 'app' ? app2 : toasts2);
  const win2 = {}; const loc2 = { hash: '' };
  const ctx2 = vm.createContext({ Node, document: doc2, window: win2, location: loc2, fetch: fetchShim, console, setTimeout, clearTimeout, setInterval, clearInterval, URLSearchParams, Date, Set, Map, Promise, Error, JSON, Object, Number, String, Array });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../xfpanel/web/i18n-en.js'), 'utf8'), ctx2);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../xfpanel/web/app.js'), 'utf8'), ctx2);
  await tick(800);
  const main2 = () => all(app2, (e) => e.tagName === 'MAIN')[0];
  const t2 = text(main2());
  assert.ok(t2.includes('Dashboard') || text(app2).includes('Dashboard'), 'english nav: ' + text(app2).slice(0, 200));
  assert.ok(t2.includes('Extra traffic counted') && t2.includes('Clients with a factor'), 'english dashboard: ' + t2.slice(0, 300));
  assert.ok(t2.includes('New version v9.0.0 is available.'), 'english update banner');
  assert.ok(!/[\u0600-\u06FF]/.test(t2.replace(/فارسی/g, '')), 'no Persian text left on the English dashboard: ' + (t2.match(/[\u0600-\u06FF][^A-Za-z]{0,30}/) || [''])[0]);
  // update confirmation dialog, then the "updating" screen
  await click(byText(main2(), 'button', 'Update now')[0]); await tick(200);
  assert.ok(text(doc2.body).includes('Update to release v9.0.0?'), 'english update confirm');
  const yes = byText(doc2.body, 'button', 'Update now').pop(); await click(yes); await tick(800);
  assert.ok(text(app2).includes('Updating'), 'updating screen: ' + text(app2).slice(0, 200));
  // other pages in English
  win2.onhashchange; // (the update screen replaced the layout, so mount navigation pages again below)

  assert.deepStrictEqual(errors.map(String), [], 'no unhandled rejections');
  console.log('UI smoke test passed');
  stop(0);
})().catch((e) => { console.error('UI SMOKE FAILED:', e); stop(1); });
