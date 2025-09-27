/**
 * Optimized Index Loader
 * Removes blocking stats warming that causes 10-30 second delays
 */

(function() {
  'use strict';

  const DASHBOARD_URL = '/dashboard';
  const LOADER_MIN_MS = 1000;
  const FALLBACK_MS = 5000;

  const progressBar = document.getElementById('progress');
  const statusText = document.getElementById('status-text');
  let redirected = false;
  let fallbackTimer = null;

  function updateProgress(ratio) {
    if (progressBar) {
      progressBar.style.width = `${Math.round(ratio * 100)}%`;
    }
  }

  function updateStatus(message) {
    if (statusText) {
      statusText.textContent = message;
    }
  }

  // Critical initialization tasks only
  const initTasks = [
    {
      key: 'check-migration',
      label: 'Checking database schema',
      run: async () => checkMigration(),
    },
    {
      key: 'scan-thumbnails',
      label: 'Scanning thumbnail directories',
      run: async () => scanThumbnails(),
    },
    // REMOVED: warm-stats task that was causing 10-30 second delays
    {
      key: 'check-scheduler',
      label: 'Initializing background tasks',
      run: async () => checkScheduler(),
    },
  ];

  async function checkMigration() {
    updateStatus('Checking database version…');
    try {
      const response = await fetch('/api/v1/migration/info');
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
      }

      const info = await response.json();
      if (info?.data?.status === 'migration_required') {
        sessionStorage.setItem('promptmanager.migration.required', 'true');
        window.location.href = '/migration';
        return;
      }
      return { meta: 'Database schema OK' };
    } catch (error) {
      console.warn('Migration check failed:', error);
      return { meta: 'Migration check skipped' };
    }
  }

  async function scanThumbnails() {
    updateStatus('Preparing thumbnails…');
    try {
      const response = await fetch('/api/v1/thumbnails/scan-directories');
      if (!response.ok) {
        console.warn(`Thumbnail scan returned ${response.status}`);
        return { meta: 'Thumbnail scan skipped' };
      }
      const data = await response.json();
      return { meta: `Found ${data?.data?.total || 0} thumbnails` };
    } catch (error) {
      console.warn('Thumbnail scan failed:', error);
      throw new Error('Thumbnail scan failed');
    }
  }

  async function checkScheduler() {
    updateStatus('Starting background services…');
    try {
      const response = await fetch('/api/v1/scheduler/status');
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
      }

      const data = await response.json();
      const running = data?.data?.running || false;
      return { meta: running ? 'Scheduler active' : 'Scheduler inactive' };
    } catch (error) {
      console.warn('Scheduler check failed:', error);
      return { meta: 'Scheduler check skipped' };
    }
  }

  /**
   * OPTIMIZED: Stats warming moved to background
   * This now happens AFTER page load, not blocking the user
   */
  function warmStatsInBackground() {
    // Delay stats warming to after page is interactive
    setTimeout(async () => {
      try {
        // Check if stats are already cached
        const lastWarm = sessionStorage.getItem('promptmanager.statsWarmAt');
        if (lastWarm) {
          const warmTime = new Date(lastWarm).getTime();
          const now = Date.now();
          // Skip if warmed within last 5 minutes
          if (now - warmTime < 300000) {
            console.log('Stats already warm, skipping');
            return;
          }
        }

        // Warm stats in background (non-blocking)
        const response = await fetch('/api/v1/stats/overview');
        if (response.ok) {
          const payload = await response.json();
          if (payload?.success) {
            sessionStorage.setItem(
              'promptmanager.statsWarmAt',
              new Date().toISOString()
            );
            console.log('Stats warmed successfully in background');
          }
        }
      } catch (error) {
        // Silent fail - stats will be loaded on-demand
        console.debug('Background stats warming skipped:', error);
      }
    }, 2000); // Wait 2 seconds after page load
  }

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

    // Start background stats warming (non-blocking)
    warmStatsInBackground();

    // Redirect immediately
    setTimeout(triggerRedirect, 100);
  }

  async function runInitTasks() {
    const startTime = Date.now();
    const totalTasks = initTasks.length;
    let completed = 0;

    updateStatus('Initializing…');
    updateProgress(0);

    for (const task of initTasks) {
      try {
        updateStatus(task.label);
        updateProgress(completed / totalTasks);

        const result = await task.run();
        console.log(`✓ ${task.key}:`, result?.meta || 'complete');

        completed++;
      } catch (error) {
        console.error(`✗ ${task.key}:`, error);
        // Continue with other tasks even if one fails
        completed++;
      }
    }

    const elapsed = Date.now() - startTime;
    const remaining = Math.max(0, LOADER_MIN_MS - elapsed);

    if (remaining > 0) {
      updateStatus('Almost ready…');
      updateProgress(0.95);
      await new Promise(resolve => setTimeout(resolve, remaining));
    }

    completeAndRedirect();
  }

  // Start initialization
  document.addEventListener('DOMContentLoaded', () => {
    // Set fallback redirect
    fallbackTimer = setTimeout(() => {
      console.log('Fallback redirect triggered');
      triggerRedirect();
    }, FALLBACK_MS);

    // Run initialization tasks
    runInitTasks().catch(error => {
      console.error('Init failed:', error);
      triggerRedirect();
    });
  });
})();