/**
 * FFmpeg Settings Functions for Settings Page
 *
 * Handles FFmpeg path detection and testing
 */

(function () {
  'use strict';

  if (typeof window === 'undefined') {
    return;
  }
  const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
  if (!pmActive) {
    console.info('[PromptManager] settings-ffmpeg skipped outside PromptManager UI context');
    const noop = () => {};
    const noopAsync = async () => {};
    window.detectFFmpeg = noopAsync;
    window.testFFmpeg = noopAsync;
    window.saveFFmpegPath = noopAsync;
    window.updateFFmpegStatus = noop;
    return;
  }

/**
 * Detect FFmpeg automatically
 */
async function detectFFmpeg(options = {}) {
    const { silent = false } = options;
    const statusElement = document.getElementById('ffmpegStatus');
    const pathInput = document.getElementById('ffmpegPath');

    if (statusElement) {
        statusElement.textContent = 'Detecting FFmpeg...';
        statusElement.classList.remove('status-ok', 'status-warn', 'status-error');
    }

    try {
        const response = await fetch('/api/v1/thumbnails/test-ffmpeg', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });

        if (!response.ok) {
            throw new Error(`Detection failed: ${response.statusText}`);
        }

        const result = await response.json();
        const candidate = result.best_candidate || (result.candidates || []).find(c => c.reachable);

        if (result.success && candidate) {
            if (pathInput) {
                pathInput.value = candidate.path;
            }
            if (statusElement) {
                statusElement.textContent = `FFmpeg detected at ${candidate.path} — ${candidate.version || 'version unknown'}`;
                statusElement.classList.add('status-ok');
            }
            if (!silent) {
                showNotification('FFmpeg detected successfully', 'success');
            }

            await saveFFmpegPath(candidate.path);

            if (typeof window.saveSettings === 'function' && window.loadSettings) {
                const settings = await window.loadSettings();
                settings.ffmpegPath = candidate.path;
                await window.saveSettings(settings, { silent: true });
            }
        } else {
            if (statusElement) {
                const detail = candidate?.summary || (result.candidates && result.candidates[0]?.summary) || 'Please install or specify a path';
                statusElement.textContent = `FFmpeg not found. ${detail}`;
                statusElement.classList.add('status-error');
            }
            if (pathInput) {
                pathInput.value = '';
                pathInput.placeholder = 'Enter path to ffmpeg executable';
            }
            if (!silent) {
                showNotification('FFmpeg not found. Please install FFmpeg or specify the path manually.', 'warning');
            }
        }

    } catch (error) {
        console.error('FFmpeg detection error:', error);
        if (statusElement) {
            statusElement.textContent = `Detection error: ${error.message}`;
            statusElement.classList.add('status-error');
        }
        if (!silent) {
            showNotification(`Failed to detect FFmpeg: ${error.message}`, 'error');
        }
}
}

/**
 * Test FFmpeg with current path
 */
async function testFFmpeg() {
    const pathInput = document.getElementById('ffmpegPath');
    const statusElement = document.getElementById('ffmpegStatus');

    if (!pathInput || !pathInput.value) {
        showNotification('Please enter an FFmpeg path to test', 'warning');
        return;
    }

    if (statusElement) {
        statusElement.textContent = 'Testing FFmpeg...';
        statusElement.classList.remove('status-ok', 'status-warn', 'status-error');
    }

    try {
        const response = await fetch('/api/v1/thumbnails/test-ffmpeg', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ffmpeg_path: pathInput.value
            })
        });

        if (!response.ok) {
            throw new Error(`Test failed: ${response.statusText}`);
        }

        const result = await response.json();
        const candidate = result.candidate || (result.best_candidate && result.best_candidate.reachable ? result.best_candidate : null);

        if (result.success && candidate) {
            if (statusElement) {
                statusElement.textContent = `FFmpeg working at ${candidate.path} — ${candidate.version || 'version unknown'}`;
                statusElement.classList.add('status-ok');
            }
            showNotification('FFmpeg is working correctly', 'success');

            await saveFFmpegPath(candidate.path);

            if (typeof window.saveSettings === 'function' && window.loadSettings) {
                const settings = await window.loadSettings();
                settings.ffmpegPath = candidate.path;
                await window.saveSettings(settings, { silent: true });
            }
        } else {
            if (statusElement) {
                statusElement.textContent = `FFmpeg not working: ${result.error || 'Unknown error'}`;
                statusElement.classList.add('status-error');
            }
            showNotification(`FFmpeg test failed: ${result.error || 'Unknown error'}`, 'error');
        }

    } catch (error) {
        console.error('FFmpeg test error:', error);
        if (statusElement) {
            statusElement.textContent = `Test error: ${error.message}`;
            statusElement.classList.add('status-error');
        }
        showNotification(`Failed to test FFmpeg: ${error.message}`, 'error');
    }
}

/**
 * Save FFmpeg path to settings
 */
async function saveFFmpegPath(path) {
    try {
        const response = await fetch('/api/v1/settings/thumbnails', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ffmpeg_path: path
            })
        });

        if (!response.ok) {
            throw new Error(`Failed to save settings: ${response.statusText}`);
        }

        console.log('FFmpeg path saved successfully');
    } catch (error) {
        console.error('Failed to save FFmpeg path:', error);
    }
}

/**
 * Show notification
 */
function showNotification(message, type = 'info') {
    // Use existing notification system if available
    if (window.NotificationManager) {
        window.NotificationManager.show(message, type);
    } else if (window.NotificationService) {
        window.NotificationService.show(message, type);
    } else {
        // Fallback to console
        console.log(`[${type.toUpperCase()}] ${message}`);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    // Check if on settings page with video section
    const videoSection = document.getElementById('video');
    if (videoSection) {
        // Add event listener for path changes
        const pathInput = document.getElementById('ffmpegPath');
        if (pathInput) {
            let saveTimeout;
            pathInput.addEventListener('input', () => {
                // Debounce saving
                clearTimeout(saveTimeout);
                saveTimeout = setTimeout(() => {
                    if (pathInput.value) {
                        saveFFmpegPath(pathInput.value);
                    }
                }, 1000);
            });

            if (!pathInput.value) {
                detectFFmpeg({ silent: true });
            } else {
                updateFFmpegStatus(pathInput.value);
            }
        }
    }
});

async function updateFFmpegStatus(currentPath) {
    const statusElement = document.getElementById('ffmpegStatus');
    if (!statusElement || !currentPath) {
        return;
    }
    statusElement.textContent = `Using FFmpeg: ${currentPath}`;
    statusElement.classList.remove('status-error');
    statusElement.classList.add('status-ok');
}

window.detectFFmpeg = detectFFmpeg;
window.testFFmpeg = testFFmpeg;
window.saveFFmpegPath = saveFFmpegPath;
window.updateFFmpegStatus = updateFFmpegStatus;
})();
