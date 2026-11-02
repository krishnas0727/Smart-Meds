(() => {
  let installPrompt = null;
  const button = () => document.getElementById('pwa-install-button');

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event;
    const installButton = button();
    if (installButton) installButton.classList.remove('hidden');
  });

  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    const installButton = button();
    if (installButton) installButton.classList.add('hidden');
  });

  window.addEventListener('DOMContentLoaded', () => {
    const installButton = button();
    if (installButton) {
      installButton.addEventListener('click', async () => {
        if (!installPrompt) return;
        installPrompt.prompt();
        await installPrompt.userChoice;
        installPrompt = null;
        installButton.classList.add('hidden');
      });
    }
    if ('serviceWorker' in navigator && window.isSecureContext) {
      navigator.serviceWorker.register('/service-worker.js').catch((error) => {
        console.warn('PWA service worker registration failed:', error.name);
      });
    }
  });

  // Browser push notifications are intentionally not requested: no push subscription
  // backend is configured. Scheduled SMS/voice reminders remain separately consent-gated.
})();
