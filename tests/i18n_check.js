// Fails if any t('…') key in app.js has no English entry, or if the English table has dead keys.
const fs = require('fs'), path = require('path'), vm = require('vm');
const dir = path.join(__dirname, '../xfpanel/web');
const ctx = vm.createContext({ window: {} });
vm.runInContext(fs.readFileSync(path.join(dir, 'i18n-en.js'), 'utf8'), ctx);
const EN = ctx.window.XFP_EN;
const src = fs.readFileSync(path.join(dir, 'app.js'), 'utf8');
const used = new Set();
for (const m of src.matchAll(/\bt\('((?:[^'\\\n]|\\.)*)'\)/g)) used.add(vm.runInContext("'" + m[1] + "'", vm.createContext({})));
const missing = [...used].filter((k) => !(k in EN));
const dead = Object.keys(EN).filter((k) => !used.has(k));
if (missing.length || dead.length) { console.error('missing:', missing, 'dead:', dead); process.exit(1); }
const persian = /[\u0600-\u06FF]/;
const untranslated = Object.entries(EN).filter(([k, v]) => persian.test(v) || !v.trim());
if (untranslated.length) { console.error('English values with Persian/empty text:', untranslated); process.exit(1); }
console.log('i18n ok: ' + used.size + ' strings');
