/* Passo 78.2 — permissão por gesto do usuário e inscrição vinculada à conta. */
(function () {
  const button = document.getElementById('cen-push-button');
  const status = document.getElementById('cen-push-status');
  if (!button || !status) return;
  let key = null;
  function say(message) { status.textContent = message; }
  function decodeBase64Url(value) {
    const padded = (value + '='.repeat((4 - value.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
    return Uint8Array.from(atob(padded), c => c.charCodeAt(0));
  }
  async function post(path, body) {
    const response = await fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="cen-csrf"]').content},
      body: JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Não foi possível salvar a configuração.');
    return data;
  }
  async function init() {
    if (!window.isSecureContext || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      say('Este dispositivo ou navegador não oferece suporte a notificações Push.'); return;
    }
    try {
      const res = await fetch('/api/push/config', {credentials:'same-origin'});
      if (!res.ok) throw new Error('Falha ao verificar configuração.');
      const config = await res.json();
      if (!config.enabled) { say('Aguardando configuração do serviço de notificações.'); return; }
      key = config.publicKey;
      const registration = await navigator.serviceWorker.ready;
      const sub = await registration.pushManager.getSubscription();
      if (sub && Notification.permission === 'granted') {
        await post('/api/push/subscribe', sub.toJSON());
        say('Notificações ativadas neste aparelho.');
        button.textContent = 'Desativar neste aparelho'; button.hidden = false;
      } else {
        say(Notification.permission === 'denied' ? 'Permissão bloqueada. Altere nas configurações do aparelho.' : 'Receba avisos da escola diretamente neste aparelho.');
        if (Notification.permission !== 'denied') { button.textContent = 'Ativar notificações'; button.hidden = false; }
      }
    } catch (err) { say('Não foi possível verificar notificações. Tente atualizar a página.'); console.warn(err); }
  }
  button.addEventListener('click', async function () {
    button.disabled = true;
    try {
      const registration = await navigator.serviceWorker.ready;
      let sub = await registration.pushManager.getSubscription();
      if (sub && Notification.permission === 'granted') {
        await post('/api/push/unsubscribe', {endpoint: sub.endpoint});
        await sub.unsubscribe();
        say('Notificações desativadas neste aparelho.');
        button.textContent = 'Ativar notificações';
      } else {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') { say('Permissão não concedida. Você pode alterá-la nas configurações do aparelho.'); button.hidden = true; return; }
        sub = await registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:decodeBase64Url(key)});
        try { await post('/api/push/subscribe', sub.toJSON()); }
        catch (err) { await sub.unsubscribe(); throw err; }
        say('Notificações ativadas neste aparelho!');
        button.textContent = 'Desativar neste aparelho';
      }
    } catch (err) { say('Não foi possível concluir: ' + (err.message || 'erro desconhecido')); }
    finally { button.disabled = false; }
  });
  init();
})();
