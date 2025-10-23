/**
 * PromptManager ComfyUI Extension
 *
 * This extension enables the "Send to ComfyUI" feature by:
 * 1. Listening for registry refresh requests from the backend
 * 2. Scanning the workflow for prompt-compatible nodes
 * 3. Registering available nodes with the backend
 * 4. Receiving prompt update events and updating node widgets
 *
 * Compatible Nodes (tracked nodes only):
 * - CLIPTextEncode (standard ComfyUI)
 * - PromptManager (V1)
 * - PromptManagerV2
 * - PromptManagerPositive
 * - PromptManagerNegative
 * - PromptManagerText (V1 text-only)
 * - PromptManagerV2Text
 * - PromptManagerPositiveText
 * - PromptManagerNegativeText
 *
 * Note: PromptManagerDetailer is excluded (not tracked)
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Scanning lock to prevent concurrent registry updates
let isScanning = false;

/**
 * Get all nodes from the current graph
 */
function getAllGraphNodes(graph) {
    if (!graph) return [];

    const nodes = [];
    for (const node of graph._nodes || []) {
        nodes.push(node);
    }
    return nodes;
}

/**
 * Get a specific node from a graph by ID
 */
function getNodeFromGraph(graphId, nodeId) {
    // For now, we only support root graph
    // Future: support subgraphs
    const graph = app.graph;
    if (!graph) return null;

    const numericNodeId = typeof nodeId === 'string' ? Number(nodeId) : nodeId;
    return graph._nodes_by_id?.[numericNodeId] || null;
}

/**
 * Check if a node can receive prompts
 */
function isPromptCompatibleNode(node) {
    if (!node || !node.type) return false;

    const compatibleTypes = [
        'CLIPTextEncode',
        'PromptManager',
        'PromptManagerV2',
        'PromptManagerPositive',
        'PromptManagerNegative',
        'PromptManagerText',
        'PromptManagerV2Text',
        'PromptManagerPositiveText',
        'PromptManagerNegativeText',
    ];

    return compatibleTypes.includes(node.type);
}

/**
 * Get the text widget from a node
 */
function getTextWidget(node) {
    if (!node || !node.widgets) return null;

    // Find the text widget - usually named 'text', 'prompt', or 'positive_prompt'
    for (const widget of node.widgets) {
        if (widget.type === 'customtext' || widget.type === 'text') {
            return widget;
        }
        if (widget.name === 'text' || widget.name === 'prompt' || widget.name === 'positive_prompt') {
            return widget;
        }
    }

    // Fallback: return first string widget
    for (const widget of node.widgets) {
        if (widget.type === 'string') {
            return widget;
        }
    }

    return null;
}

/**
 * Scan the current workflow and register prompt-compatible nodes
 */
async function scanAndRegisterNodes() {
    // Prevent concurrent scans that race and clear each other's data
    if (isScanning) {
        console.log('[PromptManager] Scan already in progress, skipping');
        return;
    }

    isScanning = true;
    try {
        const graph = app.graph;
        if (!graph) {
            console.warn('[PromptManager] No active graph to scan');
            return;
        }

        const nodes = getAllGraphNodes(graph);
        console.log(`[PromptManager] Scanning ${nodes.length} total nodes in graph`);
        const compatibleNodes = [];

        for (const node of nodes) {
            console.log(`[PromptManager] Checking node: ${node.type} (id: ${node.id})`);
            if (isPromptCompatibleNode(node)) {
                console.log(`[PromptManager] ✓ Compatible node found: ${node.type}`);
                const widget = getTextWidget(node);
                const widgetNames = node.widgets ? node.widgets.map(w => w.name) : [];

                const nodeInfo = {
                    node_id: node.id,
                    graph_id: 'root', // Currently only support root graph
                    type: node.type,
                    title: node.title || node.type,
                    widgets: widgetNames,
                    bgcolor: node.bgcolor || null,
                    graph_name: graph.title || 'root',
                    metadata: {
                        has_text_widget: widget !== null,
                        widget_count: node.widgets?.length || 0,
                    }
                };

                compatibleNodes.push(nodeInfo);
            }
        }

        console.log(`[PromptManager] Found ${compatibleNodes.length} prompt-compatible nodes`);

        // Register nodes with the backend
        if (compatibleNodes.length > 0) {
            try {
                const response = await fetch('/prompt_manager/api/register-nodes', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        nodes: compatibleNodes
                    })
                });

                const result = await response.json();
                if (result.success) {
                    console.log(`[PromptManager] Registered ${result.count} nodes successfully`);
                } else {
                    console.error('[PromptManager] Failed to register nodes:', result.error);
                }
            } catch (error) {
                console.error('[PromptManager] Error registering nodes:', error);
            }
        } else {
            // Register empty array to clear the registry
            try {
                await fetch('/prompt_manager/api/register-nodes', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        nodes: []
                    })
                });
            } catch (error) {
                console.error('[PromptManager] Error clearing registry:', error);
            }
        }
    } finally {
        isScanning = false;
    }
}

/**
 * Handle prompt update from backend
 */
function handlePromptUpdate(message) {
    const nodeId = message?.node_id;
    const graphId = message?.graph_id || 'root';
    const promptText = message?.prompt_text || '';
    const mode = message?.mode || 'append';
    const type = message?.type || 'positive'; // 'positive' or 'negative'

    console.log(`[PromptManager] Received prompt update for node ${graphId}:${nodeId}, mode: ${mode}, type: ${type}`);

    if (nodeId === undefined || nodeId === null) {
        console.warn('[PromptManager] No node_id in prompt update message');
        return;
    }

    const numericNodeId = typeof nodeId === 'string' ? Number(nodeId) : nodeId;

    // Get the node
    const node = getNodeFromGraph(graphId, numericNodeId);
    if (!node) {
        console.warn(`[PromptManager] Node not found: ${graphId}:${nodeId}`);
        return;
    }

    // Get the text widget
    const widget = getTextWidget(node);
    if (!widget) {
        console.warn(`[PromptManager] No text widget found for node ${graphId}:${nodeId}`);
        return;
    }

    // Update the widget value
    const currentValue = widget.value || '';

    if (mode === 'replace') {
        widget.value = promptText;
    } else {
        // Append mode - add a space if current value isn't empty
        widget.value = currentValue.trim()
            ? `${currentValue.trim()} ${promptText}`
            : promptText;
    }

    // Trigger the callback to update the node
    if (typeof widget.callback === 'function') {
        widget.callback(widget.value);
    }

    // Mark the graph as modified
    if (app.graph) {
        app.graph.change();
    }

    console.log(`[PromptManager] Updated node ${graphId}:${nodeId} with ${mode} mode`);
}

/**
 * Register the PromptManager extension with ComfyUI
 */
console.log('[PromptManager] Extension file loaded - registering...');

app.registerExtension({
    name: "PromptManager.Integration",

    async setup() {
        console.log('[PromptManager] Extension setup started');
        console.log('[PromptManager] app.graph exists:', !!app.graph);
        console.log('[PromptManager] api object:', !!api);

        // Listen for registry refresh requests from backend
        api.addEventListener("prompt_registry_refresh", async (event) => {
            console.log('[PromptManager] Registry refresh requested');
            await scanAndRegisterNodes();
        });

        // Listen for prompt update events from backend
        api.addEventListener("prompt_update", (event) => {
            handlePromptUpdate(event.detail || {});
        });

        console.log('[PromptManager] Event listeners registered');

        // Do initial scan when workflow loads
        // Wait a bit for the graph to be fully loaded
        setTimeout(async () => {
            console.log('[PromptManager] Initial scan timer fired');
            console.log('[PromptManager] - app.graph exists:', !!app.graph);
            if (app.graph) {
                console.log('[PromptManager] - app.graph._nodes:', app.graph._nodes?.length || 0, 'nodes');
            }
            if (app.graph && app.graph._nodes && app.graph._nodes.length > 0) {
                console.log('[PromptManager] Performing initial node scan');
                await scanAndRegisterNodes();
            } else {
                console.log('[PromptManager] Skipping initial scan - no nodes in graph yet');
            }
        }, 1000);
    },

    // Hook into graph changes to update registry
    async graphChanged(graph) {
        // Debounce the scan to avoid excessive API calls
        if (this.scanTimeout) {
            clearTimeout(this.scanTimeout);
        }

        this.scanTimeout = setTimeout(async () => {
            if (graph && graph._nodes) {
                console.log('[PromptManager] Graph changed, updating node registry');
                await scanAndRegisterNodes();
            }
        }, 500); // Wait 500ms after last graph change
    },

    // Hook into node creation to update registry
    async nodeCreated(node) {
        // Only scan if it's a compatible node type
        if (isPromptCompatibleNode(node)) {
            console.log(`[PromptManager] Compatible node created: ${node.type}`);

            // Debounce the scan
            if (this.scanTimeout) {
                clearTimeout(this.scanTimeout);
            }

            this.scanTimeout = setTimeout(async () => {
                await scanAndRegisterNodes();
            }, 500);
        }
    },

    // Hook into node removal to update registry
    async nodeRemoved(node) {
        // Only scan if it was a compatible node type
        if (isPromptCompatibleNode(node)) {
            console.log(`[PromptManager] Compatible node removed: ${node.type}`);

            // Debounce the scan
            if (this.scanTimeout) {
                clearTimeout(this.scanTimeout);
            }

            this.scanTimeout = setTimeout(async () => {
                await scanAndRegisterNodes();
            }, 500);
        }
    }
});

console.log('[PromptManager] Extension loaded');