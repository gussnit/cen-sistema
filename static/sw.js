/* Portal CEN — Passo 78.1. Sem cache de páginas autenticadas ou dados pessoais. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (event) { event.waitUntil(self.clients.claim()); });
// Não interceptar requisições nesta fase: mantém login, CSRF e conteúdo sempre no servidor.
