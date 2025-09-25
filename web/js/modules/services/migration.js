/**
 * Migration Service - Handles v1 to v2 data migration
 * Detects old database, transforms data, and manages migration process
 * @module MigrationService
 */
const MigrationService = (function() {
    'use strict';

    function createStub() {
        const target = {};
        return new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return (...args) => {
                    if (prop === 'prefetch' || prop === 'init' || prop === 'startMigration' || prop === 'renderMigrationBanner') {
                        return Promise.resolve();
                    }
                    return undefined;
                };
            },
            set: (obj, prop, value) => {
                obj[prop] = value;
                return true;
            },
        });
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] migration service skipped outside PromptManager UI context');
        return createStub();
    }

    // Configuration
    const config = {
        v1DbPaths: ['prompts.db', 'example_prompts.db'],
        migrationKey: 'promptmanager_v2_migrated',
        freshInstallKey: 'promptmanager_v2_fresh_install',
        backupSuffix: '_v1_backup',
        apiEndpoint: '/api/v1/migration'
    };

    const SESSION_INFO_KEY = 'promptmanager_migration_info_cache';
    const REMIND_LATER_KEY = 'migration_remind_later';
    const SESSION_TOKEN = `${Date.now()}_${Math.random().toString(36).slice(2)}`;

    const apiRoutes = {
        info: `${config.apiEndpoint}/info`,
        start: `${config.apiEndpoint}/start`
    };

    async function requestJson(url, options = {}) {
        const init = { ...options };
        const headers = { Accept: 'application/json', ...(init.headers || {}) };

        if (init.body && !headers['Content-Type']) {
            headers['Content-Type'] = 'application/json';
        }

        init.headers = headers;

        const response = await fetch(url, init);
        const text = await response.text();
        let payload = {};

        if (text) {
            try {
                payload = JSON.parse(text);
            } catch (error) {
                console.error('MigrationService: Failed to parse JSON response', error);
            }
        }

        if (!response.ok) {
            const message = payload.error || response.statusText || 'Request failed';
            throw new Error(message);
        }

        return payload;
    }

    // State
    let migrationStatus = {
        checked: false,
        v1Detected: false,
        migrated: false,
        inProgress: false,
        error: null,
        info: null,
        lastProgress: null
    };

    function cacheMigrationInfo(info) {
        migrationStatus.info = info;
        try {
            sessionStorage.setItem(SESSION_INFO_KEY, JSON.stringify(info));
        } catch (error) {
            console.warn('Migration: Unable to cache migration info', error);
        }
    }

    function getCachedMigrationInfo() {
        try {
            const cached = sessionStorage.getItem(SESSION_INFO_KEY);
            if (!cached) {
                return null;
            }
            return JSON.parse(cached);
        } catch (error) {
            console.warn('Migration: Failed to parse cached info', error);
            sessionStorage.removeItem(SESSION_INFO_KEY);
            return null;
        }
    }

    // Private methods
    function getMigrationFlag() {
        return localStorage.getItem(config.migrationKey) === 'true';
    }

    function setMigrationFlag(value) {
        if (value) {
            localStorage.setItem(config.migrationKey, 'true');
            localStorage.setItem(config.migrationKey + '_date', new Date().toISOString());
        } else {
            localStorage.removeItem(config.migrationKey);
            localStorage.removeItem(config.migrationKey + '_date');
        }
    }

    function getFreshInstallFlag() {
        return localStorage.getItem(config.freshInstallKey) === 'true';
    }

    function setFreshInstallFlag(value) {
        if (value) {
            localStorage.setItem(config.freshInstallKey, 'true');
            localStorage.setItem(config.freshInstallKey + '_date', new Date().toISOString());
        } else {
            localStorage.removeItem(config.freshInstallKey);
            localStorage.removeItem(config.freshInstallKey + '_date');
        }
    }

    async function fetchMigrationInfo({ forceRefresh = false } = {}) {
        if (!forceRefresh) {
            const cached = getCachedMigrationInfo();
            if (cached) {
                return cached;
            }
        }

        console.log('Migration: Fetching migration info from', apiRoutes.info);

        const payload = await requestJson(apiRoutes.info, { method: 'GET' });
        const data = payload.data || payload || {};

        const info = {
            needed: Boolean(data.needed),
            status: data.status || 'unknown',
            v1Info: data.v1_info || {},
            raw: data
        };

        cacheMigrationInfo(info);
        return info;
    }

    async function performMigration(action) {
        const payload = await requestJson(apiRoutes.start, {
            method: 'POST',
            body: JSON.stringify({ action })
        });

        const data = payload.data || payload || {};
        data.stats = data.stats || {};
        return data;
    }

    function createMigrationModal() {
        // Remove existing modal if present
        const existing = document.getElementById('migration-modal');
        if (existing) existing.remove();

        const modal = document.createElement('div');
        modal.id = 'migration-modal';
        modal.className = 'modal modal-overlay active';
        modal.innerHTML = `
            <div class="modal-content migration-modal">
                <div class="modal-header migration-header">
                    <div class="migration-title">
                        <span class="migration-icon" aria-hidden="true">✨</span>
                        <h2 class="modal-title">Welcome to PromptManager 2.0</h2>
                    </div>
                    <button class="modal-close" aria-label="Close" onclick="MigrationService.closeMigrationModal()">×</button>
                </div>
                <div class="modal-body migration-body">
                    <div class="migration-welcome">
                        <p class="migration-description">We detected your PromptManager v1 data. Migrate now for a cleaner layout, faster search, and better organization. A backup is automatically created.</p>

                        <div class="migration-stats">
                            <div class="stat-item">
                                <span class="stat-label">Prompts</span>
                                <span class="stat-value" id="prompts-count">-</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Images</span>
                                <span class="stat-value" id="images-count">-</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Categories</span>
                                <span class="stat-value" id="categories-count">-</span>
                            </div>
                        </div>

                        <div class="migration-options">
                            <div class="option-card recommended">
                                <h3>🔄 Migrate Everything</h3>
                                <p>Bring your prompts, categories, and settings to v2.</p>
                                <ul>
                                    <li>✓ Keeps all your work intact</li>
                                    <li>✓ Auto-backup of v1 database</li>
                                    <li>✓ One-click, safe and reversible</li>
                                </ul>
                                <button class="btn btn-primary" data-action="migrate-now">Migrate Now</button>
                            </div>
                            <div class="option-card">
                                <h3>🆕 Start Fresh</h3>
                                <p>Begin clean and migrate later from Settings.</p>
                                <ul>
                                    <li>• No data will be moved</li>
                                    <li>• You can decide later</li>
                                </ul>
                                <button class="btn btn-ghost" data-action="skip-migration">Start Fresh</button>
                            </div>
                        </div>

                        <div class="migration-progress hidden" id="migration-progress">
                            <h3>Migrating…</h3>
                            <div class="progress-bar"><div class="progress-fill" id="progress-fill"></div></div>
                            <p class="progress-status" id="progress-status">Preparing…</p>
                            <ul class="progress-steps" id="progress-steps">
                                <li data-step="backup">Backup v1 database</li>
                                <li data-step="read">Read v1 data</li>
                                <li data-step="transform">Transform to v2</li>
                                <li data-step="write">Write new records</li>
                                <li data-step="verify">Verify results</li>
                            </ul>
                        </div>

                        <div class="migration-result hidden" id="migration-result"></div>
                    </div>
                </div>
                <div class="modal-footer migration-footer">
                    <span class="migration-footer-note">You can migrate anytime from Settings → Advanced.</span>
                    <button class="btn btn-link btn-remind-later" data-action="remind-later">Remind me later</button>
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        modal.querySelector('[data-action="migrate-now"]').addEventListener('click', () => {
            MigrationService.startMigration();
        });
        modal.querySelector('[data-action="skip-migration"]').addEventListener('click', () => {
            MigrationService.skipMigration();
        });
        modal.querySelector('[data-action="remind-later"]').addEventListener('click', () => {
            MigrationService.remindLater();
        });
        return modal;
    }

    function updateProgress(step, percent, message) {
        const progressEl = document.getElementById('migration-progress');
        const fillEl = document.getElementById('progress-fill');
        const statusEl = document.getElementById('progress-status');
        const stepsEl = document.getElementById('progress-steps');

        migrationStatus.lastProgress = { step, percent, message };

        if (progressEl) {
            progressEl.classList.remove('hidden');
            document.querySelector('.migration-options')?.classList.add('hidden');
        }

        if (fillEl) {
            fillEl.style.width = `${percent}%`;
        }

        if (statusEl) {
            statusEl.textContent = message;
        }

        if (stepsEl && step) {
            const stepEl = stepsEl.querySelector(`[data-step="${step}"]`);
            if (stepEl) {
                // Mark previous steps as complete
                const allSteps = stepsEl.querySelectorAll('li');
                let foundCurrent = false;
                allSteps.forEach(li => {
                    if (li === stepEl) {
                        li.classList.add('active');
                        foundCurrent = true;
                    } else if (!foundCurrent) {
                        li.classList.add('complete');
                        li.classList.remove('active');
                    }
                });
            }
        }
    }

    function showResult(success, data) {
        const resultEl = document.getElementById('migration-result');
        const progressEl = document.getElementById('migration-progress');

        if (progressEl) progressEl.classList.add('hidden');

        if (resultEl) {
            resultEl.classList.remove('hidden');

            if (success) {
                // Check if this was an already migrated case
                if (data.already_migrated) {
                    resultEl.innerHTML = `
                        <div class="result-success">
                            <h3>✅ Already Migrated!</h3>
                            <p>${data.message || 'Your data was previously migrated to PromptManager 2.0'}</p>
                            <div class="result-stats">
                                <div>✓ ${data.prompts_migrated || 0} prompts in v2</div>
                            </div>
                            <button class="btn btn-primary" onclick="MigrationService.closeMigrationModal(); location.reload();">
                                Continue to Dashboard
                            </button>
                        </div>
                    `;
                } else {
                    resultEl.innerHTML = `
                        <div class="result-success">
                            <h3>✅ Migration Successful!</h3>
                            <p>Your data has been successfully migrated to PromptManager 2.0</p>
                            <div class="result-stats">
                                <div>✓ ${data.prompts_migrated || 0} prompts migrated</div>
                                <div>✓ ${data.images_migrated ?? data.images_linked ?? 0} images linked</div>
                                <div>✓ ${data.categories_migrated || 0} categories preserved</div>
                            </div>
                            <p class="result-backup">Backup saved as: ${data.backup_path || 'prompts_v1_backup.db'}</p>
                            <button class="btn btn-primary" onclick="MigrationService.closeMigrationModal(); location.reload();">
                                Start Using v2
                            </button>
                        </div>
                    `;
                }
            } else {
                resultEl.innerHTML = `
                    <div class="result-error">
                        <h3>❌ Migration Failed</h3>
                        <p>An error occurred during migration:</p>
                        <div class="error-message">${data.error || 'Unknown error'}</div>
                        <p>Your v1 data is still intact. You can try again from Settings > Advanced.</p>
                        <button class="btn btn-primary" onclick="MigrationService.closeMigrationModal()">
                            Close
                        </button>
                    </div>
                `;
            }
        }
    }

    // Public API
    return {
        /**
         * Initialize migration service and check for v1 data
         * Optimized to minimize startup overhead with dual-flag system
         */
        init: async function() {
            if (getMigrationFlag()) {
                migrationStatus.migrated = true;
                migrationStatus.checked = true;
                console.log('Migration: Already completed, skipping checks');
                return false;
            }

            if (getFreshInstallFlag()) {
                migrationStatus.checked = true;
                console.log('Migration: Fresh install, skipping checks');
                return false;
            }

            try {
                const remindToken = sessionStorage.getItem(REMIND_LATER_KEY);
                if (remindToken) {
                    if (remindToken === SESSION_TOKEN) {
                        console.log('Migration: User opted to be reminded later (session)');
                        return false;
                    }
                    sessionStorage.removeItem(REMIND_LATER_KEY);
                }
            } catch (error) {
                console.warn('Migration: Unable to read remind-later flag', error);
            }

            console.log('Migration: Checking for v1 data via API...');

            try {
                const cached = getCachedMigrationInfo();
                const info = cached || await fetchMigrationInfo();
                if (!cached) {
                    cacheMigrationInfo(info);
                } else {
                    // Refresh cache asynchronously for next load
                    fetchMigrationInfo({ forceRefresh: true }).catch((error) => {
                        console.warn('Migration: Background refresh failed', error);
                    });
                }
                migrationStatus.checked = true;
                migrationStatus.v1Detected = info.needed;

                if (!info.needed) {
                    console.log('Migration: No v1 data found, marking as fresh install');
                    setFreshInstallFlag(true);
                    return false;
                }

                console.log('Migration: Legacy data detected, presenting migration modal');
                createMigrationModal();
                const stats = info.v1Info || {};

                const prompts = document.getElementById('prompts-count');
                const images = document.getElementById('images-count');
                const categories = document.getElementById('categories-count');

                if (prompts) prompts.textContent = stats.prompt_count ?? stats.prompts ?? '0';
                if (images) images.textContent = stats.image_count ?? stats.images ?? '0';
                if (categories) categories.textContent = stats.category_count ?? stats.categories ?? '0';

                return true;
            } catch (error) {
                migrationStatus.error = error.message;
                console.error('Migration: Failed to load migration info', error);
                return false;
            }
        },

        /**
         * Start the migration process
         */
        startMigration: async function() {
            if (migrationStatus.inProgress) {
                console.warn('Migration already in progress');
                return null;
            }

            migrationStatus.inProgress = true;
            migrationStatus.error = null;

            try {
                updateProgress('backup', 10, 'Preparing migration…');
                updateProgress('transform', 60, 'Migrating data. This may take a moment…');

                const result = await performMigration('migrate');
                const stats = result.stats || {};

                updateProgress('verify', 100, 'Finalising migration…');

                if (result.success) {
                    setMigrationFlag(true);
                    migrationStatus.migrated = true;
                    migrationStatus.v1Detected = false;
                    showResult(true, stats);

                    if (window.EventBus) {
                        EventBus.emit('migration.complete', result);
                    }
                } else {
                    const errorMessage = stats.error || 'Migration failed';
                    migrationStatus.error = errorMessage;
                    showResult(false, { error: errorMessage });
                }

                return result;
            } catch (error) {
                migrationStatus.error = error.message;
                console.error('Migration error:', error);
                showResult(false, { error: error.message });
                throw error;
            } finally {
                migrationStatus.inProgress = false;
            }
        },

        /**
         * Skip migration and start fresh
         */
        skipMigration: async function() {
            try {
                const result = await performMigration('fresh');

                if (result.success) {
                    setFreshInstallFlag(true);
                    setMigrationFlag(true);
                    migrationStatus.migrated = true;
                    migrationStatus.v1Detected = false;
                    this.closeMigrationModal();

                    if (window.showToast) {
                        window.showToast('Starting fresh with PromptManager 2.0', 'info');
                    }
                } else {
                    const errorMessage = result.stats?.error || 'Unable to start fresh';
                    migrationStatus.error = errorMessage;
                    if (window.showToast) {
                        window.showToast(errorMessage, 'error');
                    }
                    return result;
                }

                return result;
            } catch (error) {
                migrationStatus.error = error.message;
                console.error('Migration skip error:', error);
                if (window.showToast) {
                    window.showToast(`Failed to start fresh: ${error.message}`, 'error');
                }
                throw error;
            }
        },

        /**
         * Remind user later about migration
         */
        remindLater: function() {
            // Set a temporary flag to not show again this session
            try {
                sessionStorage.setItem(REMIND_LATER_KEY, SESSION_TOKEN);
            } catch (error) {
                console.warn('Migration: Unable to persist remind-later choice', error);
            }
            this.closeMigrationModal();
        },

        /**
         * Close migration modal
         */
        closeMigrationModal: function() {
            const modal = document.getElementById('migration-modal');
            if (modal) {
                modal.classList.remove('active');
                setTimeout(() => modal.remove(), 300);
            }
        },

        /**
         * Reset migration status (for testing or re-migration)
         */
        resetMigration: function() {
            // Clear both flags to force re-check on next init
            setMigrationFlag(false);
            setFreshInstallFlag(false);
            migrationStatus.migrated = false;
            migrationStatus.checked = false;
            migrationStatus.v1Detected = false;
            migrationStatus.info = null;
            migrationStatus.lastProgress = null;
            migrationStatus.error = null;

            try {
                sessionStorage.removeItem(SESSION_INFO_KEY);
                sessionStorage.removeItem(REMIND_LATER_KEY);
            } catch (error) {
                console.warn('Migration: Unable to clear cached migration info', error);
            }

            return true;
        },

        /**
         * Get migration status
         */
        getStatus: function() {
            return {
                ...migrationStatus,
                flags: {
                    migrated: getMigrationFlag(),
                    freshInstall: getFreshInstallFlag()
                }
            };
        },

        /**
         * Get detailed migration state for debugging
         */
        getDetailedState: function() {
            const migrated = getMigrationFlag();
            const fresh = getFreshInstallFlag();

            if (migrated) {
                return 'MIGRATED';
            } else if (fresh) {
                return 'FRESH_INSTALL';
            } else if (migrationStatus.v1Detected) {
                return 'NEEDS_MIGRATION';
            } else if (migrationStatus.checked) {
                return 'CHECKED_NO_V1';
            } else {
                return 'NOT_CHECKED';
            }
        },

        /**
         * Manually trigger migration from settings
         */
        triggerManualMigration: async function() {
            try {
                const info = await fetchMigrationInfo();

                if (!info.needed) {
                    if (window.showToast) {
                        window.showToast('No v1 database found to migrate', 'warning');
                    }
                    return false;
                }

                createMigrationModal();

                const stats = info.v1Info || {};
                const prompts = document.getElementById('prompts-count');
                const images = document.getElementById('images-count');
                const categories = document.getElementById('categories-count');

                if (prompts) prompts.textContent = stats.prompt_count ?? stats.prompts ?? '0';
                if (images) images.textContent = stats.image_count ?? stats.images ?? '0';
                if (categories) categories.textContent = stats.category_count ?? stats.categories ?? '0';

                const remindBtn = document.querySelector('#migration-modal .btn-link');
                if (remindBtn) remindBtn.style.display = 'none';

                return true;
            } catch (error) {
                migrationStatus.error = error.message;
                console.error('Migration trigger error:', error);
                if (window.showToast) {
                    window.showToast(`Migration check failed: ${error.message}`, 'error');
                }
                return false;
            }
        },

        /**
         * Execute migration now (called from modal button)
         */
        migrateNow: async function() {
            return this.startMigration();
        },

        /**
         * Public helper for settings panel actions
         */
        runMigrationAction: async function(action = 'migrate') {
            const result = await performMigration(action);
            if (result.success && action === 'migrate') {
                setMigrationFlag(true);
                migrationStatus.migrated = true;
                migrationStatus.v1Detected = false;
            }
            if (result.success && action === 'fresh') {
                setFreshInstallFlag(true);
                setMigrationFlag(true);
                migrationStatus.migrated = true;
                migrationStatus.v1Detected = false;
            }
            if (!result.success) {
                migrationStatus.error = result.stats?.error || 'Migration failed';
            }
            return result;
        },

        /**
         * Retrieve current migration metadata
         */
        getMigrationInfo: async function() {
            return fetchMigrationInfo({ forceRefresh: true });
        },

        prefetch: async function() {
            try {
                await fetchMigrationInfo({ forceRefresh: true });
            } catch (error) {
                console.error('Migration: Prefetch failed', error);
            }
        }
    };
})();

// Export for use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = MigrationService;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.MigrationService = MigrationService;
}
