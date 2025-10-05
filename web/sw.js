/**
 * Service Worker for PromptManager PWA
 * Handles offline functionality, caching, and background sync
 */

const CACHE_NAME = 'promptmanager-v1.0.1';
const API_CACHE = 'promptmanager-api-v1.0.1';
const IMAGE_CACHE = 'promptmanager-images-v1.0.1';

// Files to cache on install
const STATIC_ASSETS = [
    '/prompt_manager/',
    '/prompt_manager/dashboard',
    '/prompt_manager/gallery',
    '/prompt_manager/collections',
    '/prompt_manager/css/style.css',
    '/prompt_manager/css/theme.css',
    '/prompt_manager/css/layout.css',
    '/prompt_manager/css/components.css',
    '/prompt_manager/css/responsive.css',
    '/prompt_manager/js/app.js',
    '/prompt_manager/js/modules/core/api.js',
    '/prompt_manager/js/modules/core/state.js',
    '/prompt_manager/js/modules/core/events.js',
    '/prompt_manager/js/modules/core/sse.js',
    '/prompt_manager/manifest.json'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
    console.log('[ServiceWorker] Installing...');

    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('[ServiceWorker] Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            })
            .then(() => self.skipWaiting())
    );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
    console.log('[ServiceWorker] Activating...');

    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((cacheName) => {
                        return cacheName.startsWith('promptmanager-') &&
                               cacheName !== CACHE_NAME &&
                               cacheName !== API_CACHE &&
                               cacheName !== IMAGE_CACHE;
                    })
                    .map((cacheName) => {
                        console.log('[ServiceWorker] Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    })
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch event - serve from cache when possible
self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);

    // Skip non-HTTP(S) requests
    if (!url.protocol.startsWith('http')) {
        return;
    }

    // Handle API requests
    if (url.pathname.startsWith('/api/')) {
        event.respondWith(handleApiRequest(request));
        return;
    }

    // Handle image requests
    if (request.destination === 'image' ||
        /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(url.pathname)) {
        event.respondWith(handleImageRequest(request));
        return;
    }

    // Handle static assets
    event.respondWith(handleStaticRequest(request));
});

/**
 * Handle API requests with network-first strategy
 */
async function handleApiRequest(request) {
    const cache = await caches.open(API_CACHE);

    try {
        // Try network first
        const networkResponse = await fetch(request);

        // Cache successful GET requests
        if (request.method === 'GET' && networkResponse.ok) {
            cache.put(request, networkResponse.clone());
        }

        return networkResponse;
    } catch (error) {
        console.log('[ServiceWorker] Network request failed, serving from cache');

        // Fall back to cache
        const cachedResponse = await cache.match(request);
        if (cachedResponse) {
            return cachedResponse;
        }

        // Return error response
        return new Response(JSON.stringify({
            error: 'Network request failed and no cached data available'
        }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' }
        });
    }
}

/**
 * Handle image requests with cache-first strategy
 */
async function handleImageRequest(request) {
    const cache = await caches.open(IMAGE_CACHE);

    // Check cache first
    const cachedResponse = await cache.match(request);
    if (cachedResponse) {
        return cachedResponse;
    }

    try {
        // Fetch from network
        const networkResponse = await fetch(request);

        // Cache successful responses
        if (networkResponse.ok) {
            cache.put(request, networkResponse.clone());
        }

        return networkResponse;
    } catch (error) {
        console.log('[ServiceWorker] Failed to fetch image:', request.url);

        // Return placeholder image if available
        return caches.match('/prompt_manager/images/placeholder.png')
            || new Response('', { status: 404 });
    }
}

/**
 * Handle static asset requests with cache-first strategy
 */
async function handleStaticRequest(request) {
    const cache = await caches.open(CACHE_NAME);

    // Check cache first
    const cachedResponse = await cache.match(request);
    if (cachedResponse) {
        // Update cache in background
        event.waitUntil(updateCache(request, cache));
        return cachedResponse;
    }

    try {
        // Fetch from network
        const networkResponse = await fetch(request);

        // Cache successful responses
        if (networkResponse.ok) {
            cache.put(request, networkResponse.clone());
        }

        return networkResponse;
    } catch (error) {
        console.log('[ServiceWorker] Network request failed:', request.url);

        // Return offline page if available
        if (request.destination === 'document') {
            return caches.match('/prompt_manager/offline.html')
                || new Response('Offline', { status: 503 });
        }

        return new Response('', { status: 404 });
    }
}

/**
 * Update cache in background
 */
async function updateCache(request, cache) {
    try {
        const networkResponse = await fetch(request);
        if (networkResponse.ok) {
            cache.put(request, networkResponse);
        }
    } catch (error) {
        // Silent fail - we already returned cached version
    }
}

// Handle messages from clients
self.addEventListener('message', (event) => {
    console.log('[ServiceWorker] Message received:', event.data);

    if (event.data.action === 'skipWaiting') {
        self.skipWaiting();
    }

    if (event.data.action === 'clearCache') {
        event.waitUntil(
            caches.keys().then((cacheNames) => {
                return Promise.all(
                    cacheNames.map((cacheName) => caches.delete(cacheName))
                );
            }).then(() => {
                event.ports[0].postMessage({ success: true });
            })
        );
    }
});

// Background sync for offline actions
self.addEventListener('sync', (event) => {
    console.log('[ServiceWorker] Background sync:', event.tag);

    if (event.tag === 'sync-prompts') {
        event.waitUntil(syncPrompts());
    }
});

/**
 * Sync prompts when back online
 */
async function syncPrompts() {
    // Get pending prompts from IndexedDB
    const pendingPrompts = await getPendingPrompts();

    for (const prompt of pendingPrompts) {
        try {
            const response = await fetch('/api/v1/prompts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(prompt)
            });

            if (response.ok) {
                await removePendingPrompt(prompt.id);
            }
        } catch (error) {
            console.error('[ServiceWorker] Failed to sync prompt:', error);
        }
    }
}

// Placeholder functions for IndexedDB operations
async function getPendingPrompts() {
    // Implementation would read from IndexedDB
    return [];
}

async function removePendingPrompt(id) {
    // Implementation would remove from IndexedDB
}