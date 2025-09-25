// PromptManager/web/prompt_manager.js

import { app } from "../../scripts/app.js";

// Determine best API base prefix at runtime to support proxies (e.g., /api)
let __pmApiBase = null;
let __pmResolving = null;

async function pmResolveApiBase() {
  if (__pmApiBase) return __pmApiBase;
  if (__pmResolving) return __pmResolving;
  __pmResolving = (async () => {
    // Try direct path first
    try {
      const r = await fetch("/prompt_manager/health", { cache: "no-store" });
      if (r.ok) {
        __pmApiBase = "/prompt_manager";
        return __pmApiBase;
      }
    } catch (_) {}
    // Try /api prefixed path
    try {
      const r2 = await fetch("/api/prompt_manager/health", { cache: "no-store" });
      if (r2.ok) {
        __pmApiBase = "/api/prompt_manager";
        return __pmApiBase;
      }
    } catch (_) {}
    // Fallback to direct
    __pmApiBase = "/prompt_manager";
    return __pmApiBase;
  })();
  return __pmResolving;
}

async function pmUrl(path) {
  const base = await pmResolveApiBase();
  if (!path.startsWith("/")) path = "/" + path;
  return `${base}${path}`;
}

async function pmFetch(path, options) {
  if (path.startsWith('/api/')) {
    return fetch(path, options);
  }

  const url = await pmUrl(path);
  let res;
  try {
    res = await fetch(url, options);
  } catch (e) {
    // As a last resort try the alternate base if resolution got it wrong
    const alt = url.startsWith("/api/") ? url.replace("/api/", "/") : url.replace("/", "/api/");
    try { return await fetch(alt, options); } catch (_) { throw e; }
  }
  if (!res.ok) {
    // Attempt automatic fallback once on 404/502
    if (res.status === 404 || res.status === 502) {
      const base = await pmResolveApiBase();
      const altBase = base.startsWith("/api/") ? base.replace("/api/", "/") : "/api" + base;
      const altUrl = `${altBase}${path.startsWith("/") ? path : "/" + path}`;
      const altRes = await fetch(altUrl, options).catch(() => null);
      if (altRes && altRes.ok) return altRes;
    }
  }
  return res;
}

app.registerExtension({
  name: "PromptManager.UI",

  async beforeRegisterNodeDef(nodeType, nodeData, app) {
    if (nodeData.name === "PromptManager") {
      console.log(`[PromptManager] Patching node type for custom UI: ${nodeData.name}`);

      // Store original methods
      const onNodeCreated = nodeType.prototype.onNodeCreated;

      // Override onNodeCreated to add custom UI
      nodeType.prototype.onNodeCreated = function () {
        if (onNodeCreated) {
          onNodeCreated.apply(this, arguments);
        }

        // Initialize properties for search state (non-serialized runtime state)
        this.properties = this.properties || {};
        this.properties.resultTimeout = 3; // Default 3 seconds
        this.properties.showTestButton = false; // Default hide test button
        this.properties.webuiDisplayMode = "popup"; // Default popup mode
        
        // Runtime-only state (not serialized to workflow)
        this._searchResults = [];
        this._selectedPromptIndex = -1;
        this.resultHideTimer = null;
        
        // Store node type for conditional behavior
        this.nodeTypeName = nodeData.name;

        // Load settings from API
        this.loadSettings();

        // Create DOM widget container with unique ID for scoped styling
        const container = document.createElement("div");
        const uniqueId = `pm-${Math.random().toString(36).substring(2, 9)}`;
        container.id = uniqueId;
        
        // Add inline styles for perfect sizing like KikoLocalImageLoader
        const styleSheet = document.createElement("style");
        styleSheet.textContent = `
          #${uniqueId} {
            width: 100%;
            height: 100%;
            padding: 8px;
            background-color: #2a2a2a;
            border-radius: 4px;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            overflow: hidden;
          }
          #${uniqueId} > div {
            width: 100%;
            height: 100%;
            display: flex;
            flex-direction: column;
            gap: 8px;
          }
        `;
        document.head.appendChild(styleSheet);
        
        // Store style reference for cleanup
        this._styleSheet = styleSheet;

        this.createSearchUI(container);

        // Add as DOM widget with proper configuration
        this.searchWidget = this.addDOMWidget(
          "prompt_manager_search_ui",
          "div",
          container,
          {
            serialize: false, // Don't save UI state
          }
        );

        // Set initial node size, but preserve user resizes
        if (!this._userHasResized) {
          // For nodes with text widgets, we need more height
          const baseHeight = 400;
          const widgetHeight = this.widgets ? this.widgets.length * 30 : 0;
          this.size = [600, baseHeight + widgetHeight]; // Dynamic size based on widgets
          this.setSize(this.size); // IMPORTANT: Actually apply the size
        }

        // Hook into resize to track user changes
        const originalOnResize = this.onResize;
        this.onResize = function (size) {
          this._userHasResized = true;
          console.log("[PromptManager] User resized to:", size);
          if (originalOnResize) {
            originalOnResize.call(this, size);
          }
        };

        // Hook into serialization to preserve resize flag and prevent runtime data from being saved
        const originalSerialize = this.serialize;
        this.serialize = function () {
          const data = originalSerialize ? originalSerialize.call(this) : {};
          data._userHasResized = this._userHasResized;
          
          // Ensure search results are never saved to workflow
          if (data.properties && data.properties.searchResults) {
            delete data.properties.searchResults;
          }
          if (data.properties && data.properties.selectedPromptIndex) {
            delete data.properties.selectedPromptIndex;
          }
          
          return data;
        };

        // Hook into configure to restore resize flag
        const originalConfigure = this.configure;
        this.configure = function (data) {
          if (originalConfigure) {
            originalConfigure.call(this, data);
          }
          if (data._userHasResized) {
            this._userHasResized = data._userHasResized;
          }
        };

        this.setDirtyCanvas(true, true);
      };

      // Override onRemoved to cleanup styles
      const onRemoved = nodeType.prototype.onRemoved;
      nodeType.prototype.onRemoved = function () {
        // Cleanup styles when node is removed
        if (this._styleSheet && this._styleSheet.parentNode) {
          this._styleSheet.parentNode.removeChild(this._styleSheet);
        }
        
        // Call original onRemoved if it exists
        if (onRemoved) {
          onRemoved.apply(this, arguments);
        }
      };

      // Method to create search UI elements
      nodeType.prototype.createSearchUI = function (container) {
        container.innerHTML = "";

        // Main wrapper to match KikoLocalImageLoader structure
        const wrapper = document.createElement("div");
        wrapper.style.width = "100%";
        wrapper.style.height = "100%";
        wrapper.style.display = "flex";
        wrapper.style.flexDirection = "column";
        wrapper.style.gap = "8px";

        // Search controls section
        const searchSection = document.createElement("div");
        searchSection.style.display = "flex";
        searchSection.style.flexDirection = "column";
        searchSection.style.height = "100%";
        searchSection.style.gap = "8px";

        // Search buttons row
        const buttonRow = document.createElement("div");
        buttonRow.style.display = "flex";
        buttonRow.style.gap = "8px";
        buttonRow.style.flexShrink = "0"; // Don't shrink buttons

        // Search button
        const searchButton = document.createElement("button");
        searchButton.textContent = "🔍 Search";
        searchButton.style.flex = "1";
        searchButton.style.padding = "6px 12px";
        searchButton.style.backgroundColor = "#4a90e2";
        searchButton.style.color = "white";
        searchButton.style.border = "none";
        searchButton.style.borderRadius = "4px";
        searchButton.style.cursor = "pointer";
        searchButton.style.fontSize = "12px";
        searchButton.addEventListener("click", () => this.performSearch());
        buttonRow.appendChild(searchButton);

        // Recent prompts button
        const recentButton = document.createElement("button");
        recentButton.textContent = "📋 Recent";
        recentButton.style.flex = "1";
        recentButton.style.padding = "6px 12px";
        recentButton.style.backgroundColor = "#7b68ee";
        recentButton.style.color = "white";
        recentButton.style.border = "none";
        recentButton.style.borderRadius = "4px";
        recentButton.style.cursor = "pointer";
        recentButton.style.fontSize = "12px";
        recentButton.addEventListener("click", () => this.loadRecentPrompts());
        buttonRow.appendChild(recentButton);

        // Web UI button
        const webUIButton = document.createElement("button");
        webUIButton.textContent = "🌐 Web UI";
        webUIButton.style.flex = "1";
        webUIButton.style.padding = "6px 12px";
        webUIButton.style.backgroundColor = "#50c878";
        webUIButton.style.color = "white";
        webUIButton.style.border = "none";
        webUIButton.style.borderRadius = "4px";
        webUIButton.style.cursor = "pointer";
        webUIButton.style.fontSize = "12px";
        webUIButton.addEventListener("click", () => this.openWebInterface());
        buttonRow.appendChild(webUIButton);

        searchSection.appendChild(buttonRow);

        // Add test API button for debugging (only if enabled in settings)
        if (this.properties.showTestButton) {
          const testRow = document.createElement("div");
          testRow.style.marginBottom = "8px";

          const testButton = document.createElement("button");
          testButton.textContent = "🔧 Test API";
          testButton.style.width = "100%";
          testButton.style.padding = "4px 8px";
          testButton.style.backgroundColor = "#666";
          testButton.style.color = "white";
          testButton.style.border = "none";
          testButton.style.borderRadius = "3px";
          testButton.style.cursor = "pointer";
          testButton.style.fontSize = "10px";
          testButton.addEventListener("click", () => this.testApiConnection());
          testRow.appendChild(testButton);

          searchSection.appendChild(testRow);
        }

        // Results section
        const resultsSection = document.createElement("div");
        resultsSection.style.flex = "1"; // Take remaining space
        resultsSection.style.minHeight = "100px"; // Minimum height
        resultsSection.style.overflowY = "auto";
        resultsSection.style.border = "1px solid #444";
        resultsSection.style.borderRadius = "4px";
        resultsSection.style.backgroundColor = "#333";
        resultsSection.style.padding = "8px";
        resultsSection.style.fontSize = "11px";
        resultsSection.style.fontFamily = "monospace";
        resultsSection.innerHTML =
          '<div style="color: #999; text-align: center; padding: 20px;">💡 Click Search or Recent to find prompts</div>';

        this.resultsSection = resultsSection;
        searchSection.appendChild(resultsSection);

        // Add search section to wrapper, then wrapper to container
        wrapper.appendChild(searchSection);
        container.appendChild(wrapper);
      };

      // Method to perform search
      nodeType.prototype.performSearch = async function () {
        try {
          this.resultsSection.innerHTML =
            '<div style="color: #999; text-align: center;">🔍 Searching database...</div>';

          // Get search criteria from node widgets (only search_text available now)
          const searchText =
            this.widgets?.find((w) => w.name === "search_text")?.value || "";

          // Validate search criteria
          if (!searchText.trim()) {
            this.resultsSection.innerHTML =
              '<div style="color: #f9a825; text-align: center;">⚠️ Please enter search text</div>';
            return;
          }

          // Call the backend search function
          const response = await this.callNodeMethod("search_prompts", {
            search_text: searchText,
          });

          this.displayResults(response.results || []);

          // Show success notification
          if (response.results && response.results.length > 0) {
            this.showNotification(
              `Found ${response.results.length} prompts`,
              "success",
            );
          }
        } catch (error) {
          console.error("[PromptManager] Search error:", error);
          this.resultsSection.innerHTML = `<div style="color: #f88; text-align: center;">❌ Search error: ${error.message}</div>`;
          this.showNotification(`Search failed: ${error.message}`, "error");
        }
      };

      // Method to load recent prompts
      nodeType.prototype.loadRecentPrompts = async function () {
        try {
          this.resultsSection.innerHTML =
            '<div style="color: #999; text-align: center;">📋 Loading recent prompts...</div>';

          const response = await this.callNodeMethod("get_recent_prompts", {
            limit: 20,
          });
          this.displayResults(response.results || []);

          // Show success notification
          if (response.results && response.results.length > 0) {
            this.showNotification(
              `Loaded ${response.results.length} recent prompts`,
              "success",
            );
          } else {
            this.showNotification("No recent prompts found", "info");
          }
        } catch (error) {
          console.error("[PromptManager] Recent prompts error:", error);
          this.resultsSection.innerHTML = `<div style="color: #f88; text-align: center;">❌ Error loading recent prompts: ${error.message}</div>`;
          this.showNotification(
            `Failed to load recent prompts: ${error.message}`,
            "error",
          );
        }
      };

      // Method to load settings from API
      nodeType.prototype.loadSettings = async function () {
        try {
          const response = await pmFetch('/api/v1/system/settings');
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }

          const payload = await response.json();
          const settings = payload?.data || payload?.settings;
          if (!settings) {
            return;
          }

          this.properties.resultTimeout = settings.result_timeout ?? 3;
          this.properties.showTestButton = settings.show_test_button ?? false;
          this.properties.webuiDisplayMode = settings.webui_display_mode || 'newtab';
        } catch (error) {
          console.warn(
            '[PromptManager] Could not load settings, using defaults',
            error,
          );
        }
      };

      // Method to start auto-hide timer
      nodeType.prototype.startAutoHideTimer = function () {
        // Clear existing timer
        if (this.resultHideTimer) {
          clearTimeout(this.resultHideTimer);
        }

        // Only set timer if timeout > 0
        if (this.properties.resultTimeout > 0) {
          this.resultHideTimer = setTimeout(() => {
            this.hideResults();
          }, this.properties.resultTimeout * 1000);
        }
      };

      // Method to hide results
      nodeType.prototype.hideResults = function () {
        if (this.resultsSection) {
          // Completely hide the results section
          this.resultsSection.style.display = "none";

          // Create a restore button in place of the results
          const restoreButton = document.createElement("div");
          restoreButton.style.padding = "20px";
          restoreButton.style.backgroundColor = "#333";
          restoreButton.style.border = "1px solid #444";
          restoreButton.style.borderRadius = "4px";
          restoreButton.style.textAlign = "center";
          restoreButton.style.cursor = "pointer";
          restoreButton.style.color = "#999";
          restoreButton.style.fontSize = "12px";
          restoreButton.innerHTML =
            "🔄 Results auto-hidden<br><small>Click to restore</small>";

          // Add hover effect
          restoreButton.addEventListener("mouseenter", () => {
            restoreButton.style.backgroundColor = "#444";
            restoreButton.style.color = "#ccc";
          });
          restoreButton.addEventListener("mouseleave", () => {
            restoreButton.style.backgroundColor = "#333";
            restoreButton.style.color = "#999";
          });

          // Click to restore
          restoreButton.addEventListener("click", () => {
            this.showResults();
          });

          // Store reference and insert after results section
          this.restoreButton = restoreButton;
          this.resultsSection.parentNode.insertBefore(
            restoreButton,
            this.resultsSection.nextSibling,
          );
        }
      };

      // Method to show results
      nodeType.prototype.showResults = function () {
        if (this.resultsSection) {
          // Show the results section
          this.resultsSection.style.display = "block";

          // Remove restore button if it exists
          if (this.restoreButton && this.restoreButton.parentNode) {
            this.restoreButton.parentNode.removeChild(this.restoreButton);
            this.restoreButton = null;
          }

          // Restart timer
          this.startAutoHideTimer();
        }
      };

      // Method to display search results
      nodeType.prototype.displayResults = function (results) {
        if (!results || results.length === 0) {
          this.resultsSection.innerHTML =
            '<div style="color: #f9a825; text-align: center; padding: 20px;">📭 No prompts found</div>';
          return;
        }

        this._searchResults = results;
        this.resultsSection.innerHTML = "";

        // Ensure results are visible
        this.resultsSection.style.opacity = "1";
        this.resultsSection.style.pointerEvents = "auto";

        // Header
        const header = document.createElement("div");
        header.style.marginBottom = "10px";
        header.style.fontWeight = "bold";
        header.style.color = "#4facfe";
        header.style.textAlign = "center";
        header.style.padding = "8px";
        header.style.backgroundColor = "rgba(79, 172, 254, 0.1)";
        header.style.borderRadius = "4px";
        header.innerHTML = `📚 Found ${results.length} prompt${results.length === 1 ? "" : "s"}`;
        this.resultsSection.appendChild(header);

        // Start auto-hide timer
        this.startAutoHideTimer();

        // Results list
        results.forEach((result, index) => {
          const resultItem = document.createElement("div");
          resultItem.style.padding = "6px";
          resultItem.style.marginBottom = "4px";
          resultItem.style.backgroundColor = "#444";
          resultItem.style.borderRadius = "3px";
          resultItem.style.cursor = "pointer";
          resultItem.style.border = "1px solid transparent";

          // Hover effect
          resultItem.addEventListener("mouseenter", () => {
            resultItem.style.backgroundColor = "#555";
            resultItem.style.border = "1px solid #666";
          });
          resultItem.addEventListener("mouseleave", () => {
            resultItem.style.backgroundColor = "#444";
            resultItem.style.border = "1px solid transparent";
          });

          // Click to use prompt
          resultItem.addEventListener("click", () => {
            this.usePrompt(result, index);
          });

          // Format display
          const tags = Array.isArray(result.tags)
            ? result.tags.join(", ")
            : "No tags";
          const rating = result.rating ? `⭐${result.rating}` : "No rating";
          const category = result.category || "No category";
          const created = result.created_at
            ? new Date(result.created_at).toLocaleDateString()
            : "Unknown";

          const promptPreview =
            result.text.length > 80
              ? result.text.substring(0, 80) + "..."
              : result.text;

          resultItem.innerHTML = `
                        <div style="color: #e8e8e8; font-weight: bold; margin-bottom: 4px; line-height: 1.3;">
                            ${promptPreview}
                        </div>
                        <div style="color: #aaa; font-size: 10px; line-height: 1.4;">
                            <div style="margin-bottom: 2px;">
                                <span style="color: #4facfe;">📁 ${category}</span> • 
                                <span style="color: #ffd700;">${rating}</span> • 
                                <span style="color: #8cc8ff;">📅 ${created}</span>
                            </div>
                            <div style="color: #9c9c9c;">
                                🏷️ ${tags}
                            </div>
                        </div>
                    `;

          this.resultsSection.appendChild(resultItem);
        });
      };

      // Method to use a selected prompt
      nodeType.prototype.usePrompt = function (prompt, index) {
        // Find the text widget and set its value
        const textWidget = this.widgets?.find((w) => w.name === "text");
        if (textWidget) {
          textWidget.value = prompt.text;

          // Also set metadata if available
          const categoryWidget = this.widgets?.find(
            (w) => w.name === "category",
          );
          if (categoryWidget && prompt.category) {
            categoryWidget.value = prompt.category;
          }

          const tagsWidget = this.widgets?.find((w) => w.name === "tags");
          if (tagsWidget && prompt.tags) {
            tagsWidget.value = Array.isArray(prompt.tags)
              ? prompt.tags.join(", ")
              : prompt.tags;
          }

          // Visual feedback
          this.highlightSelectedResult(index);

          // Trigger widget change events
          this.setDirtyCanvas(true, true);

          console.log(
            `[PromptManager] Loaded prompt: ${prompt.text.substring(0, 50)}...`,
          );
        }
      };

      // Method to highlight selected result
      nodeType.prototype.highlightSelectedResult = function (index) {
        const resultItems = this.resultsSection.querySelectorAll(
          "div[style*='cursor: pointer']",
        );
        resultItems.forEach((item, i) => {
          if (i === index) {
            item.style.backgroundColor = "#2a5d31";
            item.style.border = "1px solid #4a8c57";
          } else {
            item.style.backgroundColor = "#444";
            item.style.border = "1px solid transparent";
          }
        });
      };

      // Method to call backend API endpoints
      nodeType.prototype.callNodeMethod = async function (method, params) {
        try {
          let url,
            options = { method: "GET" };

          if (method === "search_prompts") {
            // Build query string for search (simplified - only text search)
            const queryParams = new URLSearchParams();
            if (params.search_text)
              queryParams.append("text", params.search_text);
            queryParams.append("limit", "50");

            url = await pmUrl(`/search?${queryParams.toString()}`);
          } else if (method === "get_recent_prompts") {
            const limit = params.limit || 20;
            url = await pmUrl(`/recent?limit=${limit}`);
          } else {
            throw new Error(`Unknown method: ${method}`);
          }

          const response = await fetch(url, options);

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }

          const data = await response.json();

          if (!data.success) {
            throw new Error(data.error || "API call failed");
          }

          return { results: data.results || [] };
        } catch (error) {
          console.error(
            `[PromptManager] API call failed for ${method}:`,
            error,
          );
          throw error;
        }
      };

      // Method to save a prompt via API
      nodeType.prototype.savePromptToDatabase = async function (promptData) {
        try {
          const response = await pmFetch("/save", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(promptData),
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }

          const data = await response.json();

          if (!data.success) {
            throw new Error(data.error || "Save failed");
          }

          return data;
        } catch (error) {
          console.error("[PromptManager] Save prompt failed:", error);
          throw error;
        }
      };

      // Method to delete a prompt via API
      nodeType.prototype.deletePrompt = async function (promptId) {
        try {
          const response = await pmFetch(`/delete/${promptId}`, {
            method: "DELETE",
          });

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }

          const data = await response.json();

          if (!data.success) {
            throw new Error(data.error || "Delete failed");
          }

          return data;
        } catch (error) {
          console.error("[PromptManager] Delete prompt failed:", error);
          throw error;
        }
      };

      // Method to test API connection
      nodeType.prototype.testApiConnection = async function () {
        try {
          this.resultsSection.innerHTML =
            '<div style="color: #999; text-align: center;">🔧 Testing API connection...</div>';

          const response = await pmFetch("/test");

          if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
          }

          const data = await response.json();

          if (data.success) {
            this.resultsSection.innerHTML = `
                            <div style="color: #4caf50; text-align: center; padding: 10px;">
                                ✅ API Connection Successful!<br>
                                <small>${data.message}</small><br>
                                <small>Time: ${data.timestamp}</small>
                            </div>
                        `;
            this.showNotification("API connection test passed!", "success");
          } else {
            throw new Error(data.message || "Test failed");
          }
        } catch (error) {
          console.error("[PromptManager] API test failed:", error);
          this.resultsSection.innerHTML = `
                        <div style="color: #f44336; text-align: center; padding: 10px;">
                            ❌ API Connection Failed<br>
                            <small>${error.message}</small>
                        </div>
                    `;
          this.showNotification(`API test failed: ${error.message}`, "error");
        }
      };

      // Method to open web interface
      nodeType.prototype.openWebInterface = async function () {
        try {
          // Open the web interface served via our API endpoint
          const currentOrigin = window.location.origin;
          const base = await pmResolveApiBase();
          const uiBase = base.startsWith('/api/')
            ? base.replace('/api/', '/')
            : base;
          const webUrl = `${currentOrigin}${uiBase}`;

          if (this.properties.webuiDisplayMode === "newtab") {
            // Open in new tab without window options
            const newWindow = window.open(webUrl, "_blank");

            if (newWindow) {
              console.log("[PromptManager] Web interface opened in new tab");
              this.showNotification(
                "Web interface opened in new tab",
                "success",
              );
            } else {
              // Popup blocked: inform user without redirecting the main UI
              console.warn("[PromptManager] Popup blocked; not redirecting main UI");
              this.showNotification(
                "Popup blocked. Allow popups for ComfyUI to open PromptManager.",
                "error",
              );
            }
          } else {
            // Default popup mode with specific dimensions
            const newWindow = window.open(
              webUrl,
              "_blank",
              "width=1200,height=800,scrollbars=yes,resizable=yes",
            );

            if (newWindow) {
              console.log("[PromptManager] Web interface opened in popup");
              this.showNotification("Web interface opened in popup", "success");
            } else {
              // Popup blocked: inform user without redirecting the main UI
              console.warn("[PromptManager] Popup blocked; not redirecting main UI");
              this.showNotification(
                "Popup blocked. Allow popups for ComfyUI to open PromptManager.",
                "error",
              );
            }
          }
        } catch (error) {
          console.error("[PromptManager] Error opening web interface:", error);
          this.showNotification(
            `Web interface error: ${error.message}`,
            "error",
          );
        }
      };

      // Method to show notifications (unified)
      nodeType.prototype.showNotification = function (message, type = "info") {
        if (typeof window.showToast === 'function') {
          window.showToast(message, type);
        } else {
          console.log(`[${type}] ${message}`);
        }
      };
    }
  },
});
