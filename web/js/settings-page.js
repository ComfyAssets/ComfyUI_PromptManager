/**
 * Settings Page JavaScript
 * Handles all settings page functionality with proper separation of concerns
 */

(function() {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] settings-page skipped outside PromptManager UI context');
    return;
  }

  const coreSaveSettings = typeof window.saveSettings === 'function'
    ? window.saveSettings.bind(window)
    : null;

  // Settings state management
  let settings = {};
  let isDirty = false;
  let blurTagsDraft = [];
  let statsIgnoredWordsDraft = [];
  const DEFAULT_BLUR_TAGS = ['nsfw'];
  let allowSaveNotification = false;

  /**
   * Initialize settings page
   */
  async function init() {
    await loadSettingsToForm();
    setupNavigation();
    setupChangeTracking();
    setupSearch();
    attachButtonHandlers();
    initializeNotificationService();
    setupBlurSettingsUI();
    setupStatsIgnoredWordsUI();
    initializeSSEComponents();
  }

  /**
   * Initialize realtime components if available
   */
  function initializeSSEComponents() {
    // Initialize Footer Status (compact footer version)
    if (window.FooterStatus) {
      FooterStatus.init({
        showText: true,
        showCount: true,
        enableTooltips: true
      });
    }

    // Initialize Status Indicator
    if (window.StatusIndicator) {
      StatusIndicator.init({
        position: 'bottom-right',
        autoHide: true,
        autoHideDelay: 5000,
        showDetails: true
      });
    }

    // Initialize Progress Indicator
    if (window.ProgressIndicator) {
      ProgressIndicator.init({
        position: 'top-center',
        showPercentage: true,
        showTimeEstimate: true,
        stackable: true
      });
    }
  }

  /**
   * Load saved settings into the form
   */
  async function loadSettingsToForm() {
    try {
      // Use the async loadSettings function from settings.js which loads from backend
      let savedSettings;
      if (typeof window.loadSettings === 'function') {
        savedSettings = await window.loadSettings();
      } else {
        // Fallback to localStorage if loadSettings not available
        const stored = localStorage.getItem('promptManagerSettings');
        if (stored) {
          savedSettings = JSON.parse(stored);
        }
      }

      if (savedSettings) {
        // Apply saved settings to all form fields
        for (const [key, value] of Object.entries(savedSettings)) {
          const el = document.getElementById(key);
          if (el) {
            if (el.type === 'checkbox') {
              el.checked = value;
            } else {
              el.value = value;
            }
          }
        }

        // Apply FFmpeg path specifically
        if (savedSettings.ffmpegPath) {
          const ffmpegInput = document.getElementById('ffmpegPath');
          if (ffmpegInput) {
            ffmpegInput.value = savedSettings.ffmpegPath;
          }
        }

        statsIgnoredWordsDraft = Array.isArray(savedSettings.statsIgnoredWords)
          ? [...savedSettings.statsIgnoredWords]
          : [];
        renderStatsIgnoredWords();

        settings = savedSettings;
        console.log('Loaded settings from backend/localStorage:', savedSettings);
      }
    } catch (error) {
      console.error('Failed to load settings:', error);
    }

    initializeBlurSettings();
  }

  /**
   * Setup settings navigation between sections
   */
  function setupNavigation() {
    document.querySelectorAll('.settings-nav-item').forEach((item) => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const section = item.dataset.section;
        setActiveSection(section);
        if (section) {
          history.replaceState(null, '', `#${section}`);
        }
      });
    });

    const initialSection = (window.location.hash || '').replace('#', '');
    if (initialSection) {
      setActiveSection(initialSection);
    }
    window.addEventListener('hashchange', () => {
      const nextSection = (window.location.hash || '').replace('#', '');
      if (nextSection) {
        setActiveSection(nextSection);
      }
    });
  }

  function setActiveSection(sectionId) {
    if (!sectionId) {
      return;
    }

    const navItems = Array.from(document.querySelectorAll('.settings-nav-item'));
    const targetNav = navItems.find((item) => item.dataset.section === sectionId);
    const targetSection = document.getElementById(sectionId);

    if (!targetNav || !targetSection) {
      return;
    }

    navItems.forEach((item) => item.classList.remove('active'));
    targetNav.classList.add('active');

    document.querySelectorAll('.settings-section').forEach((section) => {
      section.classList.toggle('active', section.id === sectionId);
    });

    if (typeof targetNav.scrollIntoView === 'function') {
      targetNav.scrollIntoView({ block: 'nearest' });
    }
  }

  /**
   * Track changes to settings
   */
  function setupChangeTracking() {
    // Track input changes
    document.addEventListener('input', (e) => {
      if (e.target.closest('.settings-section')) {
        markDirty();
      }
    });

    // Track checkbox changes
    document.addEventListener('change', (e) => {
      if (e.target.type === 'checkbox' && e.target.closest('.settings-section')) {
        markDirty();
      }
    });
  }

  /**
   * Setup search functionality
   */
  function setupSearch() {
    const searchInput = document.getElementById('searchSettings');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        const searchTerm = e.target.value.toLowerCase();

        document.querySelectorAll('.form-group').forEach((group) => {
          const label = group.querySelector('.label-text');
          const hint = group.querySelector('.label-hint');

          const text = (
            (label?.textContent || '') + ' ' + (hint?.textContent || '')
          ).toLowerCase();

          group.style.display = text.includes(searchTerm) ? '' : 'none';
        });
      });
    }
  }

  /**
   * Update save status indicator
   */
  function updateSaveStatus() {
    const status = document.getElementById('saveStatus');
    if (!status) return;

    if (isDirty) {
      status.classList.add('unsaved');
      status.innerHTML = '<span>●</span><span>Unsaved changes</span>';
    } else {
      status.classList.remove('unsaved');
      status.innerHTML = '<span>✓</span><span>All changes saved</span>';
    }
  }

  /**
   * Save thumbnail enabled sizes to backend API
   */
  async function saveThumbnailSizesToBackend(settings) {
    try {
      // Convert checkbox states to enabled_sizes array
      const enabled_sizes = [];
      if (settings.thumbSizeSmall) enabled_sizes.push('small');
      if (settings.thumbSizeMedium) enabled_sizes.push('medium');
      if (settings.thumbSizeLarge) enabled_sizes.push('large');
      if (settings.thumbSizeXLarge) enabled_sizes.push('xlarge');

      // Only send if we have at least one size enabled
      if (enabled_sizes.length > 0) {
        console.log('[Settings] Saving thumbnail sizes to backend:', enabled_sizes);

        const response = await fetch('/api/v1/settings/thumbnails', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled_sizes })
        });

        if (response.ok) {
          console.log('[Settings] Thumbnail sizes saved to backend successfully');
        } else {
          console.warn('[Settings] Failed to save thumbnail sizes to backend:', response.status);
        }
      }
    } catch (error) {
      console.error('[Settings] Error saving thumbnail sizes to backend:', error);
      // Don't throw - this is not critical enough to block settings save
    }
  }

  /**
   * Save all settings
   */
  async function saveSettings(options = {}) {
    if (options instanceof Event) {
      options = {};
    }

    const hadChanges = isDirty;
    try {
      const blurTagsInput = document.getElementById('blurNSFWTags');
      if (blurTagsInput && !blurTagsInput.value.trim()) {
        blurTagsInput.value = DEFAULT_BLUR_TAGS.join(',');
      }

      // Gather all settings
      const newSettings = {};
      document.querySelectorAll(
        '.settings-section input, .settings-section select, .settings-section textarea'
      ).forEach((el) => {
        if (!el.id || el.dataset.settingsIgnore === 'true') {
          return;
        }
        newSettings[el.id] = el.type === 'checkbox' ? el.checked : el.value;
      });

      newSettings.statsIgnoredWords = [...statsIgnoredWordsDraft];

      // Use the async saveSettings function from settings.js which saves to backend
      if (typeof coreSaveSettings === 'function') {
        await coreSaveSettings(newSettings, { silent: true });
      } else {
        // Fallback to localStorage only
        localStorage.setItem('promptManagerSettings', JSON.stringify(newSettings));
      }
      console.log('Saved settings to localStorage:', newSettings);

      // Also make API call if available
      if (window.saveSettingsToAPI) {
        await window.saveSettingsToAPI(newSettings);
      }

      // Save thumbnail enabled sizes to backend
      await saveThumbnailSizesToBackend(newSettings);

      // Mark as saved
      settings = newSettings;
      statsIgnoredWordsDraft = Array.isArray(settings.statsIgnoredWords)
        ? [...settings.statsIgnoredWords]
        : [];
      renderStatsIgnoredWords();
      publishIgnoredWordsUpdate(statsIgnoredWordsDraft);
      isDirty = false;
      updateSaveStatus();
      allowSaveNotification = false;

      // Update NotificationService with new settings
      updateNotificationSettings();

      // Show success notification only when there were pending changes
      if (hadChanges && allowSaveNotification && window.NotificationService && options.silent !== true) {
        window.NotificationService.show('Settings saved successfully', 'success');
      }
    } catch (error) {
      if (window.NotificationService) {
        window.NotificationService.show('Failed to save settings', 'error');
      }
    }
  }

  /**
   * Reset settings to defaults
   */
  function resetSettings() {
    if (confirm('Reset all settings to defaults? This action cannot be undone.')) {
      location.reload();
    }
  }

  /**
   * Import settings from file
   */
  function importSettings() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = async (e) => {
      const file = e.target.files[0];
      if (file) {
        const text = await file.text();
        try {
          const imported = JSON.parse(text);
          // Apply to UI
          for (const [key, value] of Object.entries(imported)) {
            const el = document.getElementById(key);
            if (el) {
              if (el.type === 'checkbox') {
                el.checked = value;
              } else {
                el.value = value;
              }
            }
          }
          isDirty = true;
          updateSaveStatus();
          initializeBlurSettings();
          if (window.NotificationService) {
            window.NotificationService.show('Settings imported successfully', 'success');
          }
        } catch (error) {
          if (window.NotificationService) {
            window.NotificationService.show('Invalid settings file', 'error');
          }
        }
      }
    };
    input.click();
  }

  /**
   * Export settings to file
   */
  function exportSettings() {
    const exportData = {};
    document.querySelectorAll(
      '.settings-section input, .settings-section select, .settings-section textarea'
    ).forEach((el) => {
      if (el.id) {
        exportData[el.id] = el.type === 'checkbox' ? el.checked : el.value;
      }
    });

    const blob = new Blob([JSON.stringify(exportData, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'promptmanager-settings.json';
    a.click();
    URL.revokeObjectURL(url);
  }

  /**
   * Test notification system with all types
   */
  function testNotification() {
    if (window.NotificationService) {
      // Create one notification of each type to test the system
      window.NotificationService.show('Success! Your settings are working correctly.', 'success');

      setTimeout(() => {
        window.NotificationService.show('Error: This is what errors look like.', 'error');
      }, 100);

      setTimeout(() => {
        window.NotificationService.show('Warning: This is a warning message.', 'warning');
      }, 200);

      setTimeout(() => {
        window.NotificationService.show('Info: This is an informational message.', 'info');
      }, 300);

      // This tests the maxVisible setting - if set to 3, only 3 should show at once
      const maxVisible = document.getElementById('maxVisible')?.value || 3;
      console.log('Testing all 4 notification types. Max visible:', maxVisible);
    } else {
      console.error('NotificationService not loaded');
    }
  }

  /**
   * Test webhook connection
   */
  function testWebhook() {
    if (window.NotificationService) {
      window.NotificationService.show('Testing webhook...', 'info');
      setTimeout(() => {
        window.NotificationService.show('Webhook test successful!', 'success');
      }, 1500);
    }
  }

  /**
   * Update notification service settings
   */
  function updateNotificationSettings() {
    if (window.NotificationService) {
      // Map form IDs to NotificationService property names
      const notificationSettings = {
        enableNotifications: document.getElementById('enableNotifications')?.checked ?? true,
        notificationPosition: document.getElementById('notificationPosition')?.value || 'top-right',
        notificationDuration: parseInt(document.getElementById('notificationDuration')?.value) || 5,
        soundAlerts: document.getElementById('soundAlerts')?.checked ?? false,
        desktopNotifications: document.getElementById('desktopNotifications')?.checked ?? false,
        clickToDismiss: document.getElementById('clickToDismiss')?.checked ?? true,
        maxVisible: parseInt(document.getElementById('maxVisible')?.value) || 3
      };
      window.NotificationService.updateSettings(notificationSettings);
      console.log('NotificationService updated with settings:', notificationSettings);
    }
  }

  /**
   * Initialize notification service with form values
   */
  function initializeNotificationService() {
    // Wait a moment for the NotificationService to be ready
    setTimeout(() => {
      updateNotificationSettings();

      // Add real-time update listeners for notification settings
      const notificationInputs = [
        'enableNotifications', 'notificationPosition', 'notificationDuration',
        'soundAlerts', 'desktopNotifications', 'clickToDismiss', 'maxVisible'
      ];

      notificationInputs.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
          element.addEventListener('change', updateNotificationSettings);
          if (element.type === 'number' || element.type === 'text') {
            element.addEventListener('input', updateNotificationSettings);
          }
        }
      });
    }, 100);
  }

  function markDirty() {
    isDirty = true;
    allowSaveNotification = true;
    updateSaveStatus();
  }

  function setupBlurSettingsUI() {
    initializeBlurSettings();
  }

  function initializeBlurSettings() {
    const input = document.getElementById('blurNSFWTags');
    if (!input) return;

    if (!input.value) {
      input.value = DEFAULT_BLUR_TAGS.join(',');
    }

    renderBlurTagPreview(getCurrentBlurTags());
  }

  function parseBlurTagString(value) {
    if (!value) {
      return [];
    }

    return String(value)
      .split(',')
      .map((tag) => tag.trim().toLowerCase())
      .filter(Boolean);
  }

  function formatBlurTagString(tags) {
    const unique = Array.from(new Set(tags.map((tag) => tag.trim().toLowerCase()).filter(Boolean)));
    return unique.join(',');
  }

  function getCurrentBlurTags() {
    const input = document.getElementById('blurNSFWTags');
    if (!input) {
      return [...DEFAULT_BLUR_TAGS];
    }
    const parsed = parseBlurTagString(input.value);
    return parsed.length ? parsed : [...DEFAULT_BLUR_TAGS];
  }

  function renderBlurTagPreview(tags) {
    const preview = document.getElementById('blurNSFWTagsPreview');
    if (!preview) return;

    preview.innerHTML = '';

    if (!tags.length) {
      const empty = document.createElement('span');
      empty.className = 'blur-tag-empty';
      empty.textContent = 'No tags configured';
      preview.appendChild(empty);
      return;
    }

    tags.forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'blur-tag-chip';
      chip.textContent = tag;
      preview.appendChild(chip);
    });
  }

  function openBlurTagsModal() {
    blurTagsDraft = [...getCurrentBlurTags()];
    renderBlurTagModalList();
    showBlurTagsModal();
    const input = document.getElementById('blurTagInput');
    if (input) {
      input.value = '';
      setTimeout(() => input.focus(), 50);
    }
  }

  function showBlurTagsModal() {
    const modal = document.getElementById('blurTagsModal');
    if (!modal) return;
    modal.removeAttribute('hidden');
    document.body.style.overflow = 'hidden';
  }

  function hideBlurTagsModal() {
    const modal = document.getElementById('blurTagsModal');
    if (!modal) return;
    modal.setAttribute('hidden', 'hidden');
    document.body.style.overflow = '';
  }

  function isBlurTagsModalHidden() {
    const modal = document.getElementById('blurTagsModal');
    return !modal || modal.hasAttribute('hidden');
  }

  function renderBlurTagModalList() {
    const list = document.getElementById('blurTagsList');
    if (!list) return;

    list.innerHTML = '';

    if (!blurTagsDraft.length) {
      const empty = document.createElement('span');
      empty.className = 'blur-tag-empty';
      empty.textContent = 'No tags yet';
      list.appendChild(empty);
      return;
    }

    blurTagsDraft.forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'blur-tag-chip';
      chip.textContent = tag;

      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.dataset.tag = tag;
      removeBtn.setAttribute('aria-label', `Remove ${tag}`);
      removeBtn.innerHTML = '&times;';

      chip.appendChild(removeBtn);
      list.appendChild(chip);
    });
  }

  function addBlurTag(rawTag) {
    const normalized = (rawTag || '').trim().toLowerCase();
    if (!normalized) {
      return;
    }

    if (!blurTagsDraft.includes(normalized)) {
      blurTagsDraft.push(normalized);
      blurTagsDraft = blurTagsDraft.sort();
      renderBlurTagModalList();
    }
  }

  function removeBlurTag(tag) {
    blurTagsDraft = blurTagsDraft.filter((item) => item !== tag);
    renderBlurTagModalList();
  }

  function handleBlurTagInput(rawValue) {
    const normalized = (rawValue || '').trim();
    if (!normalized) {
      return;
    }

    normalized
      .split(/[\s,]+/)
      .map((token) => token.trim().toLowerCase())
      .filter(Boolean)
      .forEach(addBlurTag);
  }

  function saveBlurTagsModal() {
    const finalTags = blurTagsDraft.length ? blurTagsDraft : [...DEFAULT_BLUR_TAGS];
    persistBlurTags(finalTags);
    hideBlurTagsModal();
  }

  function cancelBlurTagsModal() {
    hideBlurTagsModal();
  }

  function persistBlurTags(tags) {
    const input = document.getElementById('blurNSFWTags');
    if (!input) return;

    const formatted = formatBlurTagString(tags);
    input.value = formatted;
    renderBlurTagPreview(parseBlurTagString(formatted));
    markDirty();
  }

  function setupStatsIgnoredWordsUI() {
    const list = document.getElementById('statsIgnoredWordsList');
    if (!list) {
      return;
    }

    renderStatsIgnoredWords();

    const input = document.getElementById('statsIgnoredWordInput');
    const addButton = document.querySelector('[data-action="add-ignored-word"]');
    const resetButton = document.querySelector('[data-action="reset-ignored-words"]');

    addButton?.addEventListener('click', () => {
      if (!input) return;
      addStatsIgnoredWord(input.value);
      input.value = '';
      input.focus();
    });

    input?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        addStatsIgnoredWord(event.target.value);
        event.target.value = '';
      }
    });

    list.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-word]');
      if (!button) return;
      removeStatsIgnoredWord(button.dataset.word);
    });

    resetButton?.addEventListener('click', () => {
      if (!statsIgnoredWordsDraft.length) {
        return;
      }
      statsIgnoredWordsDraft = [];
      renderStatsIgnoredWords();
      markDirty();
    });

    window.addEventListener('stats.ignoredWords.updated', (event) => {
      const words = event?.detail?.words;
      if (!Array.isArray(words)) {
        return;
      }
      statsIgnoredWordsDraft = [...words];
      if (settings) {
        settings.statsIgnoredWords = [...words];
      }
      renderStatsIgnoredWords();
    });
  }

  function renderStatsIgnoredWords() {
    const list = document.getElementById('statsIgnoredWordsList');
    if (!list) {
      return;
    }

    list.innerHTML = '';

    if (!statsIgnoredWordsDraft.length) {
      const empty = document.createElement('span');
      empty.className = 'stats-ignored-empty';
      empty.textContent = 'No ignored words configured';
      list.appendChild(empty);
      return;
    }

    statsIgnoredWordsDraft.forEach((word) => {
      const chip = document.createElement('span');
      chip.className = 'stats-ignored-chip';
      const label = document.createElement('span');
      label.className = 'stats-ignored-label';
      label.textContent = word;

      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.className = 'stats-ignored-remove';
      removeButton.dataset.word = word;
      removeButton.setAttribute('aria-label', `Remove ${word} from ignored words`);
      removeButton.innerHTML = '&times;';

      chip.appendChild(label);
      chip.appendChild(removeButton);
      list.appendChild(chip);
    });
  }

  function addStatsIgnoredWord(rawWord) {
    const value = (rawWord || '').trim();
    if (!value) {
      return;
    }

    const normalized = value.toLowerCase();
    const exists = statsIgnoredWordsDraft.some((entry) => entry.toLowerCase() === normalized);
    if (exists) {
      return;
    }

    statsIgnoredWordsDraft.push(value);
    statsIgnoredWordsDraft = statsIgnoredWordsDraft.sort((a, b) => a.localeCompare(b));
    renderStatsIgnoredWords();
    markDirty();
  }

  function removeStatsIgnoredWord(word) {
    const normalized = (word || '').toLowerCase();
    const next = statsIgnoredWordsDraft.filter((entry) => entry.toLowerCase() !== normalized);
    if (next.length === statsIgnoredWordsDraft.length) {
      return;
    }
    statsIgnoredWordsDraft = next;
    renderStatsIgnoredWords();
    markDirty();
  }

  function publishIgnoredWordsUpdate(words) {
    const payload = { words: [...words] };
    window.dispatchEvent(new CustomEvent('stats.ignoredWords.updated', { detail: payload }));
    if (window.EventBus) {
      try {
        EventBus.emit('stats.ignoredWords.updated', payload);
      } catch (error) {
        console.error('Failed to emit stats.ignoredWords.updated via EventBus', error);
      }
    }
  }

  /**
   * Attach button handlers
   */
  function attachButtonHandlers() {
    // Make functions available to onclick handlers
    window.saveSettingsPage = saveSettings;
    window.resetSettings = resetSettings;
    window.importSettings = importSettings;
    window.exportSettings = exportSettings;
    window.testNotification = testNotification;
    window.testWebhook = testWebhook;
    window.resetSettingsFunc = resetSettings; // Alias for save bar

    const manageBlurButton = document.querySelector('[data-action="manage-blur-tags"]');
    manageBlurButton?.addEventListener('click', openBlurTagsModal);

    document.querySelector('[data-action="add-blur-tag"]')?.addEventListener('click', () => {
      const input = document.getElementById('blurTagInput');
      if (!input) return;
      addBlurTag(input.value);
      input.value = '';
      input.focus();
    });

    const blurTagInput = document.getElementById('blurTagInput');
    blurTagInput?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        handleBlurTagInput(event.target.value);
        event.target.value = '';
      }
    });

    blurTagInput?.addEventListener('input', (event) => {
      const value = event.target.value;
      if (value.includes(',')) {
        const parts = value.split(',');
        const lastPart = parts.pop();
        parts.forEach((part) => handleBlurTagInput(part));
        event.target.value = lastPart ?? '';
      }
    });

    document.querySelector('[data-action="save-blur-tags-modal"]')?.addEventListener('click', saveBlurTagsModal);
    document.querySelector('[data-action="cancel-blur-tags-modal"]')?.addEventListener('click', cancelBlurTagsModal);
    document.querySelector('[data-action="close-blur-tags-modal"]')?.addEventListener('click', cancelBlurTagsModal);

    const blurTagsModal = document.getElementById('blurTagsModal');
    if (blurTagsModal) {
      blurTagsModal.addEventListener('click', (event) => {
        if (event.target === blurTagsModal) {
          cancelBlurTagsModal();
        }
      });
    }

    const blurTagsList = document.getElementById('blurTagsList');
    if (blurTagsList) {
      blurTagsList.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-tag]');
        if (!button) return;
        removeBlurTag(button.dataset.tag);
      });
    }

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !isBlurTagsModalHidden()) {
        cancelBlurTagsModal();
      }
    });
  }

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
