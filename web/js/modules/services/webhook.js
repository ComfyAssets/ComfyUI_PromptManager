/**
 * Webhook Integration Service
 * Handles webhook notifications for Slack, Discord, Teams
 */
const WebhookService = (function() {
    'use strict';

    function createStub() {
       
        const target = {};
        const stub = new Proxy(target, {
            get: (obj, prop) => {
                if (prop in obj) {
                    return obj[prop];
                }
                return (...args) => Promise.resolve({ success: false });
            },
            set: (obj, prop, value) => {
                obj[prop] = value;
                return true;
            },
        });
        return stub;
    }

    if (typeof window === 'undefined') {
        return createStub();
    }
    const pmActive = (window.location?.pathname || '').includes('/prompt_manager');
    if (!pmActive) {
        console.info('[PromptManager] webhook service skipped outside PromptManager UI context');
        return createStub();
    }

    // Webhook payload builders
    // Webhook payload builders
    const payloadBuilders = {
        slack: function(message, options = {}) {
            return {
                text: message,
                attachments: [{
                    color: options.color || '#6078EA',
                    title: options.title || 'PromptManager Notification',
                    text: options.text || message,
                    footer: 'PromptManager',
                    ts: Math.floor(Date.now() / 1000),
                    fields: options.fields || []
                }]
            };
        },

        discord: function(message, options = {}) {
            return {
                content: message,
                embeds: [{
                    title: options.title || 'PromptManager Notification',
                    description: options.text || message,
                    color: parseInt((options.color || '#6078EA').replace('#', ''), 16),
                    timestamp: new Date().toISOString(),
                    footer: {
                        text: 'PromptManager'
                    },
                    fields: options.fields || []
                }]
            };
        },

        teams: function(message, options = {}) {
            return {
                '@type': 'MessageCard',
                '@context': 'http://schema.org/extensions',
                themeColor: (options.color || '#6078EA').replace('#', ''),
                summary: message,
                sections: [{
                    activityTitle: options.title || 'PromptManager',
                    activitySubtitle: 'Notification',
                    text: options.text || message,
                    facts: options.fields ? options.fields.map(f => ({
                        name: f.name,
                        value: f.value
                    })) : []
                }]
            };
        }
    };

    // Notification type colors
    const typeColors = {
        success: '#28a745',
        error: '#dc3545',
        warning: '#ffc107',
        info: '#17a2b8'
    };

    // Public API
    return {
        /**
         * Send webhook notification
         * @param {string} url - Webhook URL
         * @param {string} message - Message to send
         * @param {object} options - Additional options
         */
        send: async function(url, message, options = {}) {
            if (!url) {
                throw new Error('Webhook URL is required');
            }

            const service = options.service || this.detectService(url);
            const type = options.type || 'info';
            const color = typeColors[type] || typeColors.info;

            // Build payload based on service
            const payloadBuilder = payloadBuilders[service] || payloadBuilders.slack;
            const payload = payloadBuilder(message, {
                ...options,
                color: color
            });

            try {
                const response = await fetch('/api/webhook/send', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        url: url,
                        payload: payload,
                        service: service
                    })
                });

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }

                return await response.json();
            } catch (error) {
                console.error('Webhook send failed:', error);
                throw error;
            }
        },

        /**
         * Test webhook configuration
         */
        test: async function(url, service) {
            const testMessage = '🧪 Test notification from PromptManager';

            return this.send(url, testMessage, {
                service: service,
                title: 'Test Notification',
                text: 'This is a test webhook notification to verify your configuration.',
                type: 'info',
                fields: [
                    { name: 'Status', value: 'Test' },
                    { name: 'Timestamp', value: new Date().toLocaleString() }
                ]
            });
        },

        /**
         * Detect webhook service from URL
         */
        detectService: function(url) {
            if (url.includes('slack.com')) return 'slack';
            if (url.includes('discord.com') || url.includes('discordapp.com')) return 'discord';
            if (url.includes('webhook.office.com')) return 'teams';
            return 'slack'; // Default
        },

        /**
         * Send notification based on event type
         */
        notify: async function(event, data) {
            const settings = this.getSettings();
            if (!settings.enabled || !settings.url) return;

            // Check if this event type is enabled
            const eventEnabled = settings.events && settings.events[event];
            if (!eventEnabled) return;

            // Build message based on event
            const message = this.buildEventMessage(event, data);

            try {
                await this.send(settings.url, message.text, {
                    service: settings.service,
                    title: message.title,
                    type: message.type,
                    fields: message.fields
                });
            } catch (error) {
                console.error(`Failed to send webhook for ${event}:`, error);
            }
        },

        /**
         * Build message for specific events
         */
        buildEventMessage: function(event, data) {
            const messages = {
                'prompt.saved': {
                    title: 'Prompt Saved',
                    text: `New prompt saved: "${data.title || 'Untitled'}"`,
                    type: 'success',
                    fields: [
                        { name: 'Category', value: data.category || 'Uncategorized' },
                        { name: 'ID', value: data.id }
                    ]
                },
                'prompt.deleted': {
                    title: 'Prompt Deleted',
                    text: `Prompt deleted: "${data.title || 'Untitled'}"`,
                    type: 'info'
                },
                'image.generated': {
                    title: 'Image Generated',
                    text: `New image generated with prompt: "${data.prompt || 'Unknown'}"`,
                    type: 'success',
                    fields: [
                        { name: 'Model', value: data.model || 'Unknown' },
                        { name: 'Size', value: data.size || 'Unknown' }
                    ]
                },
                'error.occurred': {
                    title: 'Error Occurred',
                    text: data.message || 'An error occurred',
                    type: 'error',
                    fields: [
                        { name: 'Code', value: data.code || 'Unknown' },
                        { name: 'Source', value: data.source || 'Unknown' }
                    ]
                }
            };

            return messages[event] || {
                title: event,
                text: JSON.stringify(data),
                type: 'info'
            };
        },

        /**
         * Get webhook settings from storage
         */
        getSettings: function() {
            const stored = localStorage.getItem('webhook_settings');
            return stored ? JSON.parse(stored) : {
                enabled: false,
                url: '',
                service: 'slack',
                events: {
                    'prompt.saved': true,
                    'prompt.deleted': false,
                    'image.generated': true,
                    'error.occurred': true
                }
            };
        },

        /**
         * Save webhook settings
         */
        saveSettings: function(settings) {
            localStorage.setItem('webhook_settings', JSON.stringify(settings));
        }
    };
})();

// Export for use
if (typeof module !== 'undefined' && module.exports) {
    module.exports = WebhookService;
}

// Attach to window for global access
if (typeof window !== 'undefined') {
    window.WebhookService = WebhookService;
}
