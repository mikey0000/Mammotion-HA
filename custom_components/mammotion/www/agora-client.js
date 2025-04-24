/**
 * Client WebRTC con SDK Agora
 */

class MammotionAgoraClient {
    constructor(cameraEntityId) {
      this.cameraEntityId = cameraEntityId;
      this.videoElement = null;
      this.cameraAttributes = null;
      this.selectedSide = 'left'; // Default: camera sinistra
      this.rtcClient = null;
      this.localTracks = {
        videoTrack: null,
        audioTrack: null
      };
      this.remoteUsers = {};
      this.options = {
        appId: null,
        channel: null,
        token: null,
        uid: null
      };
      this.isJoined = false;
    }
  
    /**
     * Inizializza il client Agora
     * @param {HTMLVideoElement} videoElement - Elemento video dove mostrare lo stream
     * @param {Object} attributes - Attributi della camera
     */
    async initialize(videoElement, attributes) {
      this.videoElement = videoElement;
      this.cameraAttributes = attributes;
      
      // Estrai le informazioni necessarie dagli attributi
      this.options.appId = this.cameraAttributes.app_id;
      this.options.channel = this.cameraAttributes.channel_name;
      this.updateCameraToken();
      
      // Crea il client Agora RTC
      this.rtcClient = AgoraRTC.createClient({ mode: 'live', codec: 'h264' });
      
      // Configurazione dei gestori di eventi
      this.rtcClient.on('user-published', this._handleUserPublished.bind(this));
      this.rtcClient.on('user-unpublished', this._handleUserUnpublished.bind(this));
      
      console.log('Client Agora inizializzato con:', this.options);
    }
  
    /**
     * Aggiorna il token in base alla camera selezionata
     */
    updateCameraToken() {
      const cameras = {
        'left': 0,
        'right': 1,
        'rear': 2
      };
      
      const cameraIndex = cameras[this.selectedSide] || 0;
      
      if (this.cameraAttributes.cameras && 
          this.cameraAttributes.cameras[cameraIndex] && 
          this.cameraAttributes.cameras[cameraIndex].token) {
        this.options.token = this.cameraAttributes.cameras[cameraIndex].token;
      } else if (this.cameraAttributes.token) {
        // Fallback al token generico
        this.options.token = this.cameraAttributes.token;
      }
      
      // Imposta uid dall'attributo o genera un uid casuale
      this.options.uid = this.cameraAttributes.uid || Math.floor(Math.random() * 1000000);
    }
  
    /**
     * Imposta il lato della camera (left, right, rear)
     * @param {string} side - Lato della camera da utilizzare
     */
    setCamera(side) {
      this.selectedSide = side;
      this.updateCameraToken();
    }
  
    /**
     * Gestisce l'evento quando un utente remoto pubblica un media track
     */
    async _handleUserPublished(user, mediaType) {
      // Iscriviti al flusso dell'utente
      await this.rtcClient.subscribe(user, mediaType);
      console.log('Iscritto al flusso', mediaType, 'dell\'utente', user.uid);
      
      // Se è un flusso video, riproducilo sull'elemento video
      if (mediaType === 'video') {
        this.remoteUsers[user.uid] = user;
        user.videoTrack.play(this.videoElement);
      }
      
      // Se è un flusso audio, riproducilo
      if (mediaType === 'audio') {
        user.audioTrack.play();
      }
    }
  
    /**
     * Gestisce l'evento quando un utente remoto annulla la pubblicazione di un media track
     */
    async _handleUserUnpublished(user) {
      console.log('Utente remoto ha interrotto la pubblicazione', user.uid);
      if (this.remoteUsers[user.uid]) {
        delete this.remoteUsers[user.uid];
      }
    }
  
    /**
     * Avvia la connessione e si unisce al canale
     */
    async connect() {
      try {
        if (!this.options.appId || !this.options.channel || !this.options.token) {
          throw new Error('Parametri mancanti per la connessione Agora');
        }
        
        // Unisciti al canale
        await this.rtcClient.join(
          this.options.appId,
          this.options.channel,
          this.options.token,
          this.options.uid
        );
        
        console.log('Connessione al canale stabilita:', this.options.channel);
        this.isJoined = true;
        
        // Per stream di sola visualizzazione, non è necessario pubblicare track locali
        // Ma se necessario, ecco come farlo:
        /*
        this.localTracks.audioTrack = await AgoraRTC.createMicrophoneAudioTrack();
        this.localTracks.videoTrack = await AgoraRTC.createCameraVideoTrack();
        await this.rtcClient.publish(Object.values(this.localTracks));
        */
        
        return true;
      } catch (error) {
        console.error('Errore nella connessione al canale Agora:', error);
        this.disconnect();
        throw error;
      }
    }
  
    /**
     * Chiude la connessione e rilascia le risorse
     */
    async disconnect() {
      // Chiudi i track locali
      Object.values(this.localTracks).forEach(track => {
        if (track) {
          track.close();
        }
      });
      this.localTracks = { videoTrack: null, audioTrack: null };
      
      // Lascia il canale e rilascia i riferimenti
      if (this.isJoined) {
        await this.rtcClient.leave();
      }
      this.remoteUsers = {};
      this.isJoined = false;
      
      console.log('Disconnesso dal canale Agora');
    }
    
    /**
     * Metodo semplificato per connettere alla camera selezionata
     */
    async reconnect() {
      if (this.isJoined) {
        await this.disconnect();
      }
      await this.connect();
    }
  }
  
  /**
   * Card Lovelace personalizzata che utilizza l'SDK Agora
   */
  class MammotionAgoraCard extends HTMLElement {
    constructor() {
      super();
      this._config = {};
      this._hass = null;
      this._agoraClient = null;
      this._connected = false;
    }
  
    setConfig(config) {
      if (!config.entity || !config.entity.startsWith('camera.')) {
        throw new Error('Si prega di specificare un\'entità camera valida');
      }
      
      this._config = config;
      this._configureUI();
    }
  
    set hass(hass) {
      this._hass = hass;
      this._updateCameraAttributes();
    }
    
    _updateCameraAttributes() {
      if (!this._hass || !this._config.entity) return;
      
      const cameraState = this._hass.states[this._config.entity];
      if (!cameraState) {
        console.error(`Entità camera ${this._config.entity} non trovata`);
        return;
      }
      
      const attributes = cameraState.attributes;
      
      // Se abbiamo un client e gli attributi sono cambiati, aggiorniamo
      if (this._agoraClient && this._agoraClient.cameraAttributes) {
        const hasChanges = ['app_id', 'channel_name', 'token', 'uid'].some(
          key => attributes[key] !== this._agoraClient.cameraAttributes[key]
        );
        
        if (hasChanges && this._connected) {
          this._agoraClient.cameraAttributes = attributes;
          this._agoraClient.updateCameraToken();
          this._agoraClient.reconnect();
        }
      }
    }
  
    _configureUI() {
      this.innerHTML = `
        <ha-card header="${this._config.title || 'Mammotion Agora Stream'}">
          <div class="card-content">
            <div class="video-container">
              <div id="video-placeholder" style="width: 100%; height: 0; padding-bottom: 56.25%; position: relative;">
                <div id="video-element" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; background-color: #000;"></div>
              </div>
            </div>
            <div class="camera-controls">
              <mwc-button id="left-camera">Sinistra</mwc-button>
              <mwc-button id="right-camera">Destra</mwc-button>
              <mwc-button id="rear-camera">Posteriore</mwc-button>
            </div>
            <div class="connection-controls">
              <mwc-button id="connect-btn">Connetti</mwc-button>
              <mwc-button id="disconnect-btn" disabled>Disconnetti</mwc-button>
            </div>
            <div id="status" style="margin-top: 8px;">Non connesso</div>
          </div>
        </ha-card>
        <style>
          .camera-controls, .connection-controls {
            display: flex;
            justify-content: space-between;
            margin: 8px 0;
          }
          mwc-button {
            margin: 2px;
          }
          #status {
            text-align: center;
            font-style: italic;
          }
        </style>
      `;
      
      // Aggiungi i listener agli eventi
      this._setupEventListeners();
    }
  
    _setupEventListeners() {
      const videoElement = this.querySelector('#video-element');
      const statusElement = this.querySelector('#status');
      const connectButton = this.querySelector('#connect-btn');
      const disconnectButton = this.querySelector('#disconnect-btn');
      const leftCameraButton = this.querySelector('#left-camera');
      const rightCameraButton = this.querySelector('#right-camera');
      const rearCameraButton = this.querySelector('#rear-camera');
      
      // Bottone connetti
      connectButton.addEventListener('click', async () => {
        if (!this._hass || !this._config.entity) return;
        
        const cameraState = this._hass.states[this._config.entity];
        if (!cameraState) {
          statusElement.textContent = `Errore: entità ${this._config.entity} non trovata`;
          return;
        }
        
        statusElement.textContent = 'Connessione in corso...';
        
        try {
          // Crea un nuovo client se non esiste
          if (!this._agoraClient) {
            this._agoraClient = new MammotionAgoraClient(this._config.entity);
            await this._agoraClient.initialize(videoElement, cameraState.attributes);
          }
          
          // Connetti al canale
          await this._agoraClient.connect();
          
          this._connected = true;
          statusElement.textContent = 'Connesso';
          connectButton.disabled = true;
          disconnectButton.disabled = false;
          leftCameraButton.disabled = false;
          rightCameraButton.disabled = false;
          rearCameraButton.disabled = false;
        } catch (err) {
          statusElement.textContent = `Errore: ${err.message}`;
        }
      });
      
      // Bottone disconnetti
      disconnectButton.addEventListener('click', async () => {
        if (!this._agoraClient) return;
        
        statusElement.textContent = 'Disconnessione in corso...';
        
        try {
          await this._agoraClient.disconnect();
          
          this._connected = false;
          statusElement.textContent = 'Disconnesso';
          connectButton.disabled = false;
          disconnectButton.disabled = true;
          leftCameraButton.disabled = true;
          rightCameraButton.disabled = true;
          rearCameraButton.disabled = true;
        } catch (err) {
          statusElement.textContent = `Errore durante la disconnessione: ${err.message}`;
        }
      });
      
      // Bottoni selezione camera
      leftCameraButton.addEventListener('click', async () => {
        if (!this._agoraClient || !this._connected) return;
        
        statusElement.textContent = 'Cambio alla camera sinistra...';
        this._agoraClient.setCamera('left');
        await this._agoraClient.reconnect();
        statusElement.textContent = 'Connesso alla camera sinistra';
      });
      
      rightCameraButton.addEventListener('click', async () => {
        if (!this._agoraClient || !this._connected) return;
        
        statusElement.textContent = 'Cambio alla camera destra...';
        this._agoraClient.setCamera('right');
        await this._agoraClient.reconnect();
        statusElement.textContent = 'Connesso alla camera destra';
      });
      
      rearCameraButton.addEventListener('click', async () => {
        if (!this._agoraClient || !this._connected) return;
        
        statusElement.textContent = 'Cambio alla camera posteriore...';
        this._agoraClient.setCamera('rear');
        await this._agoraClient.reconnect();
        statusElement.textContent = 'Connesso alla camera posteriore';
      });
    }
  
    getCardSize() {
      return 4;
    }
  }
  
  // Registra la card personalizzata
  customElements.define('mammotion-agora-card', MammotionAgoraCard);