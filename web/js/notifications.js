(function () {
  'use strict';

  function isPromptManagerContext() {
    if (typeof window === 'undefined') {
      return false;
    }
    const path = window.location?.pathname || '';
    return path.includes('/prompt_manager');
  }

  const TYPE_CONFIG = {
    success: { icon: 'fa-circle-check', title: 'Success' },
    error: { icon: 'fa-circle-xmark', title: 'Error' },
    warning: { icon: 'fa-triangle-exclamation', title: 'Warning' },
    info: { icon: 'fa-circle-info', title: 'Notice' },
  };

  const POSITION_CLASS = {
    'top-left': 'toast-top-left',
    'top-center': 'toast-top-center',
    'top-right': 'toast-top-right',
    'bottom-left': 'toast-bottom-left',
    'bottom-center': 'toast-bottom-center',
    'bottom-right': 'toast-bottom-right',
  };

  const DEFAULT_SETTINGS = {
    enableNotifications: true,
    notificationPosition: 'top-right',
    notificationDuration: 5,
    soundAlerts: false,
    desktopNotifications: false,
    clickToDismiss: false,
    maxVisible: 3,
  };

  class NotificationService {
    constructor() {
      this.settings = { ...DEFAULT_SETTINGS };
      this.layer = null;
      this.activeTimers = new WeakMap();
      this.audioContext = null;
      this.desktopAsked = false;
    }

    init(initial = {}) {
      this.updateSettings({ ...DEFAULT_SETTINGS, ...initial }, { silent: true });
      if (isPromptManagerContext()) {
        this.ensureLayer();
      }
      return this;
    }

    ensureLayer() {
      if (!isPromptManagerContext()) {
        return null;
      }
      const position = this.settings.notificationPosition;
      if (!this.layer) {
        this.layer = document.createElement('div');
        this.layer.id = 'pm-toast-layer';
        this.layer.className = `toast-layer ${POSITION_CLASS[position] || POSITION_CLASS['top-right']}`;
        this.layer.setAttribute('role', 'status');
        this.layer.setAttribute('aria-live', 'assertive');
        document.body.appendChild(this.layer);
      } else {
        Object.values(POSITION_CLASS).forEach((cls) => this.layer.classList.remove(cls));
        this.layer.classList.add(POSITION_CLASS[position] || POSITION_CLASS['top-right']);
      }
      return this.layer;
    }

    updateSettings(partial, options = {}) {
      const next = { ...this.settings, ...partial };
      const positionChanged = next.notificationPosition !== this.settings.notificationPosition;
      this.settings = next;
      if (positionChanged && this.layer) {
        this.ensureLayer();
      }
      if (partial.desktopNotifications && !this.desktopAsked) {
        this.requestDesktopPermission();
      }
      if (!options.silent && !this.settings.enableNotifications) {
        this.clear();
      }
      return this.settings;
    }

    requestDesktopPermission() {
      if (!('Notification' in window)) return;
      if (Notification.permission === 'default') {
        Notification.requestPermission();
        this.desktopAsked = true;
      }
    }

    toMilliseconds(value) {
      const seconds = Number(value) || DEFAULT_SETTINGS.notificationDuration;
      return Math.max(seconds, 0) * 1000;
    }

    show(message, type = 'info', options = {}) {
      if (!this.settings.enableNotifications) return null;
      const config = TYPE_CONFIG[type] ? type : 'info';
      const base = TYPE_CONFIG[config];
      const layer = this.ensureLayer();
      if (!layer) {
        return null;
      }
      const duration = options.duration != null
        ? this.toMilliseconds(options.duration)
        : this.toMilliseconds(this.settings.notificationDuration);
      const clickToDismiss = options.clickToDismiss != null
        ? options.clickToDismiss
        : this.settings.clickToDismiss;
      const maxVisible = options.maxVisible != null
        ? options.maxVisible
        : this.settings.maxVisible;
      const safeMaxVisible = Math.max(1, Number(maxVisible) || 1);

      while (layer.children.length >= safeMaxVisible) {
        const child = layer.firstElementChild;
        if (child) this.dismiss(child, { immediate: true });
        else break;
      }

      const toast = document.createElement('div');
      toast.setAttribute('role', 'alert');
      toast.setAttribute('aria-live', 'assertive');
      toast.className = `pm-toast pm-toast--${config}`;
      toast.tabIndex = 0;
      if (clickToDismiss || duration === 0) {
        toast.classList.add('pm-toast--static');
      }
      toast.innerHTML = `
        <div class="pm-toast__icon">
          <i class="fa-solid ${options.icon || base.icon}" aria-hidden="true"></i>
        </div>
        <div class="pm-toast__body">
          <div class="pm-toast__title">${options.title || base.title}</div>
          <div class="pm-toast__message">${message}</div>
          <div class="pm-toast__progress">
            <div class="pm-toast__progress-bar"></div>
          </div>
        </div>
        <button class="pm-toast__close" type="button" aria-label="Dismiss notification">
          <i class="fa-solid fa-xmark" aria-hidden="true"></i>
        </button>
      `;

      const progress = toast.querySelector('.pm-toast__progress-bar');
      if (!clickToDismiss && duration > 0) {
        toast.style.setProperty('--pm-toast-duration', `${duration}ms`);
        progress.style.animationDuration = `${duration}ms`;
      } else {
        toast.style.setProperty('--pm-toast-duration', `0ms`);
      }

      const closeButton = toast.querySelector('.pm-toast__close');
      closeButton.addEventListener('click', () => this.dismiss(toast));
      toast.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          this.dismiss(toast);
        }
      });

      layer.appendChild(toast);
      requestAnimationFrame(() => toast.classList.add('is-visible'));

      if (!clickToDismiss && duration > 0) {
        const timer = setTimeout(() => this.dismiss(toast), duration + 50);
        this.activeTimers.set(toast, timer);
      }

      if (options.desktop ?? this.settings.desktopNotifications) {
        this.pushDesktopNotification(message, base.title);
      }
      if (options.sound ?? this.settings.soundAlerts) {
        this.playSound(config);
      }

      return toast;
    }

    success(message, options) {
      return this.show(message, 'success', options);
    }

    error(message, options) {
      return this.show(message, 'error', options);
    }

    warning(message, options) {
      return this.show(message, 'warning', options);
    }

    info(message, options) {
      return this.show(message, 'info', options);
    }

    dismiss(toast, { immediate = false } = {}) {
      if (!toast) return;
      const timer = this.activeTimers.get(toast);
      if (timer) {
        clearTimeout(timer);
        this.activeTimers.delete(toast);
      }
      toast.classList.remove('is-visible');
      if (immediate) {
        toast.remove();
      } else {
        setTimeout(() => toast.remove(), 220);
      }
    }

    clear() {
      if (!this.layer) return;
      Array.from(this.layer.children).forEach((toast) => this.dismiss(toast, { immediate: true }));
    }

    playSound(type) {
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        if (!this.audioContext) {
          this.audioContext = new AudioCtx();
        }
        const ctx = this.audioContext;
        if (ctx.state === 'suspended') {
          ctx.resume().catch(() => {});
        }
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        const freqMap = { success: 880, error: 220, warning: 440, info: 660 };
        osc.frequency.value = freqMap[type] || 660;
        gain.gain.value = 0.15;
        osc.type = 'sine';
        osc.connect(gain);
        gain.connect(ctx.destination);
        const now = ctx.currentTime;
        osc.start(now);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
        osc.stop(now + 0.4);
      } catch (error) {
        console.warn('Notification sound failed', error);
      }
    }

    pushDesktopNotification(message, title) {
      if (!('Notification' in window)) return;
      if (Notification.permission === 'granted') {
        new Notification(title, {
          body: message,
          icon: '/prompt_manager/favicon-32.png',
        });
      } else if (Notification.permission === 'default' && !this.desktopAsked) {
        Notification.requestPermission().then((permission) => {
          if (permission === 'granted') {
            this.pushDesktopNotification(message, title);
          }
        });
        this.desktopAsked = true;
      }
    }

    testAll() {
      const variants = [
        { type: 'info', message: 'Informational update in progress' },
        { type: 'success', message: 'Action completed successfully' },
        { type: 'warning', message: 'Please review your configuration' },
        { type: 'error', message: 'Something went wrong while saving' },
      ];
      variants.forEach((variant, index) => {
        setTimeout(() => this.show(variant.message, variant.type), index * 180);
      });
    }
  }

  if (!window.notificationService) {
    window.notificationService = new NotificationService();
    // Also expose with capital N for compatibility
    window.NotificationService = window.notificationService;
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => window.notificationService.init());
    } else {
      window.notificationService.init();
    }
  }

  window.showToast = function (message, type = 'info', options) {
    return window.notificationService.show(message, type, options || {});
  };
})();
