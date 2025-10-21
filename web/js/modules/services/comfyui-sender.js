/**
 * ComfyUI Sender Service
 *
 * Handles sending prompts from the dashboard to ComfyUI nodes.
 * Provides UI for node selection and feedback via toast notifications.
 */

(function() {
  'use strict';

  /**
   * ComfyUI Sender Service
   */
  const ComfyUISender = (function() {
    let nodeRegistry = null;
    let registryCache = null;
    let registryCacheTime = 0;
    const CACHE_DURATION = 30000; // 30 seconds

    /**
     * Get available ComfyUI nodes from registry
     */
    async function getAvailableNodes(forceRefresh = false) {
      const now = Date.now();

      // Return cached registry if still fresh and not forcing refresh
      if (!forceRefresh && registryCache && (now - registryCacheTime) < CACHE_DURATION) {
        return registryCache;
      }

      try {
        const response = await fetch('/prompt_manager/api/get-registry');

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const result = await response.json();

        if (!result.success) {
          throw new Error(result.error || 'Failed to get node registry');
        }

        // Cache the result
        registryCache = result.data;
        registryCacheTime = now;

        return result.data;
      } catch (error) {
        console.error('[ComfyUISender] Error fetching node registry:', error);
        throw error;
      }
    }

    /**
     * Send prompt to specified ComfyUI nodes
     */
    async function sendPromptToNodes(promptId, nodeIds, mode = 'append', type = 'both') {
      if (!promptId) {
        throw new Error('Prompt ID is required');
      }

      if (!nodeIds || nodeIds.length === 0) {
        throw new Error('At least one node must be selected');
      }

      try {
        const response = await fetch('/prompt_manager/api/send-prompt', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            prompt_id: promptId,
            node_ids: nodeIds,
            mode: mode,
            type: type,
          })
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const result = await response.json();

        if (!result.success) {
          throw new Error(result.error || 'Failed to send prompt');
        }

        return result;
      } catch (error) {
        console.error('[ComfyUISender] Error sending prompt:', error);
        throw error;
      }
    }

    /**
     * Show node selector modal
     */
    function showNodeSelectorModal(nodes, onSelect) {
      // Create modal
      const modal = document.createElement('div');
      modal.className = 'modal node-selector-modal';
      modal.style.display = 'flex';

      modal.innerHTML = `
        <div class="modal-backdrop"></div>
        <div class="modal-content">
          <div class="modal-header">
            <h2 class="modal-title">Send to ComfyUI Node</h2>
            <button class="modal-close" aria-label="Close modal">&times;</button>
          </div>
          <div class="modal-body">
            <p class="node-selector-description">
              Select which ComfyUI node(s) should receive this prompt:
            </p>
            <div class="node-selector-options"></div>
            <div class="node-selector-mode">
              <label class="node-selector-mode-label">
                <input type="checkbox" class="mode-checkbox" value="replace">
                <span>Replace existing text (default: append)</span>
              </label>
            </div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-secondary modal-cancel">Cancel</button>
            <button class="btn btn-primary modal-submit">Send Prompt</button>
          </div>
        </div>
      `;

      document.body.appendChild(modal);

      const optionsContainer = modal.querySelector('.node-selector-options');
      const nodesArray = Object.values(nodes);

      // Create checkbox for each node
      nodesArray.forEach(node => {
        const option = document.createElement('label');
        option.className = 'node-selector-option';

        option.innerHTML = `
          <input type="checkbox" name="node" value="${node.unique_id}" checked>
          <div class="node-selector-info">
            <div class="node-selector-title">${node.title || node.type}</div>
            <div class="node-selector-meta">
              <span class="node-selector-type">${node.type}</span>
              <span class="node-selector-id">#${node.id}</span>
            </div>
          </div>
        `;

        optionsContainer.appendChild(option);
      });

      // Event handlers
      const closeModal = () => {
        modal.remove();
      };

      const submitSelection = () => {
        const checkboxes = modal.querySelectorAll('input[name="node"]:checked');
        const selectedNodeIds = Array.from(checkboxes).map(cb => cb.value);
        const modeCheckbox = modal.querySelector('.mode-checkbox');
        const mode = modeCheckbox.checked ? 'replace' : 'append';

        if (selectedNodeIds.length === 0) {
          window.showToast?.('Please select at least one node', 'warning');
          return;
        }

        closeModal();
        onSelect(selectedNodeIds, mode);
      };

      modal.querySelector('.modal-close').addEventListener('click', closeModal);
      modal.querySelector('.modal-cancel').addEventListener('click', closeModal);
      modal.querySelector('.modal-submit').addEventListener('click', submitSelection);
      modal.querySelector('.modal-backdrop').addEventListener('click', closeModal);

      // Close on Escape key
      const handleEscape = (e) => {
        if (e.key === 'Escape') {
          closeModal();
          document.removeEventListener('keydown', handleEscape);
        }
      };
      document.addEventListener('keydown', handleEscape);
    }

    /**
     * Main entry point - send prompt to ComfyUI
     */
    async function sendToComfyUI(prompt, options = {}) {
      const shiftPressed = options.shiftPressed || false;
      const type = options.type || 'both'; // 'positive', 'negative', or 'both'

      try {
        // Show loading toast
        const loadingToast = window.showToast?.(
          'Checking available ComfyUI nodes...',
          'info',
          { duration: 0, clickToDismiss: true }
        );

        // Get available nodes
        const registry = await getAvailableNodes();

        // Dismiss loading toast
        if (loadingToast && window.notificationService) {
          window.notificationService.dismiss(loadingToast, { immediate: true });
        }

        if (!registry || !registry.nodes) {
          window.showToast?.(
            'Failed to connect to ComfyUI. Make sure ComfyUI is running.',
            'error'
          );
          return;
        }

        const nodes = registry.nodes;
        const nodeCount = Object.keys(nodes).length;

        if (nodeCount === 0) {
          window.showToast?.(
            'No compatible nodes found in ComfyUI workflow. Add a CLIPTextEncode or PromptManager node.',
            'warning',
            { duration: 6 }
          );
          return;
        }

        // Determine mode based on shift key
        const mode = shiftPressed ? 'replace' : 'append';

        // If only one node, send directly (unless shift is pressed for mode selection)
        if (nodeCount === 1 && !shiftPressed) {
          const nodeId = Object.keys(nodes)[0];
          const node = nodes[nodeId];

          const typeLabel = type === 'both' ? 'both prompts' : `${type} prompt`;
          window.showToast?.(
            `Sending ${typeLabel} to "${node.title || node.type}"...`,
            'info',
            { duration: 2 }
          );

          try {
            const result = await sendPromptToNodes(prompt.id, [nodeId], mode, type);

            window.showToast?.(
              `✅ ${typeLabel.charAt(0).toUpperCase() + typeLabel.slice(1)} sent to ComfyUI (${mode} mode)`,
              'success'
            );
          } catch (error) {
            window.showToast?.(
              `Failed to send prompt: ${error.message}`,
              'error'
            );
          }
        } else {
          // Multiple nodes or shift pressed - show selector
          showNodeSelectorModal(nodes, async (selectedNodeIds, selectedMode) => {
            const typeLabel = type === 'both' ? 'prompts' : `${type} prompt`;
            window.showToast?.(
              `Sending ${typeLabel} to ${selectedNodeIds.length} node(s)...`,
              'info',
              { duration: 2 }
            );

            try {
              const result = await sendPromptToNodes(
                prompt.id,
                selectedNodeIds,
                selectedMode,
                type
              );

              const message = result.sent_count === selectedNodeIds.length
                ? `✅ Prompt sent to ${result.sent_count} node(s) (${selectedMode} mode)`
                : `⚠️ Sent to ${result.sent_count} of ${selectedNodeIds.length} node(s)`;

              window.showToast?.(message, 'success');

              if (result.failed_nodes && result.failed_nodes.length > 0) {
                console.warn('[ComfyUISender] Some nodes failed:', result.failed_nodes);
              }
            } catch (error) {
              window.showToast?.(
                `Failed to send prompt: ${error.message}`,
                'error'
              );
            }
          });
        }
      } catch (error) {
        window.showToast?.(
          `Error: ${error.message}`,
          'error'
        );
      }
    }

    // Public API
    return {
      sendToComfyUI,
      getAvailableNodes,
      sendPromptToNodes,
    };
  })();

  // Export to window
  if (typeof window !== 'undefined') {
    window.ComfyUISender = ComfyUISender;
  }

  // Export for module systems
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ComfyUISender;
  }
})();
