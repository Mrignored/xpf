/* Applies the saved (or system) colour scheme and language before first paint, so there is no flash. */
(function () {
  var root = document.documentElement;
  var theme = null, lang = null;
  try { theme = localStorage.getItem('xfp-theme'); lang = localStorage.getItem('xfp-lang'); } catch (e) { /* storage blocked */ }
  if (theme !== 'light' && theme !== 'dark') {
    theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  if (lang !== 'fa' && lang !== 'en') lang = 'fa';
  root.setAttribute('data-theme', theme);
  root.setAttribute('lang', lang);
  root.setAttribute('dir', lang === 'fa' ? 'rtl' : 'ltr');
})();
