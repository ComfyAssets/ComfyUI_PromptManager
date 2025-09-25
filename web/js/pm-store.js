/* pm-store.js
   PromptManager global store + API client (CDN-friendly, no build)
   Usage:
     <script src="/js/pm-store.js"></script>
     <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
     <script>
       document.addEventListener('alpine:init', () => {
         Alpine.store('pm', PM.createStore({ apiBase: '/api' }));
       });
     </script>
*/
(() => {
  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] pm-store skipped outside PromptManager UI context');
    return;
  }
  const PM = {};
  // ---- Utilities ----
  PM.formatDate = function formatDate(s, { withTZ = false } = {}) {
    if (!s) return '';
    try {
      // Normalize fractional seconds to millisecond precision so JS Date parses reliably
      const m = String(s).match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$/);
      if (m) {
        const base = m[1];
        const frac = m[2] ? ('.' + m[2].slice(1, 4)) : '';
        const zone = m[3] || 'Z';
        const d = new Date(base + frac + zone);
        if (!isNaN(d.getTime())) return d.toLocaleString(undefined, withTZ ? { hour12: false, timeZoneName: 'short' } : { hour12: false });
      }
      const d2 = new Date(s);
      if (!isNaN(d2.getTime())) return d2.toLocaleString(undefined, withTZ ? { hour12: false, timeZoneName: 'short' } : { hour12: false });
      return String(s);
    } catch { return String(s); }
  };
  const DEFAULTS = {
    settings: {
      db: {
        path: "prompts.db",
        autoSave: true,
        saveInterval: 30,
        backup: true,
        backupInterval: 3600,
        maxBackups: 10,
      },
      cache: { enabled: true, maxMB: 512, ttl: 3600 },
      perf: { concurrency: 4, prefetch: true },
      display: {
        density: "comfortable",
        font: 15,
        tooltips: true,
        retina: true,
      },
      gallery: { cols: 4, showEXIF: false },
      notify: { desktop: false, toasts: true },
      logging: { level: "info", buffer: 2000 },
      advanced: { dev: false, beta: false },
    },
  };

  // ---------- Toasts unified via notificationService ----------
  function toast({ variant = "primary", icon = "info-circle", text = "" }) {
    const map = { primary: 'info', success: 'success', warning: 'warning', danger: 'error', info: 'info' };
    const type = map[variant] || 'info';
    if (typeof window.showToast === 'function') {
      return window.showToast(text, type, {});
    }
    if (window.notificationService && typeof window.notificationService.show === 'function') {
      return window.notificationService.show(text, type, {});
    }
    // Fallback to Shoelace alert if notification system not loaded yet
    const el = document.createElement('sl-alert');
    el.variant = variant; el.closable = true; el.style.position = 'fixed'; el.style.right = '12px'; el.style.bottom = '12px'; el.style.zIndex = 9999;
    el.innerHTML = `<sl-icon slot="icon">${icon}</sl-icon>${text}`; document.body.appendChild(el); el.toast?.(); return el;
  }

  // ---------- Helpers ----------
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function fetchJSON({
    method = "GET",
    url,
    body,
    headers = {},
    retries = 1,
    timeoutMs = 15000,
  }) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json", ...headers },
        body: body ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}: ${txt || res.statusText}`);
      }
      const ct = res.headers.get("content-type") || "";
      return ct.includes("application/json") ? res.json() : res.text();
    } catch (err) {
      if (retries > 0) {
        await sleep(400);
        return fetchJSON({
          method,
          url,
          body,
          headers,
          retries: retries - 1,
          timeoutMs,
        });
      }
      throw err;
    } finally {
      clearTimeout(t);
    }
  }

  // ---------- Store factory ----------
  PM.createStore = function createStore({ apiBase } = {}) {
    // Auto-detect API base: prefer Comfy '/api/prompt_manager' when under /prompt_manager
    const inferredBase = (() => {
      try {
        const p = window.location.pathname || '';
        if (p.startsWith('/prompt_manager') || p.startsWith('/api/prompt_manager')) return '/api/prompt_manager';
      } catch {}
      return '/api';
    })();
    apiBase = apiBase || inferredBase;
    // local cache for simple memoization (by string key)
    const cache = new Map();

    return {
      // ----- state -----
      apiBase,
      busy: false,
      lastError: "",
      selection: new Set(), // selected prompt ids in dashboard
      prompts: [], // latest fetch results
      collections: [],
      settings: null,
      stats: null,

      // ----- utils -----
      clearSelection() {
        this.selection.clear();
      },
      toggleSelect(id) {
        this.selection.has(id)
          ? this.selection.delete(id)
          : this.selection.add(id);
      },

      // ----- SETTINGS -----
      async loadSettings() {
        try {
          this.busy = true;
          let s;
          try {
            s = await fetchJSON({ url: `${this.apiBase}/settings` });
          } catch {
            /* API optional at first */
          }
          this.settings =
            s ||
            JSON.parse(localStorage.getItem("pm.settings") || "null") ||
            DEFAULTS.settings;
          if (!s)
            localStorage.setItem("pm.settings", JSON.stringify(this.settings));
        } catch (e) {
          this.lastError = String(e);
          toast({
            variant: "danger",
            icon: "exclamation-triangle",
            text: `Load settings failed: ${this.lastError}`,
          });
        } finally {
          this.busy = false;
        }
      },
      async saveSettings(newSettings) {
        try {
          this.busy = true;
          this.settings = { ...(this.settings || {}), ...(newSettings || {}) };
          localStorage.setItem("pm.settings", JSON.stringify(this.settings));
          try {
            const method = (this.apiBase === '/prompt_manager') ? 'POST' : 'PUT';
            await fetchJSON({ method, url: `${this.apiBase}/settings`, body: this.settings });
          } catch {}
          toast({
            variant: "success",
            icon: "check2-circle",
            text: "Settings saved",
          });
        } catch (e) {
          this.lastError = String(e);
          toast({
            variant: "danger",
            icon: "exclamation-triangle",
            text: `Save settings failed: ${this.lastError}`,
          });
        } finally {
          this.busy = false;
        }
      },

      // ----- PROMPTS -----
      async getPrompts(params = {}) {
        const qs = new URLSearchParams();
        for (const [k, v] of Object.entries(params))
          if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
        const key = `prompts:${qs}`;
        if (cache.has(key)) return cache.get(key);
        this.busy = true;
        try {
          const data = await fetchJSON({ url: `${this.apiBase}/prompts?${qs.toString()}` });
          // Normalize shapes: array | {data:{prompts}} | {prompts}
          let rows;
          if (Array.isArray(data)) rows = data;
          else if (data && data.data && Array.isArray(data.data.prompts)) rows = data.data.prompts;
          else if (data && Array.isArray(data.prompts)) rows = data.prompts;
          else rows = [];
          this.prompts = rows;
          cache.set(key, rows);
          return rows;
        } catch (e) {
          this.lastError = String(e);
          toast({
            variant: "danger",
            icon: "exclamation-triangle",
            text: `Load prompts failed: ${this.lastError}`,
          });
          return [];
        } finally {
          this.busy = false;
        }
      },
      async createPrompt(p) {
        // Map field names when talking to Comfy plugin
        const body = (this.apiBase === '/prompt_manager')
          ? {
              prompt: p.positive || p.prompt || '',
              negative_prompt: p.negative || '',
              category: p.category,
              tags: p.tags,
              rating: p.rating,
              notes: p.notes,
            }
          : p;
        const data = await fetchJSON({ method: "POST", url: `${this.apiBase}/prompts`, body });
        toast({
          variant: "success",
          icon: "plus-circle",
          text: `Prompt created`,
        });
        cache.clear(); // invalidate
        // Normalize return
        if (data && data.data) return data.data;
        return data;
      },
      async updatePrompt(id, patch) {
        try {
          const body = (String(this.apiBase).includes('/prompt_manager'))
            ? {
                prompt: patch.positive || patch.prompt,
                negative_prompt: patch.negative,
                category: patch.category,
                tags: patch.tags,
                rating: patch.rating,
                notes: patch.notes,
              }
            : patch;
          const method = 'PATCH';
          const data = await fetchJSON({ method, url: `${this.apiBase}/prompts/${id}` , body });
          toast({ variant: "success", icon: "pencil", text: `Prompt #${id} updated` });
          cache.clear();
          if (data && data.data) return data.data;
          return data;
        } catch (e) {
          // Fallback to Flask API if available
          try {
            const fb = {};
            if (patch.positive || patch.prompt) fb.positive = patch.positive || patch.prompt;
            if (typeof patch.negative !== 'undefined') fb.negative = patch.negative;
            if (typeof patch.category !== 'undefined') fb.category = patch.category;
            if (typeof patch.tags !== 'undefined') fb.tags = patch.tags;
            if (typeof patch.rating !== 'undefined') fb.rating = patch.rating;
            const data2 = await fetchJSON({ method: 'PATCH', url: `/api/prompts/${id}`, body: fb });
            toast({ variant: "success", icon: "pencil", text: `Prompt #${id} updated` });
            cache.clear();
            if (data2 && data2.data) return data2.data;
            return data2;
          } catch (e2) {
            toast({ variant: 'warning', icon: 'exclamation-triangle', text: `Update not supported (${String(e2)})` });
            throw e2;
          }
        }
      },
      async deletePrompt(id) {
        try {
          await fetchJSON({ method: "DELETE", url: `${this.apiBase}/prompts/${id}` });
          toast({ variant: "primary", icon: "trash", text: `Prompt #${id} deleted` });
          cache.clear();
        } catch (e) {
          // Fallback to Flask
          try {
            await fetchJSON({ method: 'DELETE', url: `/api/prompts/${id}` });
            toast({ variant: "primary", icon: "trash", text: `Prompt #${id} deleted` });
            cache.clear();
          } catch (e2) {
            toast({ variant: 'warning', icon: 'exclamation-triangle', text: `Delete not supported (${String(e2)})` });
            throw e2;
          }
        }
      },

      // ----- COLLECTIONS -----
      async listCollections() {
        const key = "collections";
        if (cache.has(key)) return cache.get(key);
        const data = await fetchJSON({ url: `${this.apiBase}/collections` });
        this.collections = data;
        cache.set(key, data);
        return data;
      },
      async createCollection(col) {
        const data = await fetchJSON({
          method: "POST",
          url: `${this.apiBase}/collections`,
          body: col,
        });
        toast({
          variant: "success",
          icon: "bookmark-plus",
          text: `Collection "${data.name}" created`,
        });
        cache.delete("collections");
        return data;
      },
      async materializeCollection(id) {
        return fetchJSON({
          method: "POST",
          url: `${this.apiBase}/collections/${id}/materialize`,
        });
      },

      // ----- STATS -----
      async getStats({ range = "30d", gran = "day" } = {}) {
        const key = `stats:${range}:${gran}`;
        if (cache.has(key)) return cache.get(key);
        const data = await fetchJSON({
          url: `${this.apiBase}/stats?range=${range}&gran=${gran}`,
        });
        this.stats = data;
        cache.set(key, data);
        return data;
      },

      // ----- Misc -----
      async exportSelected() {
        const rows = this.prompts.filter((p) => this.selection.has(p.id));
        const blob = new Blob([rows.map((r) => JSON.stringify(r)).join("\n")], {
          type: "application/x-ndjson",
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "prompts.ndjson";
        a.click();
        URL.revokeObjectURL(url);
      },
    };
  };

  window.PM = PM;
})();
