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
      .camera-label {
        font-family: Arial, sans-serif;
        z-index: 10;
        user-select: none;
        box-shadow: 0 1px 3px rgba(0,0,0,0.3);
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
          <button id="switch-video-button" class="control-button" style="display: none;">
            <svg viewBox="0 0 24 24"><path d="M19,8L15,12H18C18,15.31 15.31,18 12,18C11,18 10.03,17.75 9.2,17.3L7.74,18.76C8.97,19.54 10.43,20 12,20C16.42,20 20,16.42 20,12H23L19,8M6,12C6,8.69 8.69,6 12,6C13,6 13.97,6.25 14.8,6.7L16.26,5.24C15.03,4.46 13.57,4 12,4C7.58,4 4,7.58 4,12H1L5,16L9,12H6Z" /></svg>
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
    this.shadowRoot.getElementById('switch-video-button').addEventListener('click', () => this._switchCamera());

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

      //Define Cameras name
      const cameras = {
        1: "Left Camera",  
        2: "Right Camera",
        3: "Rear Camera"
      };

      //Array for keep traking videos
      const remoteUsers = [];
      let currentVideoIndex = 0;
      const switchVideoButton = this.shadowRoot.getElementById('switch-video-button');

      //Recover preferred camera (if exist)
      const savedCameraUid = localStorage.getItem('preferredMammotionCameraUid');
      let preferredCameraIndex = null;

      // Update tokens and start streaming
      await this._hass.callService('mammotion', 'refresh_stream', { entity_id: entityId });
      await this._hass.callService('mammotion', 'start_video', { entity_id: entityId });
      const { response } = await this._hass.callService('mammotion', 'get_tokens', { entity_id: entityId, return_response: true }, {}, true, true);

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

            // Add user to array if not already present
          if (!remoteUsers.some(u => u.uid === user.uid)) {
            remoteUsers.push(user);
            
            // If this is the saved preferred camera, set index
            if (savedCameraUid && user.uid.toString() === savedCameraUid) {
                preferredCameraIndex = remoteUsers.length - 1;
            }
          }
          
          // Show switch button if more than one video
          if (remoteUsers.length > 1) {
              switchVideoButton.style.display = 'block';
          }
          
          // Set to preferred camera if found
          if (preferredCameraIndex !== null) {
              currentVideoIndex = preferredCameraIndex;
              preferredCameraIndex = null; // Reset after use
          }
          
          // Display current video
          this._showCurrentVideo();

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
        if (mediaType === 'video') {
            // Remove user from array
            const index = remoteUsers.findIndex(u => u.uid === user.uid);
            if (index > -1) {
                remoteUsers.splice(index, 1);
            }
            
            // Update current index if needed
            if (currentVideoIndex >= remoteUsers.length && remoteUsers.length > 0) {
                currentVideoIndex = 0;
            }
            
            // Hide switch button if only one video
            if (remoteUsers.length <= 1) {
                switchVideoButton.style.display = 'none';
            }

            if(remoteUsers.length <= 0)
            {
              this._showLoading("Video stream end");
              return;
            }
              
            
            // Display current video
            this._showCurrentVideo();
        }
      });

      client.setClientRole("host");

      console.log("App ID: " + response.appId);
      console.log("App Channel: " + response.channelName);
      console.log("App Token: " + response.token);
      console.log("App UID: " + response.uid);

      // Set timeout for connection
      const connectionTimeout = setTimeout(() => {
        if (!this._isPlaying) {
          this._showLoading("Connection timeout. Click play to retry.");
          this._isConnecting = false;
        }
      }, 20000);

      await client.join(response.appId, response.channelName, response.token, parseInt(response.uid));
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

  // Switch camera
  _switchCamera() {
    currentVideoIndex = (currentVideoIndex + 1) % remoteUsers.length;
    this._showCurrentVideo();
  }

    // Function to display only the current video
  _showCurrentVideo() {
    // Clear container (but keep loading overlay)
    const loadingElement = this.shadowRoot.getElementById('loading');
    videoContainer.innerHTML = '';
    videoContainer.appendChild(loadingElement);
    
    // If there are users with video
    if (remoteUsers.length > 0) {
        const currentUser = remoteUsers[currentVideoIndex];
        
        // Create div for current video
        const userVideoDiv = document.createElement('div');
        userVideoDiv.id = `user-${currentUser.uid}`;
        userVideoDiv.style.width = '100%';
        userVideoDiv.style.height = '100%';
        videoContainer.appendChild(userVideoDiv);
        
        // Create camera name label
        const cameraLabel = document.createElement('div');
        cameraLabel.className = 'camera-label';
        cameraLabel.textContent = cameras[currentUser.uid] || `Camera ${currentUser.uid}`;
        cameraLabel.style.position = 'absolute';
        cameraLabel.style.bottom = '10px';
        cameraLabel.style.right = '10px';
        cameraLabel.style.background = 'rgba(0, 0, 0, 0.6)';
        cameraLabel.style.color = 'white';
        cameraLabel.style.padding = '4px 8px';
        cameraLabel.style.borderRadius = '4px';
        cameraLabel.style.fontSize = '12px';
        videoContainer.appendChild(cameraLabel);
        
        // Save current camera preference
        localStorage.setItem('preferredMammotionCameraUid', currentUser.uid.toString());
        
        // Play the video
        currentUser.videoTrack.play(userVideoDiv);
    } else {
        // If no videos, show loading
        this._showLoading("Loading...");
    }
  }
  
  // Start periodic connection check
  _startConnectionCheck() {
    if (this._connectionCheckInterval) {
      clearInterval(this._connectionCheckInterval);
    }
    
    this._connectionCheckInterval = setInterval(async () => {
      if (this._isPlaying && this._client && !this._isConnecting) {
        const state = this._client.connectionState;
        if (state !== 'CONNECTED') {
          this._showLoading("Connection unstable...");
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