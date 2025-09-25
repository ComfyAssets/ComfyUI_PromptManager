/**
 * Legacy alias that delegates to the current ScanManager implementation.
 * @module ScanManagerV2
 */
const ScanManagerV2 = (function() {
  'use strict';

  if (typeof window === 'undefined') {
    return {};
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] scan-manager-v2 skipped outside PromptManager UI context');
    return {};
  }

  if (window.ScanManager) {
    console.warn('[ScanManagerV2] Delegating to ScanManager');
    return window.ScanManager;
  }

  console.warn('[ScanManagerV2] ScanManager not available; returning no-op object');
  return {
    init() {
      console.warn('[ScanManagerV2] init called but ScanManager is unavailable');
    }
  };
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = ScanManagerV2;
}

if (typeof window !== 'undefined') {
  window.ScanManagerV2 = ScanManagerV2;
}
