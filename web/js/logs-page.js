(function () {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] logs-page skipped outside PromptManager UI context');
    return;
  }

  const PRIMARY_LOG_ENDPOINT = '/api/v1/logs';
  const API_BASE_CANDIDATES = [
    PRIMARY_LOG_ENDPOINT.replace(/\/logs$/, ''),
    '/api/prompt_manager',
  ];
  let resolvedApiBase = null;

  function normalizeBase(base) {
    return base.endsWith('/') ? base.slice(0, -1) : base;
  }

  function normalizePath(path) {
    if (!path) {
      return '';
    }
    return path.startsWith('/') ? path : `/${path}`;
  }

  function buildUrl(base, path, params) {
    const normalizedBase = normalizeBase(base);
    const normalizedPath = normalizePath(path);
    const url = `${normalizedBase}${normalizedPath}`;

    if (!params) {
      return url;
    }

    const search = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') {
        return;
      }
      search.set(key, String(value));
    });

    const queryString = search.toString();
    return queryString ? `${url}?${queryString}` : url;
  }

  async function fetchJson(path, options = {}) {
    const { method = 'GET', params = null, body } = options;
    const bases = resolvedApiBase
      ? [resolvedApiBase, ...API_BASE_CANDIDATES.filter((base) => base !== resolvedApiBase)]
      : API_BASE_CANDIDATES.slice();

    let lastError = null;

    for (const candidate of bases) {
      const url = buildUrl(candidate, path, params);
      const headers = {};
      const fetchOptions = { method, headers };

      if (method !== 'GET' && body !== undefined) {
        headers['Content-Type'] = 'application/json';
        fetchOptions.body = JSON.stringify(body);
      }

      try {
        const response = await fetch(url, fetchOptions);
        if (!response.ok) {
          const err = new Error(`${response.status} ${response.statusText}`);
          err.status = response.status;
          throw err;
        }
        const payload = await response.json();
        resolvedApiBase = candidate;
        return payload;
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError || new Error('Unable to reach PromptManager logs API');
  }

  function buildDownloadUrl(filename) {
    const base = resolvedApiBase || API_BASE_CANDIDATES[0];
    return buildUrl(base, `/logs/download/${encodeURIComponent(filename)}`);
  }

  function notify(kind, message) {
    if (window.notificationService && typeof window.notificationService[kind] === 'function') {
      window.notificationService[kind](message);
    } else if (typeof window.showToast === 'function') {
      window.showToast(message, kind);
    }
  }

  function rebuildOptions(select, files, active) {
    if (!select) {
      return;
    }
    select.innerHTML = '';

    files.forEach((file) => {
      const option = document.createElement('option');
      option.value = file.name;
      option.textContent = file.name;
      if (file.name === active) {
        option.selected = true;
      }
      select.appendChild(option);
    });
  }

  function ensureArray(value) {
    if (Array.isArray(value)) {
      return value;
    }
    return [];
  }

  window.pmLogsApp = function () {
    return {
      file: 'promptmanager.log',
      tail: 500,
      auto: false,
      timer: null,
      files: [],
      content: '',
      async init() {
        await this.refresh(true);
        if (this.auto) {
          this.toggleAuto();
        }
      },
      changeFile(value) {
        this.file = value;
        this.refresh();
      },
      toggleAuto() {
        if (this.timer) {
          clearInterval(this.timer);
          this.timer = null;
        }
        if (this.auto) {
          this.timer = setInterval(() => this.refresh(), 2000);
        }
      },
      async refresh(initial = false) {
        try {
          const payload = await fetchJson('/logs', {
            params: {
              file: this.file,
              tail: typeof this.tail === 'number' ? this.tail : Number(this.tail) || 0,
            },
          });

          if (!payload || payload.success === false) {
            const message = payload && payload.error ? payload.error : 'Failed to load logs';
            throw new Error(message);
          }

          this.files = ensureArray(payload.files);
          const active = payload.active || (this.files[0] && this.files[0].name) || this.file || '';
          this.file = active;
          this.content = payload.content || '';

          const select = document.getElementById('fileSel');
          rebuildOptions(select, this.files, this.file);
        } catch (error) {
          console.warn('Failed to load logs', error);
          this.content = '(failed to load logs)';
        }
      },
      async rotate() {
        try {
          const payload = await fetchJson('/logs/rotate', { method: 'POST' });
          if (payload && payload.success) {
            notify('success', 'Log rotated');
          } else {
            notify('warning', (payload && payload.error) || 'Unable to rotate logs');
          }
        } catch (error) {
          console.warn('Failed to rotate logs', error);
          notify('warning', 'Unable to rotate logs');
        }
        await this.refresh();
      },
      async clearLogs() {
        try {
          const payload = await fetchJson('/logs/clear', { method: 'POST' });
          if (payload && payload.success) {
            notify('success', 'Logs cleared');
          } else {
            notify('warning', (payload && payload.error) || 'Unable to clear logs');
          }
        } catch (error) {
          console.warn('Failed to clear logs', error);
          notify('warning', 'Unable to clear logs');
        }
        await this.refresh(true);
      },
      download() {
        if (!this.file) {
          notify('warning', 'No log file selected');
          return;
        }
        const link = document.createElement('a');
        link.href = buildDownloadUrl(this.file);
        link.download = this.file;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      },
    };
  };
})();
