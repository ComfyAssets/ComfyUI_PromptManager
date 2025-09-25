/**
 * Scan Manager Module
 * Handles scanning ComfyUI output directory for images with prompts
 * @module ScanManager
 */
const ScanManager = (function() {
  'use strict';

  function createStub() {
    const target = {};
    const stub = new Proxy(target, {
      get: (obj, prop) => {
        if (prop in obj) {
          return obj[prop];
        }
        return (...args) => {
          if (prop === 'scan' || prop === 'rescan') {
            return Promise.resolve({ missing: 0 });
          }
          return stub;
        };
      },
      set: (obj, prop, value) => {
        obj[prop] = value;
        return true;
      },
    });
    return stub;
  }

  if (typeof window === 'undefined') {
    return createStub();
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] scan manager skipped outside PromptManager UI context');
    return createStub();
  }

  // Configuration
  const config = {
    apiEndpoint: '/api/scan'
  };

  // State
  let state = {
    isScanning: false,
    abortController: null,
    unsubscribe: null,
    progressId: null,  // Progress indicator ID
    stats: {
      processed: 0,
      found: 0,
      added: 0,
      linked: 0
    }
  };

  // DOM Elements cache
  let elements = {};

  /**
   * Cache DOM elements for performance
   */
  function cacheElements() {
    elements = {
      modal: document.getElementById('scanModal'),
      startBtn: document.getElementById('startScanBtn'),
      cancelBtn: document.getElementById('cancelScanBtn'),
      progressSection: document.getElementById('scanProgress'),
      progressBar: document.getElementById('scanProgressBar'),
      statusText: document.getElementById('scanStatusText'),
      countText: document.getElementById('scanCount'),
      foundText: document.getElementById('scanFound'),
      resultsSection: document.getElementById('scanResults'),
      resultFilesScanned: document.getElementById('resultFilesScanned'),
      resultPromptsFound: document.getElementById('resultPromptsFound'),
      resultPromptsAdded: document.getElementById('resultPromptsAdded'),
      resultImagesLinked: document.getElementById('resultImagesLinked')
    };
  }

  /**
   * Show the scan modal
   */
  function showModal() {
    cacheElements();
    if (elements.modal) {
      elements.modal.style.display = 'flex';
      resetUI();
    }
  }

  /**
   * Hide the scan modal
   */
  function hideModal() {
    if (elements.modal) {
      elements.modal.style.display = 'none';
      // Stop any ongoing scan
      if (state.isScanning) {
        stopScan();
      }
    }
  }

  /**
   * Reset UI to initial state
   */
  function resetUI() {
    if (elements.progressSection) {
      elements.progressSection.style.display = 'none';
    }
    if (elements.resultsSection) {
      elements.resultsSection.style.display = 'none';
    }
    if (elements.progressBar) {
      elements.progressBar.style.width = '0%';
    }
    if (elements.statusText) {
      elements.statusText.textContent = 'Preparing...';
    }
    if (elements.countText) {
      elements.countText.textContent = '0 files processed';
    }
    if (elements.foundText) {
      elements.foundText.textContent = '0 prompts found';
    }

    // Reset button states
    if (elements.startBtn) {
      elements.startBtn.disabled = false;
      elements.startBtn.innerHTML = '<i class="fa-solid fa-play"></i> Start Scan';
    }
    if (elements.cancelBtn) {
      elements.cancelBtn.textContent = 'Cancel';
    }

    // Reset stats
    state.stats = {
      processed: 0,
      found: 0,
      added: 0,
      linked: 0
    };
  }

  /**
   * Start the scanning process
   */
  async function startScan() {
    if (state.isScanning) return;

    state.isScanning = true;

    // Update UI
    if (elements.progressSection) {
      elements.progressSection.style.display = 'block';
    }
    if (elements.startBtn) {
      elements.startBtn.disabled = true;
      elements.startBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Scanning...';
    }

    // Start progress indicator if available
    if (window.ProgressIndicator) {
      state.progressId = ProgressIndicator.start({
        title: 'Scanning Images',
        description: 'Scanning ComfyUI output directory...',
        cancelable: true,
        onCancel: () => stopScan()
      });
    }

    try {
      if (window.EventBus) {
        state.unsubscribe = EventBus.on('sse:progress', (event) => {
          if (!event || event.operation !== 'scan') {
            return;
          }
          handleProgressUpdate(event);
        });
      }

      state.abortController = new AbortController();

      const response = await fetch(config.apiEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({}),
        signal: state.abortController.signal
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || 'Scan failed');
      }

      const data = await response.json();
      handleScanComplete({
        files_scanned: data.files_scanned,
        prompts_found: data.prompts_found,
        prompts_added: data.prompts_added,
        images_linked: data.images_linked
      });

    } catch (error) {
      console.error('Failed to start scan:', error);
      handleScanError('Failed to start scan. Please try again.');
    }
  }

  /**
   * Stop the scanning process
   */
  function stopScan() {
    state.isScanning = false;

    if (state.unsubscribe) {
      state.unsubscribe();
      state.unsubscribe = null;
    }

    if (state.abortController) {
      state.abortController.abort();
      state.abortController = null;
    }

    // Cancel progress indicator if exists
    if (window.ProgressIndicator && state.progressId) {
      ProgressIndicator.cancel(state.progressId);
      state.progressId = null;
    }

    // Update UI
    if (elements.startBtn) {
      elements.startBtn.disabled = false;
      elements.startBtn.innerHTML = '<i class="fa-solid fa-play"></i> Start Scan';
    }
  }

  /**
   * Handle progress updates from server
   */
  function handleProgressUpdate(event) {
    if (!event || event.operation !== 'scan') {
      return;
    }

    const payload = event.stats || event;
    updateProgress({
      progress: event.progress,
      status: event.message || event.status,
      processed: payload.processed,
      found: payload.found,
      added: payload.added,
      linked: payload.linked
    });
  }

  /**
   * Update progress display
   */
  function updateProgress(data) {
    // Update progress bar
    if (elements.progressBar && typeof data.progress === 'number') {
      elements.progressBar.style.width = `${data.progress}%`;
    }

    // Update status text
    if (elements.statusText && data.status) {
      elements.statusText.textContent = data.status;
    }

    // Update progress indicator if available
    if (window.ProgressIndicator && state.progressId && typeof data.progress === 'number') {
      ProgressIndicator.update(state.progressId, data.progress, {
        description: data.status || 'Scanning...'
      });
    }

    // Update counts
    if (typeof data.processed === 'number') {
      state.stats.processed = data.processed;
      if (elements.countText) {
        elements.countText.textContent = `${data.processed} files processed`;
      }
    }

    if (typeof data.found === 'number') {
      state.stats.found = data.found;
      if (elements.foundText) {
        elements.foundText.textContent = `${data.found} prompts found`;
      }
    }

    // Store additional stats if provided
    if (typeof data.added === 'number') {
      state.stats.added = data.added;
    }
    if (typeof data.linked === 'number') {
      state.stats.linked = data.linked;
    }
  }

  /**
   * Handle scan completion
   */
  function handleScanComplete(data) {
    if (data.processed !== undefined) state.stats.processed = data.processed;
    if (data.files_scanned !== undefined) state.stats.processed = data.files_scanned;
    if (data.found !== undefined) state.stats.found = data.found;
    if (data.prompts_found !== undefined) state.stats.found = data.prompts_found;
    if (data.added !== undefined) state.stats.added = data.added;
    if (data.prompts_added !== undefined) state.stats.added = data.prompts_added;
    if (data.linked !== undefined) state.stats.linked = data.linked;
    if (data.images_linked !== undefined) state.stats.linked = data.images_linked;

    // Complete progress indicator if available
    if (window.ProgressIndicator && state.progressId) {
      ProgressIndicator.complete(state.progressId, {
        style: ProgressIndicator.STYLES.SUCCESS
      });
      state.progressId = null;
    }

    stopScan();

    // Update progress to 100%
    if (elements.progressBar) {
      elements.progressBar.style.width = '100%';
    }
    if (elements.statusText) {
      elements.statusText.textContent = 'Scan complete!';
    }

    // Show results
    showResults();

    // Update button
    if (elements.cancelBtn) {
      elements.cancelBtn.textContent = 'Close';
    }

    // Refresh gallery if available
    if (window.refreshGallery && typeof window.refreshGallery === 'function') {
      setTimeout(() => {
        window.refreshGallery();
      }, 1000);
    }
  }

  /**
   * Show scan results
   */
  function showResults() {
    if (!elements.resultsSection) return;

    elements.resultsSection.style.display = 'block';

    // Update result values
    if (elements.resultFilesScanned) {
      elements.resultFilesScanned.textContent = state.stats.processed;
    }
    if (elements.resultPromptsFound) {
      elements.resultPromptsFound.textContent = state.stats.found;
    }
    if (elements.resultPromptsAdded) {
      elements.resultPromptsAdded.textContent = state.stats.added;
    }
    if (elements.resultImagesLinked) {
      elements.resultImagesLinked.textContent = state.stats.linked;
    }
  }

  /**
   * Handle scan error
   */
  function handleScanError(message) {
    // Show error in progress indicator if available
    if (window.ProgressIndicator && state.progressId) {
      ProgressIndicator.error(state.progressId, message);
      state.progressId = null;
    }

    stopScan();

    // Update UI
    if (elements.statusText) {
      elements.statusText.textContent = 'Error: ' + message;
      elements.statusText.style.color = '#ef4444';
    }

    // Show notification
    if (window.NotificationService) {
      window.NotificationService.show('Scan failed: ' + message, 'error');
    }

    // Reset button
    if (elements.startBtn) {
      elements.startBtn.disabled = false;
      elements.startBtn.innerHTML = '<i class="fa-solid fa-play"></i> Retry Scan';
    }
  }

  /**
   * Initialize the module
   */
  function init(options = {}) {
    Object.assign(config, options);

    // Attach event listeners
    document.addEventListener('click', function(e) {
      const action = e.target.closest('[data-action]')?.dataset.action;

      switch(action) {
        case 'scan-images':
          showModal();
          break;
        case 'start-scan':
          startScan();
          break;
        case 'cancel-scan':
          if (state.isScanning) {
            stopScan();
            hideModal();
          } else {
            hideModal();
          }
          break;
        case 'close-scan-modal':
          hideModal();
          break;
      }
    });

    // Also handle backdrop clicks
    document.addEventListener('click', function(e) {
      if (e.target.classList.contains('modal-backdrop')) {
        const modal = e.target.closest('.modal');
        if (modal && modal.id === 'scanModal') {
          hideModal();
        }
      }
    });

    console.log('ScanManager initialized');
    return this;
  }

  // Public API
  return {
    init: init,
    showModal: showModal,
    hideModal: hideModal,
    startScan: startScan,
    stopScan: stopScan,
    isScanning: () => state.isScanning
  };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
  module.exports = ScanManager;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
  window.ScanManager = ScanManager;
}
