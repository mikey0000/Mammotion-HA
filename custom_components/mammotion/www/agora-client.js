class CameraAgoraCard extends HTMLElement {
  // Define internal properties
  _hass;
  _config;
  _client = null;
  _isPlaying = false;
  _isConnecting = false;
  _connectionCheckInterval = null;
  
  // Add camera tracking variables as class properties
  _remoteUsers = [];
  _currentVideoIndex = 0;
  _cameras = {
    1: "Left Camera",  
    2: "Right Camera",
    3: "Rear Camera"
  };
  
  // Add joystick control properties
  _disclaimerShown = false;
  _moveIntervals = {
    up: null,
    down: null,
    left: null,
    right: null
  };

  // Required method for Home Assistant cards
  setConfig(config) {
    if (!config.entity) {
      throw new Error("You need to specify an entity");
    }

    //Define custom parameters
    this._config = {
      ...config,
      autostart: config.autostart !== undefined ? config.autostart : false,
      enableJoystick: config.enableJoystick !== undefined ? config.enableJoystick : false,
      speed: config.speed !== undefined ? parseFloat(config.speed) : 0.4
    };
    
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
      /* Joystick CSS */
      .joystick-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        z-index: 5;
        pointer-events: none; /* Allows clicks to pass through to underlying elements */
        opacity: 0; /* Initially hidden */
        transition: opacity 0.3s;
      }
      .joystick-overlay.visible {
        opacity: 1;
      }
      .joystick-button {
        position: absolute;
        width: 40px;
        height: 40px;
        background: rgba(60, 60, 60, 0.6);
        border: none;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        pointer-events: auto; /* Makes button clickable */
        transition: background 0.2s;
        box-shadow: none;
      }
      .joystick-button:active {
        background: rgba(80, 80, 80, 0.7);
      }
      .joystick-up {
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
      }
      .joystick-down {
        bottom: 20px;
        left: 50%;
        transform: translateX(-50%);
      }
      .joystick-left {
        left: 20px;
        top: 50%;
        transform: translateY(-50%);
      }
      .joystick-right {
        right: 20px;
        top: 50%;
        transform: translateY(-50%);
      }
      .disclaimer-overlay {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0, 0, 0, 0.8);
        z-index: 10;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        color: white;
        text-align: center;
        box-sizing: border-box;
        padding: 10px;
      }
      .disclaimer-button {
        margin-top: 15px;
        padding: 8px 16px;
        background: #4CAF50;
        color: white;
        border: none;
        border-radius: 4px;
        cursor: pointer;
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
    return { 
      entity: "",
      autostart: false,
      enableJoystick: false,
      speed: 0.4 
    };
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
      
      // Setup joystick overlay if enabled
      if (this._config.enableJoystick) {
        this._setupJoystickOverlay();
      }
      
      if(this._config.autostart)
      {
        // Start playback immediately
        this._playVideo();
      }
      else
        this._showLoading("Press play to start video stream");
      
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
    
    // Ensure joystick overlay is setup if enabled
    if (this._config.enableJoystick) {
      this._setupJoystickOverlay();
    }
    
    try {
      const entityId = this._config.entity;
      const switchVideoButton = this.shadowRoot.getElementById('switch-video-button');
      const videoContainer = this.shadowRoot.getElementById('agora-video');

      // Recover preferred camera (if exists)
      const savedCameraUid = localStorage.getItem('preferredMammotionCameraUid');
      let preferredCameraIndex = null;

      // Update tokens and start streaming
      await this._hass.callService('mammotion', 'refresh_stream', { entity_id: entityId });
      await this._hass.callService('mammotion', 'start_video', { entity_id: entityId });
      const { response } = await this._hass.callService('mammotion', 'get_tokens', { entity_id: entityId, return_response: true }, {}, true, true);

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
          if (!this._remoteUsers.some(u => u.uid === user.uid)) {
            this._remoteUsers.push(user);
            
            // If this is the saved preferred camera, set index
            if (savedCameraUid && user.uid.toString() === savedCameraUid) {
              preferredCameraIndex = this._remoteUsers.length - 1;
            }
          }
          
          // Show switch button if more than one video
          if (this._remoteUsers.length > 1) {
            switchVideoButton.style.display = 'block';
          }
          
          // Set to preferred camera if found
          if (preferredCameraIndex !== null) {
            this._currentVideoIndex = preferredCameraIndex;
            preferredCameraIndex = null; // Reset after use
          }
          
          // Display current video
          this._showCurrentVideo();
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
          this._toggleJoystickVisibility(false);
          
          // Stop any active movement commands when connection is lost
          Object.keys(this._moveIntervals).forEach(dir => {
            this._stopContinuousMove(dir);
          });
        } else if (state === 'CONNECTING') {
          this._showLoading("Connecting...");
          this._toggleJoystickVisibility(false);
        }
      });

      client.on("user-unpublished", (user, mediaType) => {
        if (mediaType === 'video') {
          // Remove user from array
          const index = this._remoteUsers.findIndex(u => u.uid === user.uid);
          if (index > -1) {
            this._remoteUsers.splice(index, 1);
          }
          
          // Update current index if needed
          if (this._currentVideoIndex >= this._remoteUsers.length && this._remoteUsers.length > 0) {
            this._currentVideoIndex = 0;
          }
          
          // Hide switch button if only one video
          if (this._remoteUsers.length <= 1) {
            switchVideoButton.style.display = 'none';
          }

          if (this._remoteUsers.length <= 0) {
            this._showLoading("Video stream end");
            this._toggleJoystickVisibility(false);
            return;
          }
            
          // Display current video
          this._showCurrentVideo();
        }
      });

      client.setClientRole("host");

      console.log("App ID: " + response.appid);
      console.log("App Channel: " + response.channelName);
      console.log("App Token: " + response.token);
      console.log("App UID: " + response.uid);

      // Set timeout for connection
      const connectionTimeout = setTimeout(() => {
        if (!this._isPlaying) {
          this._showLoading("Connection timeout. Click play to retry.");
          this._isConnecting = false;
          this._toggleJoystickVisibility(false);
        }
      }, 20000);

      await client.join(response.appid, response.channelName, response.token, parseInt(response.uid));
      clearTimeout(connectionTimeout);
      
      this._client = client;
      this._isPlaying = true;
      this._isConnecting = false;
      
      // Show joystick controls if enabled
      if (this._config.enableJoystick) {
        this._toggleJoystickVisibility(true);
      }
      
      // Set a reconnection check
      this._startConnectionCheck();
      
    } catch (error) {
      console.error("Error starting video:", error);
      this._isConnecting = false;
      this._showLoading(`Connection error. Click play to retry.`);
      this._toggleJoystickVisibility(false);
    }
  }

  // Switch camera
  _switchCamera() {
    this._currentVideoIndex = (this._currentVideoIndex + 1) % this._remoteUsers.length;
    this._showCurrentVideo();
  }

  // Function to display only the current video
  _showCurrentVideo() {
    const videoContainer = this.shadowRoot.getElementById('agora-video');
    const loadingElement = this.shadowRoot.getElementById('loading');
    const joystickOverlay = this.shadowRoot.getElementById('joystick-overlay');
    
    // Store joystick overlay before clearing container
    if (joystickOverlay) {
      videoContainer.removeChild(joystickOverlay);
    }
    
    // Clear container (but keep loading overlay if it exists)
    if (loadingElement) {
      videoContainer.removeChild(loadingElement);
    }
    
    videoContainer.innerHTML = '';
    
    // Restore overlays
    if (loadingElement) {
      videoContainer.appendChild(loadingElement);
    }
    
    if (this._config.enableJoystick && joystickOverlay) {
      videoContainer.appendChild(joystickOverlay);
      this._toggleJoystickVisibility(this._isPlaying);
    } else if (this._config.enableJoystick) {
      // If joystick overlay was missing, recreate it
      this._setupJoystickOverlay();
    }
    
    // If there are users with video
    if (this._remoteUsers.length > 0) {
      const currentUser = this._remoteUsers[this._currentVideoIndex];
      
      // Create div for current video
      const userVideoDiv = document.createElement('div');
      userVideoDiv.id = `user-${currentUser.uid}`;
      userVideoDiv.style.width = '100%';
      userVideoDiv.style.height = '100%';
      videoContainer.appendChild(userVideoDiv);
      
      // Create camera name label
      const cameraLabel = document.createElement('div');
      cameraLabel.className = 'camera-label';
      cameraLabel.textContent = this._cameras[currentUser.uid] || `Camera ${currentUser.uid}`;
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
  
  
  // Add safety interval to check and stop any continuous movements if video is not playing
  _startConnectionCheck() {
    if (this._connectionCheckInterval) {
      clearInterval(this._connectionCheckInterval);
    }
    
    this._connectionCheckInterval = setInterval(async () => {
      if (this._isPlaying && this._client && !this._isConnecting) {
        const state = this._client.connectionState;
        if (state !== 'CONNECTED') {
          this._showLoading("Connection unstable...");
          this._toggleJoystickVisibility(false);
          
          // Stop any movement when connection is unstable
          Object.keys(this._moveIntervals).forEach(dir => {
            this._stopContinuousMove(dir);
          });
        }
      } else {
        // If not playing, ensure all movements are stopped
        Object.keys(this._moveIntervals).forEach(dir => {
          this._stopContinuousMove(dir);
        });
      }
    }, 2000); // Check every 2 seconds
  }
  
  // Stop video
  async _stopVideo() {
    if (!this._isPlaying && !this._isConnecting) return;
    
    try {
      if (this._connectionCheckInterval) {
        clearInterval(this._connectionCheckInterval);
      }
      
      // Stop all continuous movement commands
      Object.keys(this._moveIntervals).forEach(dir => {
        this._stopContinuousMove(dir);
      });
      
      // Clean up video tracks for each remote user before leaving
      if (this._remoteUsers.length > 0) {
        for (const user of this._remoteUsers) {
          if (user.videoTrack) {
            user.videoTrack.stop();
            user.videoTrack.close();
          }
          if (user.audioTrack) {
            user.audioTrack.stop();
            user.audioTrack.close();
          }
        }
      }
      
      // Clear remote users array
      this._remoteUsers = [];
      
      // Reset switch button
      const switchVideoButton = this.shadowRoot.getElementById('switch-video-button');
      if (switchVideoButton) {
        switchVideoButton.style.display = 'none';
      }
      
      if (this._client) {
        await this._client.leave();
        this._client = null;
      }
      
      this._isPlaying = false;
      this._isConnecting = false;
      
      // Hide joystick controls
      this._toggleJoystickVisibility(false);
      
      // Save references to important elements
      const videoContainer = this.shadowRoot.getElementById('agora-video');
      const loadingElement = this.shadowRoot.getElementById('loading');
      const joystickOverlay = this.shadowRoot.getElementById('joystick-overlay');
      
      // Create a temporary container for the elements we want to preserve
      const tempContainer = document.createElement('div');
      if (loadingElement) tempContainer.appendChild(loadingElement.cloneNode(true));
      if (joystickOverlay) tempContainer.appendChild(joystickOverlay.cloneNode(true));
      
      // Clean up video container
      videoContainer.innerHTML = '';
      
      // Restore the preserved elements
      Array.from(tempContainer.children).forEach(el => {
        videoContainer.appendChild(el);
      });
      
      if (this._hass && this._config && this._config.entity) {
        await this._hass.callService('mammotion', 'stop_video', { entity_id: this._config.entity });
      }
      
      // Reinitialize event listeners for joystick after DOM manipulation
      if (this._config.enableJoystick) {
        this._setupJoystickControls();
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

  // Setup global event handlers for safety
  constructor() {
    super();
    
    // Add global event handlers to stop movements if mouseup/touchend happens outside the component
    this._boundGlobalMouseUp = this._globalMouseUp.bind(this);
    this._boundGlobalTouchEnd = this._globalTouchEnd.bind(this);
    
    document.addEventListener('mouseup', this._boundGlobalMouseUp);
    document.addEventListener('touchend', this._boundGlobalTouchEnd);
  }
  
  // Global mouseup handler (safety)
  _globalMouseUp(e) {
    if (this._isPlaying) {
      // Check if any move intervals are active and stop them
      Object.keys(this._moveIntervals).forEach(dir => {
        if (this._moveIntervals[dir]) {
          this._stopContinuousMove(dir);
        }
      });
    }
  }
  
  // Global touchend handler (safety)
  _globalTouchEnd(e) {
    if (this._isPlaying) {
      // Check if any move intervals are active and stop them
      Object.keys(this._moveIntervals).forEach(dir => {
        if (this._moveIntervals[dir]) {
          this._stopContinuousMove(dir);
        }
      });
    }
  }
  
  // Remove global listeners when disconnected
  disconnectedCallback() {
    if (this._connectionCheckInterval) {
      clearInterval(this._connectionCheckInterval);
    }
    
    // Remove global event listeners
    document.removeEventListener('mouseup', this._boundGlobalMouseUp);
    document.removeEventListener('touchend', this._boundGlobalTouchEnd);
    
    // Stop all continuous movement
    Object.keys(this._moveIntervals).forEach(dir => {
      this._stopContinuousMove(dir);
    });
    
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
  
  // Set up joystick controls
  _setupJoystickControls() {
    if (!this._config.enableJoystick) return;
    
    // Add event listeners for joystick buttons
    const directions = ['up', 'down', 'left', 'right'];
    
    directions.forEach(dir => {
      const button = this.shadowRoot.getElementById(`joystick-${dir}`);
      if (button) {
        // Remove any existing event listeners to prevent duplicates
        const newButton = button.cloneNode(true);
        button.parentNode.replaceChild(newButton, button);
        
        // For single clicks
        newButton.addEventListener('click', (e) => {
          e.stopPropagation();
          this._handleJoystickPress(dir);
        });
        
        // For continuous press
        newButton.addEventListener('mousedown', (e) => {
          e.stopPropagation();
          this._startContinuousMove(dir);
        });
        newButton.addEventListener('mouseup', (e) => {
          e.stopPropagation();
          this._stopContinuousMove(dir);
        });
        newButton.addEventListener('mouseleave', (e) => {
          e.stopPropagation();
          this._stopContinuousMove(dir);
        });
        
        // Touch support for mobile devices
        newButton.addEventListener('touchstart', (e) => {
          e.preventDefault(); // Prevents scrolling
          e.stopPropagation();
          this._startContinuousMove(dir);
        });
        newButton.addEventListener('touchend', (e) => {
          e.stopPropagation();
          this._stopContinuousMove(dir);
        });
      }
    });
  }
  
  // Create joystick overlay if needed
  _setupJoystickOverlay() {
    if (!this._config.enableJoystick) return;
    
    const videoContainer = this.shadowRoot.getElementById('agora-video');
    const existingOverlay = this.shadowRoot.getElementById('joystick-overlay');
    
    if (!existingOverlay) {
      const joystickOverlay = document.createElement('div');
      joystickOverlay.id = 'joystick-overlay';
      joystickOverlay.className = 'joystick-overlay';
      joystickOverlay.innerHTML = `
        <button id="joystick-up" class="joystick-button joystick-up">
          <svg viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M7.41,15.41L12,10.83L16.59,15.41L18,14L12,8L6,14L7.41,15.41Z" /></svg>
        </button>
        <button id="joystick-down" class="joystick-button joystick-down">
          <svg viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M7.41,8.59L12,13.17L16.59,8.59L18,10L12,16L6,10L7.41,8.59Z" /></svg>
        </button>
        <button id="joystick-left" class="joystick-button joystick-left">
          <svg viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M15.41,16.59L10.83,12L15.41,7.41L14,6L8,12L14,18L15.41,16.59Z" /></svg>
        </button>
        <button id="joystick-right" class="joystick-button joystick-right">
          <svg viewBox="0 0 24 24" width="24" height="24"><path fill="white" d="M8.59,16.59L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.59Z" /></svg>
        </button>
      `;
      
      videoContainer.appendChild(joystickOverlay);
      
      // Setup event listeners
      this._setupJoystickControls();
    }
  }
  
  // Toggle joystick visibility based on playback state
  _toggleJoystickVisibility(show) {
    if (!this._config.enableJoystick) return;
    
    const joystickOverlay = this.shadowRoot.getElementById('joystick-overlay');
    if (joystickOverlay) {
      if (show) {
        joystickOverlay.classList.add('visible');
      } else {
        joystickOverlay.classList.remove('visible');
      }
    }
  }
  
  // Handle joystick button press
  _handleJoystickPress(direction) {
    if (!this._disclaimerShown) {
      this._showMoveDisclaimer(() => {
        this._disclaimerShown = true;
        this._sendMoveCommand(direction);
      });
    } else {
      this._sendMoveCommand(direction);
    }
  }
  
  // Show disclaimer before first movement
  _showMoveDisclaimer(onAccept) {
    const videoContainer = this.shadowRoot.getElementById('agora-video');
    
    // Create disclaimer overlay
    const disclaimerOverlay = document.createElement('div');
    disclaimerOverlay.className = 'disclaimer-overlay';
    disclaimerOverlay.innerHTML = `
      <h3>Warning!</h3>
      <p>By pressing these controls, the robot will physically move.</p>
      <p>Please be aware that the video feed may not be in real time.</p>
      <p>Make sure the area around the robot is clear and safe.</p>
      <button id="disclaimer-accept" class="disclaimer-button">I understand, proceed</button>
    `;
    
    videoContainer.appendChild(disclaimerOverlay);
    
    // Add event listener to accept button
    const acceptButton = disclaimerOverlay.querySelector('#disclaimer-accept');
    acceptButton.addEventListener('click', (e) => {
      e.stopPropagation(); // Prevent click from propagating
      
      // Stop any active movements before removing the disclaimer
      Object.keys(this._moveIntervals).forEach(dir => {
        this._stopContinuousMove(dir);
      });
      
      // Remove the disclaimer after stopping movements
      videoContainer.removeChild(disclaimerOverlay);
      
      // Force mouseup event simulation to ensure any pressed buttons are released
      document.dispatchEvent(new MouseEvent('mouseup'));
      document.dispatchEvent(new TouchEvent('touchend'));
      
      // Wait a small amount of time before allowing movement again
      setTimeout(() => {
        if (onAccept) onAccept();
      }, 100);
    });
  }
  
  // Start continuous movement
  _startContinuousMove(direction) {
    // Don't start if video is not playing
    if (!this._isPlaying) {
      console.log("Cannot start movement - video not playing");
      return;
    }
    
    // First send command immediately
    if (!this._disclaimerShown) {
      this._showMoveDisclaimer(() => {
        this._disclaimerShown = true;
        // Make sure mouseup events are processed first
        setTimeout(() => {
          // Check if the button is still pressed (should not be after disclaimer)
          if (this._isMouseStillDown()) {
            this._sendMoveCommand(direction);
            
            // Start continuous sending after confirmation
            if (this._isPlaying) {
              // First clear any existing interval to be safe
              this._stopContinuousMove(direction);
              
              // Add safety timeout (max 10 seconds of continuous movement)
              this._moveIntervals[direction] = setInterval(() => {
                if (!this._isPlaying) {
                  this._stopContinuousMove(direction);
                  return;
                }
                this._sendMoveCommand(direction);
              }, 500); // Send command every 500ms
              
              // Set a safety timeout - max 10 seconds of movement
              setTimeout(() => {
                this._stopContinuousMove(direction);
              }, 10000);
            }
          }
        }, 200);
      });
    } else {
      this._sendMoveCommand(direction);
      
      // Start continuous sending
      if (this._isPlaying) {
        // First clear any existing interval to be safe
        this._stopContinuousMove(direction);
        
        this._moveIntervals[direction] = setInterval(() => {
          if (!this._isPlaying) {
            this._stopContinuousMove(direction);
            return;
          }
          this._sendMoveCommand(direction);
        }, 500); // Send command every 500ms
        
        // Set a safety timeout - max 10 seconds of movement
        setTimeout(() => {
          this._stopContinuousMove(direction);
        }, 10000);
      }
    }
  }
  
  // Check if mouse button is still being pressed
  _isMouseStillDown() {
    // This is a simple way to check, we assume mouse is not down if method is called after disclaimer
    return false;
  }
  
  // Stop continuous movement
  _stopContinuousMove(direction) {
    if (this._moveIntervals[direction]) {
      clearInterval(this._moveIntervals[direction]);
      this._moveIntervals[direction] = null;
    }
  }
  
  // Send movement command to Home Assistant
  _sendMoveCommand(direction) {
    if (!this._hass || !this._config) {
      this._stopContinuousMove(direction);
      return;
    }
    
    if (!this._isPlaying) {
      console.log("Stopping movement command because video is not playing");
      this._stopContinuousMove(direction);
      return;
    }
    
    const entityId = this._config.entity;
    const speed = this._config.speed;
    
    // Define service and action based on direction
    let service = 'mammotion';
    let action = '';
    
    switch (direction) {
      case 'up':
        action = 'move_forward';
        break;
      case 'down':
        action = 'move_backward';
        break;
      case 'left':
        action = 'move_left';
        break;
      case 'right':
        action = 'move_right';
        break;
    }
    
    // Send command to Home Assistant
    if (action) {
      try {
        this._hass.callService(service, action, { 
          entity_id: entityId, 
          speed: speed 
        });
        console.log(`Sending ${action} command to ${entityId} with speed ${speed}`);
      } catch (error) {
        console.error(`Error sending ${action} command:`, error);
        // Stop continuous movement on error
        this._stopContinuousMove(direction);
      }
    }
  }
}

// Register the element
customElements.define('camera-agora-card', CameraAgoraCard);