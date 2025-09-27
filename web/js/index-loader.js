(function () {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] index-loader skipped outside PromptManager UI context');
    return;
  }

  const DASHBOARD_URL = '/prompt_manager/dashboard';
  const MIN_DISPLAY_MS = 1200;
  const FALLBACK_REDIRECT_MS = 10000;
  const STEP_ICONS = {
    pending: '○',
    active: '●',
    done: '✔',
    error: '⚠',
  };

  const loaderTasks = [
    {
      key: 'initialize',
      label: 'Initializing environment',
      run: async () => {
        updateStatus('Initializing environment…');
        await delay(120);
        return { meta: 'Core services ready' };
      },
    },
    {
      key: 'check-migrations',
      label: 'Checking database & migrations',
      run: async () => {
        updateStatus('Checking database & migrations…');
        if (window.MigrationService && typeof window.MigrationService.prefetch === 'function') {
          await window.MigrationService.prefetch();
          return { meta: 'Migration status cached' };
        }
        return { meta: 'Migration service unavailable, continuing' };
      },
    },
    {
      key: 'scan-thumbnails',
      label: 'Scanning for missing thumbnails',
      run: async () => scanThumbnails(),
    },
    // REMOVED: Stats warming not needed with new instant stats implementation!
    // Stats are now always ready in the database table
    {
      key: 'finalize',
      label: 'Preparing dashboard',
      run: async () => {
        updateStatus('Preparing dashboard…');
        await delay(250);
        return { meta: 'Routing to dashboard' };
      },
    },
  ];

  let startTime = 0;
  let redirected = false;
  let fallbackTimer = null;
  let progressBar;
  let statusNode;
  let stepsList;
  const stepElements = new Map();

  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function updateStatus(text) {
    if (statusNode) {
      statusNode.textContent = text;
    }
  }

  function updateProgress(ratio) {
    if (!progressBar) {
      return;
    }
    const percentage = Math.max(0, Math.min(1, ratio)) * 100;
    progressBar.style.width = `${percentage}%`;
  }

  function setStepState(key, status, meta) {
    const refs = stepElements.get(key);
    if (!refs) {
      return;
    }
    refs.element.dataset.status = status;
    refs.icon.textContent = STEP_ICONS[status] || STEP_ICONS.pending;

    if (meta) {
      refs.meta.textContent = meta;
      refs.meta.style.display = 'block';
    } else {
      refs.meta.textContent = '';
      refs.meta.style.display = 'none';
    }
  }

  function renderSteps() {
    if (!stepsList) {
      return;
    }

    stepsList.innerHTML = '';
    stepElements.clear();

    loaderTasks.forEach((task) => {
      const stepItem = document.createElement('li');
      stepItem.className = 'loader-step';
      stepItem.dataset.stepKey = task.key;
      stepItem.dataset.status = 'pending';

      const icon = document.createElement('span');
      icon.className = 'loader-step__icon';
      icon.textContent = STEP_ICONS.pending;

      const content = document.createElement('div');
      content.className = 'loader-step__content';

      const label = document.createElement('span');
      label.className = 'loader-step__label';
      label.textContent = task.label;

      const meta = document.createElement('span');
      meta.className = 'loader-step__meta';
      meta.style.display = 'none';

      content.appendChild(label);
      content.appendChild(meta);

      stepItem.appendChild(icon);
      stepItem.appendChild(content);

      stepsList.appendChild(stepItem);
      stepElements.set(task.key, { element: stepItem, icon, meta });
    });
  }

  async function scanThumbnails() {
    updateStatus('Scanning for missing thumbnails…');
    const sizes = ['small', 'medium', 'large'];

    try {
      const response = await fetch('/api/v1/thumbnails/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sizes }),
      });

      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
      }

      const result = await response.json();
      if (result.missing_count > 0) {
        sessionStorage.setItem(
          'thumbnailScanResult',
          JSON.stringify({
            missing_count: result.missing_count,
            total_operations: result.total_operations || result.missing_count * sizes.length,
            scanned_at: new Date().toISOString(),
          }),
        );
        return { meta: `${result.missing_count} images queued for thumbnails` };
      }

      sessionStorage.removeItem('thumbnailScanResult');
      return { meta: 'No missing thumbnails detected' };
    } catch (error) {
      console.warn('Thumbnail scan failed:', error);
      throw new Error('Thumbnail scan failed');
    }
  }

  // REMOVED: warmStats() function - no longer needed with instant stats
  // The new stats implementation uses a persistent database table that's always ready
  // Stats load instantly (<10ms) without any warming or calculation needed

  function redirectToDashboard() {
    window.location.href = DASHBOARD_URL;
  }

  function triggerRedirect() {
    if (redirected) {
      return;
    }
    redirected = true;
    redirectToDashboard();
  }

  function completeAndRedirect() {
    clearTimeout(fallbackTimer);
    updateStatus('Ready!');
    updateProgress(1);
    const elapsed = performance.now() - startTime;
    const remaining = Math.max(0, MIN_DISPLAY_MS - elapsed);
    setTimeout(triggerRedirect, remaining);
  }

  async function runTasks() {
    const total = loaderTasks.length;

    for (let index = 0; index < loaderTasks.length; index += 1) {
      const task = loaderTasks[index];
      setStepState(task.key, 'active');
      updateProgress(index / total);

      try {
        const result = await task.run();
        const meta = result && typeof result.meta === 'string' ? result.meta : '';
        setStepState(task.key, 'done', meta);
      } catch (error) {
        const message = (error && error.message) ? error.message : 'Step failed';
        setStepState(task.key, 'error', message);
      }

      updateProgress((index + 1) / total);
    }

    completeAndRedirect();
  }

  function initializeLoader() {
    startTime = performance.now();

    const container = document.querySelector('.loader-container');
    if (!container) {
      triggerRedirect();
      return;
    }

    progressBar = container.querySelector('.loader-progress-bar');
    statusNode = container.querySelector('.status-text');
    stepsList = container.querySelector('[data-loader-steps]');

    renderSteps();
    fallbackTimer = setTimeout(triggerRedirect, FALLBACK_REDIRECT_MS);
    runTasks().catch((error) => {
      console.error('Loader failed:', error);
      triggerRedirect();
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    initializeLoader();
  });
})();
