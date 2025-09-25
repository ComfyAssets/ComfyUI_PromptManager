/**
 * Realtime Client shim that reuses ComfyUI's WebSocket in place of SSE.
 * Maintains the legacy SSEClient interface so existing modules keep working.
 */
const SSEClient = (function() {
  'use strict';

  function createStub() {
    return {
      on: () => () => {},
      off: () => {},
      createStream: () => ({
        on: () => () => {},
        off: () => {},
        close: () => {},
        getStatus: () => 'disconnected',
      }),
      getStatus: () => 'disconnected',
      getClientId: () => null,
    };
  }

  if (typeof window === 'undefined') {
    return createStub();
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] SSE client skipped outside PromptManager UI context');
    return createStub();
  }

  const config = {
    reconnectDelay: 2000,
    maxReconnectDelay: 30000,
    reconnectBackoff: 1.5,
    maxReconnectAttempts: 10,
  };

  let ws = null;
  let reconnectTimer = null;
  let reconnectAttempts = 0;
  let status = 'disconnected';
  let clientId = null;

  const visibilityHandlers = {
    'visibility:visible': new Set(),
    'visibility:hidden': new Set(),
  };

  const globalHandlers = new Map();
  const messageHandlers = new Map();

  const streamRegistry = new Map();

  function init() {
    if (!ws) {
      connect();
    }
    setupVisibilityListener();
  }

  function connect() {
    if (ws || reconnectAttempts > config.maxReconnectAttempts) {
      return;
    }

    emitGlobal('sse:connecting', {});

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws`;

    ws = new WebSocket(url);
    ws.addEventListener('open', handleOpen);
    ws.addEventListener('message', handleMessage);
    ws.addEventListener('close', handleClose);
    ws.addEventListener('error', handleError);
  }

  function handleOpen() {
    status = 'connected';
    reconnectAttempts = 0;
    clearTimeout(reconnectTimer);
    emitGlobal('sse:connected', { clientId });
  }

  function handleMessage(event) {
    try {
      const payload = JSON.parse(event.data);
      const type = payload?.type;
      const data = payload?.data ?? {};

      if (!type) {
        console.warn('[SSEClient] Received message without type', payload);
        return;
      }

      if (type === 'status' && data?.sid && !clientId) {
        clientId = data.sid;
      }

      emitGlobal('ws:message', { type, data });
      emitGlobal(`ws:event:${type}`, data);

      if (type === 'prompt_manager.notifications.progress') {
        // Preserve legacy event bus contract
        emitGlobal('sse:progress', data);
      }

      const handlers = messageHandlers.get(type);
      if (handlers) {
        handlers.forEach((handler) => {
          try {
            handler(data);
          } catch (error) {
            console.error(`[SSEClient] Handler error for ${type}:`, error);
          }
        });
      }

      // Dispatch DOM CustomEvent for consumers in ComfyUI extensions
      window.dispatchEvent(new CustomEvent(type, { detail: data }));
    } catch (error) {
      console.error('[SSEClient] Failed to parse WebSocket message', error, event.data);
    }
  }

  function handleClose() {
    status = 'disconnected';
    ws = null;
    emitGlobal('sse:disconnected', { clientId });
    scheduleReconnect();
  }

  function handleError(error) {
    emitGlobal('sse:error', { error });
  }

  function scheduleReconnect() {
    if (reconnectAttempts >= config.maxReconnectAttempts) {
      emitGlobal('sse:error', { message: 'Maximum reconnect attempts reached' });
      return;
    }

    reconnectAttempts += 1;
    const delay = Math.min(
      config.reconnectDelay * Math.pow(config.reconnectBackoff, reconnectAttempts - 1),
      config.maxReconnectDelay,
    );

    emitGlobal('sse:reconnecting', { attempt: reconnectAttempts, delay });

    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      connect();
    }, delay);
  }

  function setupVisibilityListener() {
    if (setupVisibilityListener.initialized) {
      return;
    }
    setupVisibilityListener.initialized = true;

    document.addEventListener('visibilitychange', () => {
      const state = document.visibilityState === 'visible' ? 'visibility:visible' : 'visibility:hidden';
      emitVisibility(state);
    });
  }

  function emitVisibility(event) {
    const listeners = visibilityHandlers[event];
    if (listeners) {
      listeners.forEach((handler) => {
        try {
          handler();
        } catch (error) {
          console.error('[SSEClient] Visibility handler error:', error);
        }
      });
    }
    emitGlobal(event, {});
  }

  function emitGlobal(event, payload) {
    const listeners = globalHandlers.get(event);
    if (listeners) {
      listeners.forEach((handler) => {
        try {
          handler(payload);
        } catch (error) {
          console.error(`[SSEClient] Handler error for ${event}:`, error);
        }
      });
    }

    if (window.EventBus) {
      try {
        EventBus.emit(event, payload);
      } catch (error) {
        console.error('[SSEClient] EventBus emit error:', error);
      }
    }
  }

  function on(event, handler) {
    if (!globalHandlers.has(event)) {
      globalHandlers.set(event, new Set());
    }
    globalHandlers.get(event).add(handler);
    return () => off(event, handler);
  }

  function off(event, handler) {
    const handlers = globalHandlers.get(event);
    if (!handlers) {
      return;
    }
    handlers.delete(handler);
    if (handlers.size === 0) {
      globalHandlers.delete(event);
    }
  }

  function subscribe(eventType, handler) {
    if (!messageHandlers.has(eventType)) {
      messageHandlers.set(eventType, new Set());
    }
    messageHandlers.get(eventType).add(handler);
    init();

    return () => {
      const handlers = messageHandlers.get(eventType);
      if (!handlers) {
        return;
      }
      handlers.delete(handler);
      if (handlers.size === 0) {
        messageHandlers.delete(eventType);
      }
    };
  }

  function createStream(streamConfig) {
    const { events = {}, options = {} } = streamConfig || {};
    const unsubscribes = [];
    let streamStatus = 'stopped';

    const api = {
      async start() {
        if (streamStatus === 'started') {
          return;
        }

        streamStatus = 'starting';
        init();

        Object.entries(events).forEach(([eventName, handler]) => {
          if (eventName === 'connected' || eventName === 'disconnected' || eventName === 'reconnecting' || eventName === 'error') {
            const mapped = eventName === 'error' ? 'sse:error' : `sse:${eventName}`;
            unsubscribes.push(on(mapped, handler));
            return;
          }

          const unsubscribe = subscribe(eventName, handler);
          unsubscribes.push(unsubscribe);
        });

        streamStatus = 'started';
        streamRegistry.set(options.key || Symbol(), unsubscribes);
      },

      stop() {
        if (streamStatus === 'stopped') {
          return;
        }
        unsubscribes.splice(0).forEach((unsubscribe) => {
          try {
            unsubscribe();
          } catch (error) {
            console.error('[SSEClient] Failed to unsubscribe stream handler', error);
          }
        });
        streamStatus = 'stopped';
      },

      getStatus() {
        return streamStatus;
      },
    };

    return api;
  }

  function handleVisibilitySubscription(event, handler) {
    if (!visibilityHandlers[event]) {
      visibilityHandlers[event] = new Set();
    }
    visibilityHandlers[event].add(handler);
    return () => {
      visibilityHandlers[event].delete(handler);
    };
  }

  const api = {
    on(event, handler) {
      if (event === 'visibility:visible' || event === 'visibility:hidden') {
        return handleVisibilitySubscription(event, handler);
      }
      return on(event, handler);
    },

    off(event, handler) {
      if (event === 'visibility:visible' || event === 'visibility:hidden') {
        visibilityHandlers[event]?.delete(handler);
        return;
      }
      off(event, handler);
    },

    createStream(config) {
      return createStream(config);
    },

    getStatus() {
      return status;
    },

    getClientId() {
      return clientId;
    },
  };

  init();

  return api;
})();

if (typeof module !== 'undefined' && module.exports) {
  module.exports = SSEClient;
}

if (typeof window !== 'undefined') {
  window.SSEClient = SSEClient;
}
