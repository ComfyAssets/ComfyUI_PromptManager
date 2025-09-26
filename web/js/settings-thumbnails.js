/**
 * Thumbnail Settings Functions for Settings Page
 *
 * Handles thumbnail generation controls and status updates
 */

(function () {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] settings-thumbnails skipped outside PromptManager UI context');
    const noop = () => {};
    const noopAsync = async () => {};
    window.scanForMissingThumbnails = noopAsync;
    window.generateMissingThumbnails = noopAsync;
    window.rebuildAllThumbnails = noopAsync;
    window.clearThumbnailCache = noopAsync;
    window.updateThumbnailStatus = noop;
    window.updateThumbnailProgress = noop;
    window.resetThumbnailControls = noop;
    window.updateDiskUsage = noopAsync;
    return;
  }

// Global variables for thumbnail generation
let thumbnailGenerationTask = null;
let thumbnailStatusInterval = null;

/**
 * Scan for missing thumbnails
 */
async function scanForMissingThumbnails() {
    try {
        // Get selected sizes
        const sizes = [];
        if (document.getElementById('thumbSizeSmall').checked) sizes.push('small');
        if (document.getElementById('thumbSizeMedium').checked) sizes.push('medium');
        if (document.getElementById('thumbSizeLarge').checked) sizes.push('large');
        if (document.getElementById('thumbSizeXLarge').checked) sizes.push('xlarge');

        if (sizes.length === 0) {
            showNotification('Please select at least one thumbnail size', 'warning');
            return;
        }

        // Show loading state
        updateThumbnailStatus('Scanning for missing thumbnails...');

        const response = await fetch('/api/v1/thumbnails/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sizes })
        });

        if (!response.ok) {
            throw new Error(`Scan failed: ${response.statusText}`);
        }

        const result = await response.json();

        if (result.missing_count === 0) {
            updateThumbnailStatus('All thumbnails are up to date');
            document.getElementById('generateThumbnailsBtn').disabled = true;
            showNotification('No missing thumbnails found', 'success');
        } else {
            const totalOps = result.total_operations || result.missing_count * sizes.length;
            updateThumbnailStatus(`Found ${result.missing_count} images missing thumbnails (${totalOps} operations)`);
            document.getElementById('generateThumbnailsBtn').disabled = false;
            showNotification(`Found ${result.missing_count} images missing thumbnails`, 'info');
        }

        return result;

    } catch (error) {
        console.error('Scan error:', error);
        updateThumbnailStatus(`Scan failed: ${error.message}`);
        showNotification(`Failed to scan: ${error.message}`, 'error');
    }
}

/**
 * Generate missing thumbnails
 */
async function generateMissingThumbnails() {
    try {
        // Get selected sizes
        const sizes = [];
        if (document.getElementById('thumbSizeSmall').checked) sizes.push('small');
        if (document.getElementById('thumbSizeMedium').checked) sizes.push('medium');
        if (document.getElementById('thumbSizeLarge').checked) sizes.push('large');
        if (document.getElementById('thumbSizeXLarge').checked) sizes.push('xlarge');

        if (sizes.length === 0) {
            showNotification('Please select at least one thumbnail size', 'warning');
            return;
        }

        // Disable buttons during generation
        document.getElementById('generateThumbnailsBtn').disabled = true;
        document.querySelector('button[onclick="scanForMissingThumbnails()"]').disabled = true;

        // Show progress container
        document.getElementById('thumbnailProgressContainer').style.display = 'block';
        updateThumbnailProgress(0, 0);

        const response = await fetch('/api/v1/thumbnails/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                sizes,
                stream_progress: false  // Use polling instead of SSE
            })
        });

        if (!response.ok) {
            throw new Error(`Generation failed: ${response.statusText}`);
        }

        const result = await response.json();

        if (result.task_id) {
            // Start polling for progress
            thumbnailGenerationTask = result.task_id;
            startProgressPolling(result.task_id);
            showNotification('Thumbnail generation started', 'info');
        } else if (result.summary) {
            // Synchronous completion
            handleGenerationComplete(result.summary);
        }

    } catch (error) {
        console.error('Generation error:', error);
        updateThumbnailStatus(`Generation failed: ${error.message}`);
        showNotification(`Failed to generate: ${error.message}`, 'error');
        resetThumbnailControls();
    }
}

/**
 * Start polling for generation progress
 */
function startProgressPolling(taskId) {
    if (thumbnailStatusInterval) {
        clearInterval(thumbnailStatusInterval);
    }

    thumbnailStatusInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/v1/thumbnails/status/${taskId}`);

            if (!response.ok) {
                if (response.status === 404) {
                    // Task not found, might be completed
                    clearInterval(thumbnailStatusInterval);
                    resetThumbnailControls();
                    return;
                }
                throw new Error(`Status check failed: ${response.statusText}`);
            }

            const status = await response.json();

            // Update progress
            const progress = status.progress || {};
            const total = progress.total || 0;
            const completed = progress.completed || 0;
            const failed = progress.failed || 0;
            const skipped = progress.skipped || 0;
            const processed = completed + failed + skipped;

            updateThumbnailProgress(processed, total);

            // Update status text
            if (progress.current_file) {
                updateThumbnailStatus(`Processing: ${progress.current_file}`);
            }

            // Check if complete
            if (status.status === 'completed' || processed >= total) {
                clearInterval(thumbnailStatusInterval);
                handleGenerationComplete(status.result || {
                    completed,
                    failed,
                    skipped,
                    total,
                    processed
                });
            } else if (status.status === 'failed') {
                clearInterval(thumbnailStatusInterval);
                throw new Error(status.error || 'Generation failed');
            }

        } catch (error) {
            console.error('Progress check error:', error);
            clearInterval(thumbnailStatusInterval);
            updateThumbnailStatus(`Error: ${error.message}`);
            resetThumbnailControls();
        }
    }, 1000); // Poll every second
}

/**
 * Handle generation completion
 */
function handleGenerationComplete(summary) {
    const completed = Number(summary.completed ?? summary.stats?.completed ?? 0);
    const failed = Number(summary.failed ?? summary.stats?.failed ?? 0);
    const skipped = Number(summary.skipped ?? summary.stats?.skipped ?? 0);
    const processed = Number(
        summary.processed
        ?? summary.stats?.processed
        ?? completed + failed + skipped
    );
    const total = Number(summary.total ?? summary.stats?.total ?? processed);
    const durationSeconds = Number(
        summary.duration_seconds ?? summary.duration ?? 0
    );

    updateThumbnailProgress(processed, total || processed || 1);

    const timeParts = [];
    if (durationSeconds > 0) {
        const minutes = Math.floor(durationSeconds / 60);
        const seconds = durationSeconds % 60;
        if (minutes > 0) {
            timeParts.push(`${minutes}m`);
        }
        timeParts.push(`${seconds}s`);
    }

    const timeSuffix = timeParts.length ? ` in ${timeParts.join(' ')}` : '';
    updateThumbnailStatus(
        `Generation complete: ${completed} created, ${failed} failed, ${skipped} skipped${timeSuffix}`
    );

    if (failed > 0) {
        showNotification(`Generation completed with ${failed} errors`, 'warning');
    } else {
        showNotification('All thumbnails generated successfully', 'success');
    }

    resetThumbnailControls();
}

/**
 * Rebuild all thumbnails
 */
async function rebuildAllThumbnails() {
    // Get selected sizes
    const sizes = [];
    if (document.getElementById('thumbSizeSmall').checked) sizes.push('small');
    if (document.getElementById('thumbSizeMedium').checked) sizes.push('medium');
    if (document.getElementById('thumbSizeLarge').checked) sizes.push('large');
    if (document.getElementById('thumbSizeXLarge').checked) sizes.push('xlarge');

    if (sizes.length === 0) {
        showNotification('Please select at least one thumbnail size', 'warning');
        return;
    }

    const sizeNames = sizes.map(s => {
        switch(s) {
            case 'small': return 'Small (150x150)';
            case 'medium': return 'Medium (300x300)';
            case 'large': return 'Large (600x600)';
            case 'xlarge': return 'X-Large (1200x1200)';
            default: return s;
        }
    }).join(', ');

    const confirmed = await createConfirmationDialog(
        'Rebuild Thumbnails',
        `This will regenerate thumbnails for the selected sizes:\n\n${sizeNames}\n\nThis may take a long time. Continue?`,
        'Rebuild',
        'Cancel'
    );

    if (!confirmed) {
        return;
    }

    try {
        // Disable buttons during rebuild
        document.getElementById('generateThumbnailsBtn').disabled = true;
        document.querySelector('button[onclick="scanForMissingThumbnails()"]').disabled = true;

        // Show progress container
        document.getElementById('thumbnailProgressContainer').style.display = 'block';
        updateThumbnailProgress(0, 0);

        const response = await fetch('/api/v1/thumbnails/rebuild', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sizes })  // Pass selected sizes
        });

        if (!response.ok) {
            throw new Error(`Rebuild failed: ${response.statusText}`);
        }

        const result = await response.json();

        if (result.task_id) {
            thumbnailGenerationTask = result.task_id;
            startProgressPolling(result.task_id);
            showNotification(`Rebuilding thumbnails for ${sizes.length} size(s): ${result.total} operations`, 'info');
        }

    } catch (error) {
        console.error('Rebuild error:', error);
        showNotification(`Failed to rebuild: ${error.message}`, 'error');
        resetThumbnailControls();
    }
}

/**
 * Cancel the current thumbnail generation task
 */
async function cancelThumbnailGeneration() {
    if (!thumbnailGenerationTask) {
        console.log('No active thumbnail generation task to cancel');
        return;
    }

    try {
        const response = await fetch('/api/v1/thumbnails/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: thumbnailGenerationTask })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || `Cancel failed: ${response.statusText}`);
        }

        const result = await response.json();
        console.log('[INFO] Cancellation requested:', result);
        showNotification('Cancelling thumbnail generation...', 'info');

        // Update UI to show cancelling status
        updateThumbnailStatus('Cancelling...');

        // The polling will detect the cancelled status and clean up
    } catch (error) {
        console.error('Failed to cancel generation:', error);
        showNotification(`Failed to cancel: ${error.message}`, 'error');
    }
}

/**
 * Create a simple confirmation dialog
 */
function createConfirmationDialog(title, message, confirmText, cancelText) {
    return new Promise((resolve) => {
        // Create modal elements
        const modal = document.createElement('div');
        modal.className = 'modal-backdrop';
        modal.style.cssText = 'position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 9999; display: flex; align-items: center; justify-content: center;';

        const dialog = document.createElement('div');
        dialog.className = 'modal-dialog';
        dialog.style.cssText = 'background: var(--bg-secondary, #1a1a1a); border: 1px solid var(--border-color, #333); border-radius: 8px; padding: 20px; max-width: 500px; box-shadow: 0 4px 6px rgba(0,0,0,0.3);';

        dialog.innerHTML = `
            <h3 style="margin: 0 0 10px 0; color: var(--text-primary, #fff);">${title}</h3>
            <p style="margin: 0 0 20px 0; color: var(--text-secondary, #ccc);">${message}</p>
            <div style="display: flex; gap: 10px; justify-content: flex-end;">
                <button class="btn-secondary" id="modalCancel" style="padding: 8px 16px; background: var(--bg-tertiary, #2a2a2a); color: var(--text-primary, #fff); border: 1px solid var(--border-color, #444); border-radius: 4px; cursor: pointer;">
                    ${cancelText || 'Cancel'}
                </button>
                <button class="btn-primary" id="modalConfirm" style="padding: 8px 16px; background: var(--accent-color, #0066cc); color: white; border: none; border-radius: 4px; cursor: pointer;">
                    ${confirmText || 'Confirm'}
                </button>
            </div>
        `;

        modal.appendChild(dialog);
        document.body.appendChild(modal);

        // Handle clicks
        const cleanup = () => {
            document.body.removeChild(modal);
        };

        dialog.querySelector('#modalCancel').onclick = () => {
            cleanup();
            resolve(false);
        };

        dialog.querySelector('#modalConfirm').onclick = () => {
            cleanup();
            resolve(true);
        };

        // Handle backdrop click
        modal.onclick = (e) => {
            if (e.target === modal) {
                cleanup();
                resolve(false);
            }
        };
    });
}

/**
 * Clear thumbnail cache
 */
async function clearThumbnailCache() {
    // Show confirmation dialog
    const confirmed = await createConfirmationDialog(
        'Clear Thumbnail Cache',
        'This will delete ALL cached thumbnails. They will be regenerated on demand when needed. Are you sure you want to continue?',
        'Clear Cache',
        'Cancel'
    );

    if (!confirmed) {
        return;
    }

    try {
        // Show progress
        updateThumbnailStatus('Clearing thumbnail cache...');

        // Use correct endpoint and method
        const response = await fetch('/api/v1/thumbnails/cache', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });

        if (!response.ok) {
            throw new Error(`Clear failed: ${response.statusText}`);
        }

        const result = await response.json();
        showNotification(`Cleared ${result.deleted} thumbnails`, 'success');
        updateThumbnailStatus('Thumbnail cache cleared');

        // Update disk usage display
        if (window.updateDiskUsage) {
            await window.updateDiskUsage();
        }

    } catch (error) {
        console.error('Clear error:', error);
        showNotification(`Failed to clear cache: ${error.message}`, 'error');
        updateThumbnailStatus('Failed to clear cache');
    }
}

/**
 * Update thumbnail status display
 */
function updateThumbnailStatus(message) {
    const statusElement = document.getElementById('thumbnailStatus');
    if (statusElement) {
        statusElement.textContent = message;
    }
}

/**
 * Update thumbnail progress bar
 */
function updateThumbnailProgress(current, total) {
    const progressBar = document.getElementById('thumbnailProgressBar');
    const progressText = document.getElementById('thumbnailProgressText');

    if (progressBar && progressText) {
        const percentage = total > 0 ? Math.round((current / total) * 100) : 0;
        progressBar.style.width = `${percentage}%`;
        progressText.textContent = `${current} / ${total} (${percentage}%)`;
    }
}

/**
 * Reset thumbnail controls after generation
 */
function resetThumbnailControls() {
    document.getElementById('generateThumbnailsBtn').disabled = false;
    document.querySelector('button[onclick="scanForMissingThumbnails()"]').disabled = false;
    thumbnailGenerationTask = null;

    // Hide progress after a delay
    setTimeout(() => {
        document.getElementById('thumbnailProgressContainer').style.display = 'none';
    }, 3000);
}

/**
 * Show notification
 */
function showNotification(message, type = 'info') {
    // Use existing notification system if available
    if (window.NotificationManager) {
        window.NotificationManager.show(message, type);
    } else {
        // Fallback to console
        console.log(`[${type.toUpperCase()}] ${message}`);
    }
}


/**
 * Fetch and display disk usage statistics
 */
async function updateDiskUsage() {
    const formatBytes = (bytes) => {
        if (!Number.isFinite(bytes) || bytes <= 0) {
            return '0 B';
        }
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const exponent = Math.min(
            Math.floor(Math.log(bytes) / Math.log(1024)),
            units.length - 1,
        );
        const value = bytes / Math.pow(1024, exponent);
        return `${value.toFixed(value >= 10 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
    };

    const setCard = (valueId, countId, stats, options = {}) => {
        const valueEl = document.getElementById(valueId);
        const countEl = document.getElementById(countId);

        if (!valueEl && !countEl) {
            return;
        }

        if (!stats) {
            if (valueEl) valueEl.textContent = '--';
            if (countEl) countEl.textContent = options.emptyLabel || '--';
            return;
        }

        const sizeBytes = Number(stats.size_bytes || 0);
        if (valueEl) {
            valueEl.textContent = formatBytes(sizeBytes);
        }

        if (countEl) {
            if (options.countFormatter) {
                countEl.textContent = options.countFormatter(stats);
            } else {
                const count = Number(stats.file_count || stats.entry_count || 0);
                const label = options.countLabel || 'files';
                countEl.textContent = `${count.toLocaleString()} ${label}`;
            }
        }
    };

    try {
        const response = await fetch('/api/v1/thumbnails/disk-usage');
        if (!response.ok) {
            throw new Error(`Failed to fetch disk usage: ${response.statusText}`);
        }

        const data = await response.json();
        const summary = data.summary || {};
        const breakdown = data.breakdown || {};

        const usageElement = document.getElementById('thumbnailDiskUsage');
        const spaceUsedElement = document.getElementById('thumbnailSpaceUsed');
        const spaceTotalElement = document.getElementById('thumbnailSpaceTotal');

        if (usageElement) {
            const percent = Number.isFinite(summary.percentage_of_disk)
                ? `${summary.percentage_of_disk.toFixed(2)}% of disk`
                : '';
            usageElement.textContent = percent
                ? `Using ${formatBytes(summary.total_bytes || 0)} • ${percent}`
                : `Using ${formatBytes(summary.total_bytes || 0)}`;
        }

        setCard('diskImagesUsed', 'diskImagesCount', breakdown.images, {
            countLabel: 'files',
        });

        setCard('diskThumbsUsed', 'diskThumbsCount', breakdown.thumbnails, {
            countLabel: 'files',
        });

        setCard('diskCacheUsed', 'diskCacheEntries', breakdown.cache, {
            countFormatter: (stats) => {
                const entries = Number(stats.entry_count || 0);
                const cacheCount = Array.isArray(stats.caches) ? stats.caches.length : 0;
                if (!cacheCount) {
                    return `${entries.toLocaleString()} entries`;
                }
                return `${entries.toLocaleString()} entries • ${cacheCount} caches`;
            },
            emptyLabel: 'No cache data',
        });

        if (spaceUsedElement) {
            spaceUsedElement.textContent = `Total: ${formatBytes(summary.total_bytes || 0)}`;
        }

        if (spaceTotalElement) {
            const diskTotal = formatBytes(summary.disk_total_bytes || 0);
            const diskFree = formatBytes(summary.disk_free_bytes || 0);
            spaceTotalElement.textContent = `Disk: ${diskTotal} • Free: ${diskFree}`;
        }
    } catch (error) {
        console.error('Failed to update disk usage:', error);
        const usageElement = document.getElementById('thumbnailDiskUsage');
        if (usageElement) {
            usageElement.textContent = 'Unable to load storage information';
        }
        setCard('diskImagesUsed', 'diskImagesCount');
        setCard('diskThumbsUsed', 'diskThumbsCount');
        setCard('diskCacheUsed', 'diskCacheEntries');
        const spaceUsedElement = document.getElementById('thumbnailSpaceUsed');
        const spaceTotalElement = document.getElementById('thumbnailSpaceTotal');
        if (spaceUsedElement) spaceUsedElement.textContent = '--';
        if (spaceTotalElement) spaceTotalElement.textContent = '';
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    // Check if on settings page with thumbnails section
    const thumbnailSection = document.getElementById('thumbnails');
    if (thumbnailSection) {
        // Don't auto-scan on settings page - scanning happens on index.html
        // User can manually trigger scan using the "Scan for Missing" button
        updateThumbnailStatus('Click "Scan for Missing" to check for missing thumbnails');

        // Fetch and display disk usage
        updateDiskUsage();
    }
});

window.scanForMissingThumbnails = scanForMissingThumbnails;
window.generateMissingThumbnails = generateMissingThumbnails;
window.rebuildAllThumbnails = rebuildAllThumbnails;
window.clearThumbnailCache = clearThumbnailCache;
window.updateThumbnailStatus = updateThumbnailStatus;
window.updateThumbnailProgress = updateThumbnailProgress;
window.resetThumbnailControls = resetThumbnailControls;
window.updateDiskUsage = updateDiskUsage;
window.cancelThumbnailGeneration = cancelThumbnailGeneration;
})();
