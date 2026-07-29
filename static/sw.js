const CACHE_NAME = "amecare-v2-cache-v1";

const STATIC_ASSETS = [
    "/",
    "/heart",
    "/train",
    "/reports",
    "/static/style.css",
    "/static/app.js",
    "/static/manifest.json",
    "/static/icon-192.png",
    "/static/icon-512.png"
];

/**
 * インストール時
 * 基本ファイルをキャッシュする
 */
self.addEventListener("install", (event) => {
    event.waitUntil(
        caches
            .open(CACHE_NAME)
            .then((cache) => {
                return cache.addAll(STATIC_ASSETS);
            })
            .then(() => {
                return self.skipWaiting();
            })
    );
});

/**
 * 新しいService Workerをすぐ有効化
 */
self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches
            .keys()
            .then((cacheNames) => {
                return Promise.all(
                    cacheNames.map((cacheName) => {
                        if (cacheName !== CACHE_NAME) {
                            return caches.delete(cacheName);
                        }

                        return null;
                    })
                );
            })
            .then(() => {
                return self.clients.claim();
            })
    );
});

/**
 * GET通信のみキャッシュ対象
 */
self.addEventListener("fetch", (event) => {
    const request = event.request;

    if (request.method !== "GET") {
        return;
    }

    const url = new URL(request.url);

    if (url.origin !== self.location.origin) {
        return;
    }

    /**
     * HTMLページ
     * ネットワーク優先
     */
    if (request.mode === "navigate") {
        event.respondWith(
            fetch(request)
                .then((response) => {
                    const responseCopy = response.clone();

                    caches
                        .open(CACHE_NAME)
                        .then((cache) => {
                            cache.put(request, responseCopy);
                        });

                    return response;
                })
                .catch(() => {
                    return caches.match(request).then((cachedResponse) => {
                        return cachedResponse || caches.match("/");
                    });
                })
        );

        return;
    }

    /**
     * CSS・JavaScript・画像
     * キャッシュ優先
     */
    event.respondWith(
        caches.match(request).then((cachedResponse) => {
            if (cachedResponse) {
                return cachedResponse;
            }

            return fetch(request).then((response) => {
                if (
                    !response ||
                    response.status !== 200 ||
                    response.type !== "basic"
                ) {
                    return response;
                }

                const responseCopy = response.clone();

                caches
                    .open(CACHE_NAME)
                    .then((cache) => {
                        cache.put(request, responseCopy);
                    });

                return response;
            });
        })
    );
});

/**
 * アプリ側から更新命令を受け取る
 */
self.addEventListener("message", (event) => {
    if (event.data === "SKIP_WAITING") {
        self.skipWaiting();
    }
});