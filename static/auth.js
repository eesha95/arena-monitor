/* Shared sign-in helper (Clerk). Loads Clerk only when the server says sign-in is on (/api/config).
   window.AM_AUTH resolves to { enabled, clerk, token() }. token() returns a fresh session JWT (Clerk tokens live
   about one minute and are cached by Clerk), or null when signed out. */
'use strict';
(function () {
  function loadScript(src, attrs) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src; s.crossOrigin = 'anonymous'; s.defer = true;
      Object.entries(attrs || {}).forEach(([k, v]) => s.setAttribute(k, v));
      s.onload = resolve; s.onerror = () => reject(new Error('could not load ' + src));
      document.head.appendChild(s);
    });
  }
  // Clerk's form in the site's own colours (current appearance variable names), light or dark from the page tokens.
  function themedAppearance() {
    const css = getComputedStyle(document.documentElement);
    const v = n => css.getPropertyValue(n).trim();
    return {
      variables: {
        colorPrimary: v('--accent'), colorPrimaryForeground: '#ffffff', colorBackground: v('--panel'),
        colorForeground: v('--text'), colorMutedForeground: v('--muted'), colorInput: v('--input'),
        colorInputForeground: v('--text'), colorBorder: v('--line'), colorNeutral: v('--text'),
        colorDanger: v('--down'), borderRadius: '10px', fontFamily: v('--sans'), fontSize: '15px',
      },
      options: { socialButtonsVariant: 'blockButton', socialButtonsPlacement: 'top', elevation: 'flush' },
    };
  }
  window.AM_AUTH = (async () => {
    let cfg;
    try { cfg = await (await fetch('/api/config')).json(); } catch (e) { cfg = { authEnabled: false }; }
    if (!cfg.authEnabled) return { enabled: false, clerk: null, cfg, token: async () => null };
    const base = 'https://' + cfg.frontendApi + '/npm';
    // Clerk's CDN setup: the UI bundle first, then clerk-js with the publishable key; then load() with the UI constructor.
    await loadScript(base + '/@clerk/ui@1/dist/ui.browser.js');
    await loadScript(base + '/@clerk/clerk-js@6/dist/clerk.browser.js', { 'data-clerk-publishable-key': cfg.publishableKey });
    await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor }, appearance: themedAppearance() });
    const clerk = window.Clerk;
    return {
      enabled: true, clerk, cfg,
      token: async () => (clerk.session ? await clerk.session.getToken() : null),
      signOut: async () => { await clerk.signOut(); location.href = '/'; },
    };
  })();
})();
