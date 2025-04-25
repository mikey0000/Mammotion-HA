class CameraAgoraCard extends HTMLElement {
    // Definisci le proprietÃ  interne
    _hass;
    _config;
    _client = null;
  
    // Metodo obbligatorio per le card di Home Assistant
    setConfig(config) {
      if (!config.entity) {
        throw new Error("Ãˆ necessario specificare un'entitÃ ");
      }
      this._config = config;
      
      // Prepara il contenitore shadow DOM
      this.attachShadow({ mode: 'open' });
      this.shadowRoot.innerHTML = `
        <div id="card-container" style="padding: 8px;">
          <div id="agora-video" style="width: 100%; height: 300px; background: black; position: relative;">
            <div id="loading" style="color: white; text-align: center; padding-top: 140px;">Caricamento in corso...</div>
          </div>
        </div>
      `;
    }
  
    // Metodo chiamato quando Home Assistant aggiorna lo stato
    set hass(hass) {
      this._hass = hass;
      
      // Se la card non Ã¨ ancora stata inizializzata, fallo ora
      if (!this._initialized && this._config) {
        this._initialized = true;
        this._setupAgoraStream();
      }
    }
  
    // Configurazione standard per l'editor
    static getStubConfig() {
      return { entity: "" };
    }
  
    // Inizializza lo streaming Agora
    async _setupAgoraStream() {
      if (!this._hass || !this._config) return;
      
      const entityId = this._config.entity;
      if (!this._hass.states[entityId]) {
        this._showError("EntitÃ  non trovata");
        return;
      }
  
      try {
        // Carica l'SDK di Agora
        await this._loadAgoraSDK();
        
        if (!window.AgoraRTC) {
          this._showError("SDK di Agora non caricato correttamente");
          return;
        }
        
        // Aggiorna i token e avvia lo streaming
        await this._hass.callService('mammotion', 'refresh_stream', { entity_id: entityId });
        await this._hass.callService('mammotion', 'start_video', { entity_id: entityId });
        
        const attr = this._hass.states[entityId].attributes;
        const videoContainer = this.shadowRoot.getElementById('agora-video');
        
        // Crea il client Agora
        const client = window.AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
        
        client.on('user-published', async (user, mediaType) => {
          await client.subscribe(user, mediaType);
          if (mediaType === 'video') {
            // Rimuovi il messaggio di caricamento
            const loading = this.shadowRoot.getElementById('loading');
            if (loading) loading.remove();
            
            user.videoTrack.play(videoContainer);
          }
        });
        
        // Registra gli handler per gli errori
        client.on('connection-state-change', (state) => {
          console.log(`Agora connection state: ${state}`);
          if (state === 'DISCONNECTED') {
            this._showError("Connessione interrotta");
          }
        });
        
        await client.join(attr.app_id, attr.channel_name, attr.token, parseInt(attr.uid));
        this._client = client;
      } catch (error) {
        console.error("Errore inizializzazione Agora:", error);
        this._showError(`Errore: ${error.message}`);
      }
    }
  
    // Pulizia quando la card viene rimossa
    disconnectedCallback() {
      if (this._client) {
        this._client.leave();
        this._client = null;
      }
      
      if (this._hass && this._config && this._config.entity) {
        this._hass.callService('mammotion', 'stop_video', { entity_id: this._config.entity });
      }
    }
  
    // Mostra un messaggio di errore
    _showError(message) {
      const container = this.shadowRoot.getElementById('agora-video');
      if (container) {
        container.innerHTML = `<div style="color: red; padding: 1em; text-align: center;">${message}</div>`;
      }
    }
  
    // Carica l'SDK di Agora
    async _loadAgoraSDK() {
      if (!window.AgoraRTC) {
        return new Promise((resolve, reject) => {
          const script = document.createElement('script');
          script.src = 'https://download.agora.io/sdk/release/AgoraRTC_N.js';
          script.onload = resolve;
          script.onerror = () => reject(new Error("Impossibile caricare l'SDK di Agora"));
          document.head.appendChild(script);
        });
      }
    }
  }
  
  // Registra l'elemento
  customElements.define('camera-agora-card', CameraAgoraCard);