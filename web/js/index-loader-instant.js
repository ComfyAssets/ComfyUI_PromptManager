/**
 * Instant Index Loader
 * With new stats implementation, no warming needed!
 */

(function() {
  'use strict';

  const DASHBOARD_URL = '/dashboard';
  const LOADER_MIN_MS = 500;  // Reduced from 1000
  const FALLBACK_MS = 3000;   // Reduced from 5000

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

  // Only ESSENTIAL initialization tasks
  const initTasks = [
    {
      key: 'check-migration',
      label: 'Checking database',
      run: async () => checkMigration(),
    },
    {
      key: 'verify-stats',
      label: 'Verifying stats table',
      run: async () => verifyStatsTable(),  // NEW: Just verify table exists
    },
    // REMOVED: warm-stats - NOT NEEDED with new implementation!
    {
      key: 'check-scheduler',
      label: 'Starting background tasks',
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
      return { meta: 'Database OK' };
    } catch (error) {
      console.warn('Migration check failed:', error);
      return { meta: 'Migration check skipped' };
    }
  }

  async function verifyStatsTable() {
    /**
     * NEW: Just verify stats table exists
     * No warming needed - stats are always ready!
     */
    updateStatus('Verifying stats…');
    try {
      // Quick check that stats endpoint works
      const response = await fetch('/api/v1/stats/overview');
      if (response.ok) {
        const data = await response.json();
        if (data?.success) {
          // Stats are instant now!
          console.log('Stats table verified - instant access ready');
          return { meta: 'Stats instant' };
        }
      }
      return { meta: 'Stats available' };
    } catch (error) {
      // Stats will work on-demand even if this fails
      console.debug('Stats check skipped:', error);
      return { meta: 'Stats check skipped' };
    }
  }

  async function checkScheduler() {
    updateStatus('Initializing services…');
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

    // Redirect immediately - no delays!
    setTimeout(triggerRedirect, 50);
  }

  async function runInitTasks() {
    const startTime = Date.now();
    const totalTasks = initTasks.length;
    let completed = 0;

    updateStatus('Starting…');
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
        // Continue with other tasks
        completed++;
      }
    }

    const elapsed = Date.now() - startTime;
    const remaining = Math.max(0, LOADER_MIN_MS - elapsed);

    if (remaining > 0) {
      updateStatus('Ready…');
      updateProgress(0.95);
      await new Promise(resolve => setTimeout(resolve, remaining));
    }

    completeAndRedirect();
  }

  // Start initialization
  document.addEventListener('DOMContentLoaded', () => {
    // Set fallback redirect (faster now)
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