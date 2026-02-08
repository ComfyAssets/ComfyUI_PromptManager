/**
 * Theme Bridge — Detects ComfyUI palette and applies body-level styling.
 *
 * When running inside ComfyUI, the parent page already injects palette CSS
 * variables so our --pm-* tokens auto-resolve. This script adds a data
 * attribute for CSS selectors that need to distinguish light vs dark.
 *
 * When running standalone (direct URL), we fall back to dark defaults.
 */
(function () {
  'use strict';

  const LIGHT_PALETTES = ['light', 'solarized', 'github', 'arc'];

  async function detectPalette() {
    try {
      const res = await fetch('/api/settings/Comfy.ColorPalette');
      if (!res.ok) return 'dark';
      const data = await res.json();
      // data is the palette name string, or an object with value
      const name = (typeof data === 'string' ? data : data?.value || 'dark').toLowerCase();
      return name;
    } catch {
      // Standalone mode — no ComfyUI API available
      return 'dark';
    }
  }

  function applyPalette(name) {
    const isLight = LIGHT_PALETTES.some(p => name.includes(p));
    document.documentElement.setAttribute('data-pm-theme', isLight ? 'light' : 'dark');
  }

  // Run immediately — script is loaded in <head> so body isn't parsed yet.
  // We set the attribute as soon as possible to prevent flash.
  document.documentElement.setAttribute('data-pm-theme', 'dark');
  detectPalette().then(applyPalette);
})();
