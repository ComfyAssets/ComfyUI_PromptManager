/**
 * Real-time Updates Service
 * Manages SSE connections for live updates across the application
 * @module RealtimeUpdates
 */
const RealtimeUpdates = (function() {
  'use strict';

  function createStub() {
    const target = {};
    const stub = new Proxy(target, {
      get: (obj, prop) => {
        if (prop in obj) {
          return obj[prop];
        }
        return (...args) => {
          if (prop === 'subscribe' || prop === 'connectStream') {
            return { unsubscribe: () => {} };
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
    console.info('[PromptManager] realtime updates skipped outside PromptManager UI context');
    return createStub();
  }

  // Configuration
  const config = {
    reconnectOptions: {
      autoReconnect: true,
      reconnectDelay: 2000,
      maxReconnectAttempts: 5
    }
  };

  // Active streams
  const streams = new Map();

  // Subscription callbacks
  const subscriptions = new Map();

  /**
   * Initialize the realtime updates service
   */
  function init(options = {}) {
    Object.assign(config, options);

    if (!window.SSEClient) {
      console.warn('[RealtimeUpdates] SSEClient/WebSocket bridge not available');
      return this;
    }

    SSEClient.on('visibility:visible', handleVisibilityRestore);
    SSEClient.on('visibility:hidden', handleVisibilityHidden);

    initializeCoreStreams();

    return this;
  }

  /**
   * Initialize core SSE streams
   */
  function initializeCoreStreams() {
    // Gallery updates stream
    createGalleryStream();

    // Notifications stream
    createNotificationStream();

    // System status stream
    createSystemStream();
  }

  /**
   * Create gallery updates stream
   */
  function createGalleryStream() {
    const stream = SSEClient.createStream({
      events: {
        'prompt_manager.gallery.image_added': handleNewImage,
        'prompt_manager.gallery.image_deleted': handleImageDeleted,
        'prompt_manager.gallery.image_updated': handleImageUpdated,
        'prompt_manager.gallery.refresh': handleGalleryRefresh,
        connected: () => console.log('Gallery stream connected'),
        disconnected: () => console.log('Gallery stream disconnected'),
        reconnecting: (data) => console.log('Gallery stream reconnecting', data),
        error: (error) => console.error('Gallery stream error:', error)
      },
      options: {
        key: 'gallery-updates'
      }
    });

    stream.start().catch((error) => console.error('Failed to start gallery stream', error));
    streams.set('gallery', stream);
  }

  /**
   * Create notifications stream
   */
  function createNotificationStream() {
    const stream = SSEClient.createStream({
      events: {
        'prompt_manager.notifications.notification': handleNotification,
        'prompt_manager.notifications.toast': handleToastNotification,
        'prompt_manager.notifications.progress': handleProgressStream,
        connected: () => console.log('Notification stream connected'),
        disconnected: () => console.log('Notification stream disconnected'),
        reconnecting: (data) => console.log('Notification stream reconnecting', data),
        error: (error) => console.error('Notification stream error:', error)
      },
      options: {
        key: 'notifications'
      }
    });

    stream.start().catch((error) => console.error('Failed to start notification stream', error));
    streams.set('notifications', stream);
  }

  /**
   * Create system status stream
   */
  function createSystemStream() {
    const stream = SSEClient.createStream({
      events: {
        'prompt_manager.system.status': handleSystemStatus,
        'prompt_manager.system.maintenance': handleMaintenanceMode,
        connected: () => console.log('System stream connected'),
        disconnected: () => console.log('System stream disconnected'),
        reconnecting: (data) => console.log('System stream reconnecting', data),
        error: (error) => console.error('System stream error:', error)
      },
      options: {
        key: 'system-status'
      }
    });

    stream.start().catch((error) => console.error('Failed to start system stream', error));
    streams.set('system', stream);
  }

  /**
   * Start a specific stream
   */
  function startStream(name) {
    const stream = streams.get(name);
    if (stream) {
      stream.start().then(() => {
        console.log(`Started ${name} stream`);
        notifySubscribers('stream:started', { stream: name });
      }).catch(error => {
        console.error(`Failed to start ${name} stream:`, error);
        notifySubscribers('stream:error', { stream: name, error });
      });
    }
    return this;
  }

  /**
   * Stop a specific stream
   */
  function stopStream(name) {
    const stream = streams.get(name);
    if (stream) {
      stream.stop();
      console.log(`Stopped ${name} stream`);
      notifySubscribers('stream:stopped', { stream: name });
    }
    return this;
  }

  /**
   * Start all streams
   */
  function startAll() {
    streams.forEach((stream, name) => {
      startStream(name);
    });
    return this;
  }

  /**
   * Stop all streams
   */
  function stopAll() {
    streams.forEach((stream, name) => {
      stopStream(name);
    });
    return this;
  }

  // Event Handlers

  /**
   * Handle new image added to gallery
   */
  function handleNewImage(data) {
    console.log('New image added:', data);

    // Update gallery if it's active
    if (window.Gallery && window.Gallery.isActive()) {
      window.Gallery.addImage(data);
    }

    // Show notification
    if (window.ToastManager) {
      ToastManager.info('New image generated', {
        duration: 3000,
        action: {
          text: 'View',
          callback: () => window.Gallery.scrollToImage(data.id)
        }
      });
    }

    notifySubscribers('image:added', data);
  }

  /**
   * Handle image deleted
   */
  function handleImageDeleted(data) {
    console.log('Image deleted:', data);

    // Update gallery if it's active
    if (window.Gallery && window.Gallery.isActive()) {
      window.Gallery.removeImage(data.id);
    }

    notifySubscribers('image:deleted', data);
  }

  /**
   * Handle image updated
   */
  function handleImageUpdated(data) {
    console.log('Image updated:', data);

    // Update gallery if it's active
    if (window.Gallery && window.Gallery.isActive()) {
      window.Gallery.updateImage(data.id, data);
    }

    notifySubscribers('image:updated', data);
  }

  /**
   * Handle gallery refresh request
   */
  function handleGalleryRefresh(data) {
    console.log('Gallery refresh requested:', data);

    // Refresh gallery if it's active
    if (window.Gallery && window.Gallery.isActive()) {
      window.Gallery.refresh();
    }

    notifySubscribers('gallery:refresh', data);
  }

  /**
   * Handle notification
   */
  function handleNotification(data) {
    console.log('Notification received:', data);

    // Display notification based on type
    const { type = 'info', title, message, duration = 5000, actions } = data;

    if (window.ToastManager) {
      ToastManager[type](message, {
        title,
        duration,
        actions
      });
    }

    notifySubscribers('notification', data);
  }

  /**
   * Handle toast notification
   */
  function handleToastNotification(data) {
    if (window.ToastManager) {
      const { type = 'info', message, options = {} } = data;
      ToastManager[type](message, options);
    }

    notifySubscribers('toast', data);
  }

  /**
   * Handle realtime progress updates
   */
  function handleProgressStream(data) {
    if (!data) {
      return;
    }

    notifySubscribers('progress', data);
  }

  /**
   * Handle alert notification
   */
  function handleAlertNotification(data) {
    const { title, message, type = 'info' } = data;

    // Show modal alert if available
    if (window.Modal) {
      Modal.alert({
        title,
        content: message,
        type
      });
    } else {
      // Fallback to native alert
      alert(`${title}\n\n${message}`);
    }

    notifySubscribers('alert', data);
  }

  /**
   * Handle system status update
   */
  function handleSystemStatus(data) {
    console.log('System status:', data);

    // Update status indicator if available
    if (window.StatusIndicator) {
      window.StatusIndicator.update(data);
    }

    notifySubscribers('system:status', data);
  }

  /**
   * Handle system stats update
   */
  function handleSystemStats(data) {
    console.log('System stats:', data);

    // Update dashboard if active
    if (window.Dashboard && window.Dashboard.isActive()) {
      window.Dashboard.updateStats(data);
    }

    notifySubscribers('system:stats', data);
  }

  /**
   * Handle maintenance mode
   */
  function handleMaintenanceMode(data) {
    console.warn('Maintenance mode:', data);

    const { enabled, message, estimatedTime } = data;

    if (enabled) {
      // Show maintenance banner
      if (window.Banner) {
        window.Banner.show({
          type: 'warning',
          message: message || 'System is under maintenance',
          dismissible: false,
          id: 'maintenance'
        });
      }

      // Disable certain features
      disableFeaturesDuringMaintenance();
    } else {
      // Hide maintenance banner
      if (window.Banner) {
        window.Banner.hide('maintenance');
      }

      // Re-enable features
      enableFeaturesAfterMaintenance();
    }

    notifySubscribers('system:maintenance', data);
  }

  /**
   * Handle page visibility restored
   */
  function handleVisibilityRestore() {
    console.log('Page visibility restored, checking streams...');

    // Restart streams that were active
    streams.forEach((stream, name) => {
      const status = stream.getStatus();
      if (status.status === 'disconnected') {
        console.log(`Restarting ${name} stream after visibility restore`);
        startStream(name);
      }
    });
  }

  /**
   * Handle page visibility hidden
   */
  function handleVisibilityHidden() {
    console.log('Page hidden, streams will pause');

    // Could optionally stop non-critical streams here to save resources
    // For now, we'll let them continue but they may timeout
  }

  /**
   * Disable features during maintenance
   */
  function disableFeaturesDuringMaintenance() {
    // Disable upload buttons
    document.querySelectorAll('.upload-btn, .generate-btn').forEach(btn => {
      btn.disabled = true;
      btn.dataset.originalTitle = btn.title;
      btn.title = 'Disabled during maintenance';
    });
  }

  /**
   * Enable features after maintenance
   */
  function enableFeaturesAfterMaintenance() {
    // Re-enable upload buttons
    document.querySelectorAll('.upload-btn, .generate-btn').forEach(btn => {
      btn.disabled = false;
      btn.title = btn.dataset.originalTitle || '';
    });

    // Refresh gallery
    if (window.Gallery && window.Gallery.isActive()) {
      window.Gallery.refresh();
    }
  }

  // Subscription Management

  /**
   * Subscribe to events
   */
  function subscribe(event, callback) {
    if (!subscriptions.has(event)) {
      subscriptions.set(event, new Set());
    }
    subscriptions.get(event).add(callback);
    return this;
  }

  /**
   * Unsubscribe from events
   */
  function unsubscribe(event, callback) {
    if (subscriptions.has(event)) {
      subscriptions.get(event).delete(callback);
    }
    return this;
  }

  /**
   * Notify all subscribers of an event
   */
  function notifySubscribers(event, data) {
    if (subscriptions.has(event)) {
      subscriptions.get(event).forEach(callback => {
        try {
          callback(data);
        } catch (error) {
          console.error(`Error in subscriber for ${event}:`, error);
        }
      });
    }

    // Also emit to EventBus if available
    if (window.EventBus) {
      EventBus.emit(`realtime:${event}`, data);
    }
  }

  /**
   * Get status of all streams
   */
  function getStatus() {
    const status = {};
    streams.forEach((stream, name) => {
      status[name] = stream.getStatus();
    });
    return status;
  }

  /**
   * Clean up resources
   */
  function destroy() {
    // Stop all streams
    stopAll();

    // Clear subscriptions
    subscriptions.clear();

    // Unsubscribe from global events
    if (window.SSEClient) {
      SSEClient.off('visibility:visible', handleVisibilityRestore);
      SSEClient.off('visibility:hidden', handleVisibilityHidden);
    }
  }

  // Public API
  return {
    init,
    startStream,
    stopStream,
    startAll,
    stopAll,
    subscribe,
    unsubscribe,
    getStatus,
    destroy
  };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
  module.exports = RealtimeUpdates;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
  window.RealtimeUpdates = RealtimeUpdates;
}
