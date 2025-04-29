class CameraAgoraCard extends HTMLElement {
  // Define internal properties
  _hass;
  _config;
  _client = null;
  _isPlaying = false;
  _isConnecting = false;

  // Required method for Home Assistant cards
  setConfig(config) {
    if (!config.entity) {
      throw new Error("You need to specify an entity");
    }
    this._config = config;
    
    // Prepare shadow DOM container
    this.attachShadow({ mode: 'open' });
    this._render();
  }

  _render() {
    // Use standard CSS
    const style = document.createElement('style');
    style.textContent = `
      .video-container {
        width: 100%;
        height: 300px;
        background: black;
        position: relative;
      }
      .loading {
        color: white;
        text-align: center;
        padding-top: 140px;
      }
      .controls {
        display: flex;
        justify-content: space-around;
        padding: 8px;
        margin-top: 8px;
      }
      .control-button {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.1);
        border: none;
        color: gray;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .control-button svg {
        width: 24px;
        height: 24px;
        fill: currentColor;
      }
      .spinner {
        border: 4px solid rgba(255, 255, 255, 0.3);
        border-radius: 50%;
        border-top: 4px solid white;
        width: 30px;
        height: 30px;
        animation: spin 1s linear infinite;
        margin: 0 auto 10px auto;
      }
      @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }
      .overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.7);
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        color: white;
      }
    `;
    
    const card = document.createElement('ha-card');
    card.innerHTML = `
      <div class="card-content" style="padding: 8px;">
        <div id="agora-video" class="video-container">
          <div id="loading" class="overlay">
            <div class="spinner"></div>
            <div>Loading video...</div>
          </div>
        </div>
        <div class="controls">
          <button id="play-button" class="control-button">
            <svg viewBox="0 0 24 24"><path d="M8,5.14V19.14L19,12.14L8,5.14Z" /></svg>
          </button>
          <button id="pause-button" class="control-button">
            <svg viewBox="0 0 24 24"><path d="M14,19H18V5H14M6,19H10V5H6V19Z" /></svg>
          </button>
          <button id="fullscreen-button" class="control-button">
            <svg viewBox="0 0 24 24"><path d="M5,5H10V7H7V10H5V5M19,5V10H17V7H14V5H19M5,19V14H7V17H10V19H5M19,19H14V17H17V14H19V19Z" /></svg>
          </button>
        </div>
      </div>
    `;
    
    // Clear existing content and add new elements
    this.shadowRoot.innerHTML = '';
    this.shadowRoot.appendChild(style);
    this.shadowRoot.appendChild(card);
    
    // Add event listeners for the controls
    this.shadowRoot.getElementById('play-button').addEventListener('click', () => this._playVideo());
    this.shadowRoot.getElementById('pause-button').addEventListener('click', () => this._stopVideo());
    this.shadowRoot.getElementById('fullscreen-button').addEventListener('click', () => this._toggleFullscreen());
  }

  // Method called when Home Assistant updates the state
  set hass(hass) {
    this._hass = hass;
    
    // If the card hasn't been initialized yet, do it now
    if (!this._initialized && this._config) {
      this._initialized = true;
      this._setupAgoraStream();
    }
  }

  // Standard configuration for the editor
  static getStubConfig() {
    return { entity: "" };
  }

  // Initialize Agora streaming
  async _setupAgoraStream() {
    if (!this._hass || !this._config) return;
    
    const entityId = this._config.entity;
    if (!this._hass.states[entityId]) {
      this._showError("Entity not found");
      return;
    }

    try {
      // Load Agora SDK
      await this._loadAgoraSDK();
      
      if (!window.AgoraRTC) {
        this._showError("Agora SDK not loaded correctly");
        return;
      }
      
      // Start playback immediately
      this._playVideo();
    } catch (error) {
      console.error("Error initializing Agora:", error);
      this._showError(`Error: ${error.message}`);
    }
  }
  
  // Show the loading indicator
  _showLoading(message = "Loading video...") {
    const videoContainer = this.shadowRoot.getElementById('agora-video');
    
    // Create or update the loading overlay
    let loadingOverlay = this.shadowRoot.getElementById('loading');
    if (!loadingOverlay) {
      loadingOverlay = document.createElement('div');
      loadingOverlay.id = 'loading';
      loadingOverlay.className = 'overlay';
      videoContainer.appendChild(loadingOverlay);
    }
    
    loadingOverlay.innerHTML = `
      <div class="spinner"></div>
      <div>${message}</div>
    `;
    loadingOverlay.style.display = 'flex';
  }
  
  // Hide the loading indicator
  _hideLoading() {
    const loadingOverlay = this.shadowRoot.getElementById('loading');
    if (loadingOverlay) {
      loadingOverlay.style.display = 'none';
    }
  }
  
  // Play video
  async _playVideo() {
    if (this._isPlaying || this._isConnecting) return;
    
    this._isConnecting = true;
    this._showLoading("Connecting to video stream...");
    
    try {
      const entityId = this._config.entity;
      
      // Update tokens and start streaming
      await this._hass.callService('mammotion', 'refresh_stream', { entity_id: entityId });
      await this._hass.callService('mammotion', 'start_video', { entity_id: entityId });
      
      const attr = this._hass.states[entityId].attributes;
      const videoContainer = this.shadowRoot.getElementById('agora-video');
      
      const clientConfig = {
        mode: 'live',
        codec: 'vp8',
        disableLog: false,
        enableLogUpload: false,  // Disable log upload
        role: "host",
      };
      
      // Create Agora client
      if (this._client) {
        await this._client.leave();
      }
      
      const client = window.AgoraRTC.createClient(clientConfig);
      
      client.on('user-published', async (user, mediaType) => {
        await client.subscribe(user, mediaType);
        if (mediaType === 'video') {
          // Hide loading when video starts playing
          this._hideLoading();
          user.videoTrack.play(videoContainer);
        }
        if (mediaType === "audio") {
          user.audioTrack.play();
        }
      });
      
      // Register error handlers
      client.on('connection-state-change', (state) => {
        console.log(`Agora connection state: ${state}`);
        if (state === 'DISCONNECTED') {
          this._isPlaying = false;
          this._hideLoading();
          this._showLoading("Connection lost. Click play to reconnect.");
        } else if (state === 'CONNECTING') {
          this._showLoading("Connecting...");
        }
      });

      client.on("user-unpublished", (user, mediaType) => {
        if (mediaType === "video") {
          this._showLoading("Video stream ended.");
        }
      });
      
      client.setClientRole("host");
      
      console.log("App ID: " + attr.appId);
      console.log("App Channel: " + attr.channelName);
      console.log("App Token: " + attr.token);
      console.log("App UID: " + attr.uid);
      
      // Set timeout for connection
      const connectionTimeout = setTimeout(() => {
        if (!this._isPlaying) {
          this._showLoading("Connection timeout. Click play to retry.");
          this._isConnecting = false;
        }
      }, 20000);
      
      await client.join(attr.appId, attr.channelName, attr.token, parseInt(attr.uid));
      clearTimeout(connectionTimeout);
      
      this._client = client;
      this._isPlaying = true;
      this._isConnecting = false;
      
      // Set a reconnection check
      this._startConnectionCheck();
      
    } catch (error) {
      console.error("Error starting video:", error);
      this._isConnecting = false;
      this._showLoading(`Connection error. Click play to retry.`);
    }
  }
  
  // Start periodic connection check
  _startConnectionCheck() {
    if (this._connectionCheckInterval) {
      clearInterval(this._connectionCheckInterval);
    }
    
    this._connectionCheckInterval = setInterval(async () => {
      if (this._isPlaying && this._client && !this._isConnecting) {
        //await this._client.setClientRole('audience');
        //console.log("Reset role");
        const state = this._client.connectionState;
        if (state !== 'CONNECTED') {
          this._showLoading("Connection unstable...");
        }
        if (this._hass && this._entityId) {
          //await this._hass.callService('mammotion', 'start_video', { entity_id: this._entityId });
        }
      }
    }, 5000);
  }
  
  // Stop video
  async _stopVideo() {
    if (!this._isPlaying && !this._isConnecting) return;
    
    try {
      if (this._connectionCheckInterval) {
        clearInterval(this._connectionCheckInterval);
      }
      
      if (this._client) {
        await this._client.leave();
      }
      
      this._isPlaying = false;
      this._isConnecting = false;
      
      if (this._hass && this._config && this._config.entity) {
        await this._hass.callService('mammotion', 'stop_video', { entity_id: this._config.entity });
      }
      
      // Show video stopped message
      this._showLoading("Video stopped. Click play to start.");
    } catch (error) {
      console.error("Error stopping video:", error);
    }
  }
  
  // Toggle fullscreen
  _toggleFullscreen() {
    const videoContainer = this.shadowRoot.getElementById('agora-video');
    
    if (!document.fullscreenElement) {
      videoContainer.requestFullscreen().catch(err => {
        console.error(`Error attempting to enable fullscreen: ${err.message}`);
      });
    } else {
      document.exitFullscreen();
    }
  }

  // Cleanup when card is removed
  disconnectedCallback() {
    if (this._connectionCheckInterval) {
      clearInterval(this._connectionCheckInterval);
    }
    this._stopVideo();
  }

  // Show an error message
  _showError(message) {
    const videoContainer = this.shadowRoot.getElementById('agora-video');
    if (videoContainer) {
      videoContainer.innerHTML = `<div style="color: red; padding: 1em; text-align: center;">${message}</div>`;
    }
  }

  // Load Agora SDK
  async _loadAgoraSDK() {
    if (!window.AgoraRTC) {
      return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = 'https://download.agora.io/sdk/release/AgoraRTC_N.js';
        script.onload = resolve;
        script.onerror = () => reject(new Error("Unable to load Agora SDK"));
        document.head.appendChild(script);
      });
    }
  }
}

// Register the element
customElements.define('camera-agora-card', CameraAgoraCard);