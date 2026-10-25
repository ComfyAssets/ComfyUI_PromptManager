import { createScope, createButton, mountTheme } from './shared/toolkit/core.js';

function loadStyles() {
    const href = new URL('./classic-node.css', import.meta.url).href;
    if ([...document.querySelectorAll('link[rel="stylesheet"]')].some(link => link.href === href)) return;
    const link = document.createElement('link');
    Object.assign(link, { rel: 'stylesheet', href });
    document.head.append(link);
}

function message(node, text, error = false) {
    node._classicResultsScope?.dispose();
    const element = document.createElement('p');
    element.className = error ? 'pm-classic-message pm-classic-error' : 'pm-classic-message';
    element.textContent = text;
    element.setAttribute('role', error ? 'alert' : 'status');
    node.resultsSection.replaceChildren(element);
}

export function installClassicControls(nodeType, app, parseSearchQuery, api) {
    const prototype = nodeType.prototype;
    const onCreated = prototype.onNodeCreated;
    const onRemoved = prototype.onRemoved;
    prototype.onNodeCreated = function (...args) {
        this._classicScope = createScope();
        this._classicNotifications = new Set();
        this._classicScope.cleanup(() => {
            clearTimeout(this._textSyncTimer);
            clearTimeout(this.resultHideTimer);
            this._classicResultsScope?.dispose();
            for (const item of this._classicNotifications) { clearTimeout(item.timer); item.element.remove(); }
            this._classicNotifications.clear();
        });
        onCreated.apply(this, args);
    };
    prototype.onRemoved = function (...args) {
        this._classicScope?.dispose();
        return onRemoved?.apply(this, args);
    };
    prototype.createSearchUI = function (container) {
        loadStyles();
        container.classList.add('ca-tools', 'pm-classic');
        mountTheme(container, app.ui?.settings, this._classicScope);
        this._classicContainer = container;
        const wrapper = document.createElement('div');
        wrapper.className = 'pm-classic-workspace';
        const linkNote = this.createTextLinkNote();
        linkNote.classList.add('pm-classic-link');
        wrapper.append(linkNote);
        const buttons = document.createElement('div');
        buttons.className = 'pm-classic-toolbar';
        for (const [label, action, primary] of [
            ['🔍 Search', () => this.performSearch(), true],
            ['📋 Recent', () => this.loadRecentPrompts()],
            ['🌐 Web UI', () => this.openWebInterface()],
        ]) {
            const button = createButton(label, primary ? 'ca-primary' : '');
            this._classicScope.listen(button, 'click', action);
            buttons.append(button);
        }
        wrapper.append(buttons);
        if (this.properties.showTestButton) {
            const test = createButton('🔧 Test API');
            this._classicScope.listen(test, 'click', () => this.testApiConnection());
            wrapper.append(test);
        }
        this.resultsSection = document.createElement('div');
        this.resultsSection.className = 'pm-classic-results';
        wrapper.append(this.resultsSection);
        container.replaceChildren(wrapper);
        message(this, '💡 Click Search or Recent to find prompts');
    };
    prototype.performSearch = async function () {
        const { text, tags } = parseSearchQuery(this.widgets?.find(widget => widget.name === 'search_text')?.value || '');
        if (!text && !tags.length) { message(this, '⚠️ Enter search text or tag:name'); return; }
        await this._classicQuery('search_prompts', { search_text: text, tags }, '🔍 Searching database...');
    };
    prototype.loadRecentPrompts = async function () {
        await this._classicQuery('get_recent_prompts', { limit: 20 }, '📋 Loading recent prompts...');
    };
    prototype._classicQuery = async function (method, params, pending) {
        message(this, pending);
        try {
            const response = await this.callNodeMethod(method, params);
            if (this._classicScope.signal.aborted) return;
            this.displayResults(response.results || []);
            this.showNotification(response.results?.length ? `Found ${response.results.length} prompts` : 'No recent prompts found');
        } catch (error) {
            if (this._classicScope.signal.aborted) return;
            message(this, `❌ ${error.message}`, true);
            this.showNotification(error.message, 'error');
        }
    };
    prototype.displayResults = function (results) {
        if (this._classicScope.signal.aborted) return;
        this._searchResults = results || [];
        this._selectedPromptIndex = -1;
        if (!results?.length) { message(this, '📭 No prompts found'); return; }
        this._classicResultsScope?.dispose();
        this._classicResultsScope = createScope();
        const header = document.createElement('p');
        header.className = 'pm-classic-count';
        header.textContent = `📚 Found ${results.length} prompt${results.length === 1 ? '' : 's'}`;
        this.resultsSection.replaceChildren(header);
        results.forEach((result, index) => {
            const button = createButton('', 'pm-classic-result');
            button.setAttribute('aria-pressed', 'false');
            const preview = document.createElement('span');
            preview.className = 'pm-classic-preview';
            preview.textContent = result.text.length > 80 ? result.text.substring(0, 80) + '...' : result.text;
            const metadata = document.createElement('span');
            metadata.className = 'pm-classic-metadata';
            metadata.textContent = `${result.category || 'No category'} · ${result.rating ? '⭐' + result.rating : 'No rating'} · ${result.created_at ? new Date(result.created_at).toLocaleDateString() : 'Unknown'} · ${Array.isArray(result.tags) ? result.tags.join(', ') : 'No tags'}`;
            button.append(preview, metadata);
            this._classicResultsScope.listen(button, 'click', () => this.usePrompt(result, index));
            this.resultsSection.append(button);
        });
        this.startAutoHideTimer();
    };
    prototype.highlightSelectedResult = function (index) {
        this._selectedPromptIndex = index;
        this.resultsSection.querySelectorAll('.pm-classic-result').forEach((element, i) => element.setAttribute('aria-pressed', String(i === index)));
    };
    prototype.testApiConnection = async function () {
        message(this, '🔧 Testing API connection...');
        try {
            const response = await fetch(api.fileURL('/prompt_manager/test'), { signal: this._classicScope.signal });
            if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            const data = await response.json();
            if (!data.success) throw new Error(data.message || 'Test failed');
            if (this._classicScope.signal.aborted) return;
            message(this, `✅ API Connection Successful! ${data.message || ''} ${data.timestamp || ''}`);
            this.showNotification('API connection test passed!', 'success');
        } catch (error) {
            if (this._classicScope.signal.aborted) return;
            message(this, `❌ API Connection Failed: ${error.message}`, true);
            this.showNotification(`API test failed: ${error.message}`, 'error');
        }
    };
    prototype.hideResults = function () {
        if (this._classicScope.signal.aborted || this.restoreButton) return;
        this.resultsSection.style.display = 'none';
        this.restoreButton = createButton('🔄 Results auto-hidden · Click to restore', 'pm-classic-restore');
        this._classicScope.listen(this.restoreButton, 'click', () => this.showResults());
        this.resultsSection.after(this.restoreButton);
    };
    prototype.showNotification = function (text, type = 'info') {
        if (this._classicScope.signal.aborted) return;
        const element = document.createElement('div');
        element.className = 'ca-tools pm-classic-notification';
        element.dataset.caTheme = this._classicContainer.dataset.caTheme;
        element.dataset.kind = type;
        element.setAttribute('role', type === 'error' ? 'alert' : 'status');
        element.textContent = text;
        document.body.append(element);
        const item = { element, timer: setTimeout(() => { element.remove(); this._classicNotifications.delete(item); }, 3000) };
        this._classicNotifications.add(item);
    };
}
