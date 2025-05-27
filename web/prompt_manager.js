// PromptManager/web/prompt_manager.js

import { app } from "../../scripts/app.js";

app.registerExtension({
  name: "PromptManager.UI",

  async beforeRegisterNodeDef(nodeType, nodeData, app) {
    if (nodeData.name === "PromptManager") {
      console.log("[PromptManager] Patching node type for custom UI");

      // Store original methods
      const onNodeCreated = nodeType.prototype.onNodeCreated;

      // Override onNodeCreated to add custom UI
      nodeType.prototype.onNodeCreated = function () {
        if (onNodeCreated) {
          onNodeCreated.apply(this, arguments);
        }

        // Initialize properties for search state
        this.properties = this.properties || {};
        this.properties.searchResults = [];
        this.properties.selectedPromptIndex = -1;
        this.properties.resultTimeout = 3; // Default 3 seconds
        this.properties.showTestButton = false; // Default hide test button
        this.properties.webuiDisplayMode = "popup"; // Default popup mode
        this.resultHideTimer = null;

        // Load settings from API
        this.loadSettings();

        // Create DOM widget container
        const container = document.createElement("div");
        container.style.padding = "8px";
        container.style.backgroundColor = "#2a2a2a";
        container.style.borderRadius = "4px";
        container.style.marginTop = "5px";

        this.createSearchUI(container);

        // Add as DOM widget
        this.searchWidget = this.addDOMWidget(
          "prompt_manager_search_ui",
          "div",
          container,
        );

        // Set initial node size, but preserve user resizes
        if (!this._userHasResized) {
          this.size = [400, 400]; // width=400, height=300 pixels
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

        // Hook into serialization to preserve resize flag
        const originalSerialize = this.serialize;
        this.serialize = function () {
          const data = originalSerialize ? originalSerialize.call(this) : {};
          data._userHasResized = this._userHasResized;
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

      // Method to create search UI elements
      nodeType.prototype.createSearchUI = function (container) {
        container.innerHTML = "";

        // Search controls section
        const searchSection = document.createElement("div");
        searchSection.style.marginBottom = "10px";

        // Search buttons row
        const buttonRow = document.createElement("div");
        buttonRow.style.display = "flex";
        buttonRow.style.gap = "8px";
        buttonRow.style.marginBottom = "8px";

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
        resultsSection.style.maxHeight = "200px";
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

        container.appendChild(searchSection);
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
          const response = await fetch("/prompt_manager/settings");
          if (response.ok) {
            const data = await response.json();
            if (data.success && data.settings) {
              this.properties.resultTimeout = data.settings.result_timeout || 3;
              this.properties.showTestButton =
                data.settings.show_test_button || false;
              this.properties.webuiDisplayMode =
                data.settings.webui_display_mode || "popup";
            }
          }
        } catch (error) {
          console.log(
            "[PromptManager] Could not load settings, using defaults",
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

        this.properties.searchResults = results;
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

            url = `/prompt_manager/search?${queryParams.toString()}`;
          } else if (method === "get_recent_prompts") {
            const limit = params.limit || 20;
            url = `/prompt_manager/recent?limit=${limit}`;
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
          const response = await fetch("/prompt_manager/save", {
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
          const response = await fetch(`/prompt_manager/delete/${promptId}`, {
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

          const response = await fetch("/prompt_manager/test");

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
      nodeType.prototype.openWebInterface = function () {
        try {
          // Open the web interface served via our API endpoint
          const currentOrigin = window.location.origin;
          const webUrl = `${currentOrigin}/prompt_manager/web`;

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
              // Fallback if popup was blocked
              console.log(
                "[PromptManager] Popup blocked, trying alternative method",
              );
              window.location.href = webUrl;
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
              // Fallback if popup was blocked
              console.log(
                "[PromptManager] Popup blocked, trying alternative method",
              );
              window.location.href = webUrl;
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

      // Method to show notifications
      nodeType.prototype.showNotification = function (message, type = "info") {
        // Create a temporary notification element
        const notification = document.createElement("div");
        notification.style.position = "fixed";
        notification.style.top = "20px";
        notification.style.right = "20px";
        notification.style.padding = "10px 15px";
        notification.style.borderRadius = "4px";
        notification.style.color = "white";
        notification.style.fontSize = "12px";
        notification.style.zIndex = "10000";
        notification.style.maxWidth = "300px";
        notification.textContent = message;

        // Set color based on type
        switch (type) {
          case "success":
            notification.style.backgroundColor = "#4caf50";
            break;
          case "error":
            notification.style.backgroundColor = "#f44336";
            break;
          case "warning":
            notification.style.backgroundColor = "#ff9800";
            break;
          default:
            notification.style.backgroundColor = "#2196f3";
        }

        document.body.appendChild(notification);

        // Remove after 3 seconds
        setTimeout(() => {
          document.body.removeChild(notification);
        }, 3000);
      };
    }
  },
});
