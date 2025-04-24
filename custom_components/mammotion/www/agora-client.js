// === frontend/www/camera-agora-card.js ===

class CameraAgoraCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this._client = null;
    }
  
    async connectedCallback() {
      const entityId = this.getAttribute('entity');
      const hass = document.querySelector('home-assistant')?.hass;
      if (!hass || !entityId || !hass.states[entityId]) {
        this.renderError('Entità non trovata');
        return;
      }
  
      try {
        // Aggiorna i token e avvia lo streaming
        await hass.callService('mammotion', 'refresh_stream', { entity_id: entityId });
        await hass.callService('mammotion', 'start_video', { entity_id: entityId });
      } catch (e) {
        this.renderError("Errore nell'avvio del video: " + e);
        return;
      }
  
      const attr = hass.states[entityId].attributes;
      await this.loadAgoraSDK();
  
      const container = document.createElement('div');
      container.id = 'agora-video';
      container.style = 'width: 100%; height: 100%; background: black';
      this.shadowRoot.appendChild(container);
  
      const client = AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
  
      client.on('user-published', async (user, mediaType) => {
        await client.subscribe(user, mediaType);
        if (mediaType === 'video') {
          user.videoTrack.play(container);
        }
      });
  
      await client.join(attr.app_id, attr.channel_name, attr.token, parseInt(attr.uid));
      this._client = client;
    }
  
    disconnectedCallback() {
      const entityId = this.getAttribute('entity');
      const hass = document.querySelector('home-assistant')?.hass;
      if (hass && entityId) {
        hass.callService('mammotion', 'stop_video', { entity_id: entityId });
      }
      if (this._client) {
        this._client.leave();
        this._client = null;
      }
    }
  
    renderError(msg) {
      this.shadowRoot.innerHTML = `<div style="color: red; padding: 1em">${msg}</div>`;
    }
  
    async loadAgoraSDK() {
      if (!window.AgoraRTC) {
        await new Promise((resolve) => {
          const script = document.createElement('script');
          script.src = './AgoraRTC_N.js';
          script.onload = resolve;
          document.head.appendChild(script);
        });
      }
    }
  }
  
  customElements.define('camera-agora-card', CameraAgoraCard);