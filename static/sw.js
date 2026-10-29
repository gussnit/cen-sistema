/* Portal CEN — Push. Nunca armazenar páginas ou dados pessoais em cache. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });
self.addEventListener('push', function (event) {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  const title = 'Portal CEN';
  const body = typeof data.body === 'string' ? data.body.slice(0, 140) : 'Você tem uma novidade no portal.';
  event.waitUntil(self.registration.showNotification(title, {
    body: body,
    icon: '/static/icons/cen-192.png',
    badge: '/static/icons/cen-192.png',
    tag: 'cen-novidade',
    data: {url: '/'}
  }));
});
self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
