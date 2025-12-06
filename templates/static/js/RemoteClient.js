/**
 * RemoteClient.js
 * 
 * Manages the PeerJS connection on the "Client" (Phone) side.
 * - Connects to the Host using the ID from the URL.
 * - Sends commands (play, pause, volume) to the Host.
 * - Updates UI based on status messages from the Host.
 */

document.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    const hostId = params.get('id');

    if (!hostId) {
        alert("No Host ID found. Please scan the QR code again.");
        return;
    }

    const loadingLog = document.getElementById('loading-log');
    const debugLog = document.getElementById('debug-log');

    function log(msg) {
        console.log(msg);
        const entry = document.createElement('div');
        entry.textContent = `> ${msg}`;
        if (debugLog) {
            debugLog.appendChild(entry);
            debugLog.scrollTop = debugLog.scrollHeight;
        }
        if (loadingLog) {
            loadingLog.textContent = msg;
        }
    }

    log("Initializing Remote Client...");
    log(`Host ID from URL: ${hostId}`);

    const peer = new Peer();
    let conn = null;

    const loadingOverlay = document.getElementById('loading');
    const statusEl = document.getElementById('connection-status');
    const titleEl = document.getElementById('track-title');
    const playPauseBtn = document.getElementById('play-pause-btn');
    const playPauseIcon = playPauseBtn.querySelector('i');
    const prevBtn = document.getElementById('prev-btn');
    const nextBtn = document.getElementById('next-btn');
    const shuffleBtn = document.getElementById('shuffle-btn');
    const browseBtn = document.getElementById('browse-btn');
    const browseModal = document.getElementById('browse-modal');
    const browseContent = document.getElementById('browse-content');
    const browseCloseBtn = document.getElementById('browse-close-btn');
    const browseBackBtn = document.getElementById('browse-back-btn');
    const browseTitle = document.getElementById('browse-title');

    let library = null;
    let currentView = 'composers'; // 'composers' or 'works'

    // Fetch Library
    fetch('/library.json')
        .then(response => response.json())
        .then(data => {
            library = data;
            log("Library loaded: " + library.composers.length + " composers");
        })
        .catch(err => log("Error loading library: " + err));

    // Browse UI Functions
    function renderComposers() {
        if (!library) return;
        currentView = 'composers';
        browseTitle.textContent = "Library";
        browseBackBtn.classList.add('hidden');
        browseContent.innerHTML = '';

        library.composers.forEach(comp => {
            const item = document.createElement('div');
            item.className = 'browse-item';
            item.innerHTML = `
                <span class="browse-item-title">${comp.name}</span>
                <span class="browse-item-count">${comp.works.length}</span>
            `;
            item.onclick = () => renderWorks(comp);
            browseContent.appendChild(item);
        });
    }

    function renderWorks(composer) {
        currentView = 'works';
        browseTitle.textContent = composer.name;
        browseBackBtn.classList.remove('hidden');
        browseContent.innerHTML = '';

        // Play All Button
        const playAllBtn = document.createElement('button');
        playAllBtn.className = 'play-all-btn';
        playAllBtn.textContent = "Play All";
        playAllBtn.onclick = () => {
            playTracks(composer.works);
            closeBrowse();
        };
        browseContent.appendChild(playAllBtn);

        composer.works.forEach(work => {
            const item = document.createElement('div');
            item.className = 'browse-item';
            item.innerHTML = `<span class="browse-item-title">${work.title}</span>`;
            item.onclick = () => {
                playTracks([work]);
                closeBrowse();
            };
            browseContent.appendChild(item);
        });
    }

    function openBrowse() {
        renderComposers();
        browseModal.classList.remove('hidden');
    }

    function closeBrowse() {
        browseModal.classList.add('hidden');
    }

    function playTracks(tracks) {
        // Send list of tracks to host
        // tracks: [{title, url}, ...]
        sendCommand('play_tracks', tracks);
    }

    // Event Listeners
    if (shuffleBtn) {
        shuffleBtn.addEventListener('click', () => {
            shuffleBtn.classList.toggle('active');
            sendCommand('shuffle');
        });
    }

    if (browseBtn) {
        browseBtn.addEventListener('click', openBrowse);
    }

    if (browseCloseBtn) {
        browseCloseBtn.addEventListener('click', closeBrowse);
    }

    if (browseBackBtn) {
        browseBackBtn.addEventListener('click', renderComposers);
    }

    peer.on('open', (id) => {
        log('My Peer ID: ' + id);
        connectToHost(hostId);
    });

    peer.on('error', (err) => {
        console.error(err);
        log("Peer Error: " + err.type);
        statusEl.textContent = "Error: " + err.type;
        loadingOverlay.classList.add('hidden'); // Hide loading so user can see error
    });

    function connectToHost(id) {
        log("Connecting to host: " + id);
        conn = peer.connect(id);

        conn.on('open', () => {
            log("Connected to Host!");
            statusEl.textContent = "Connected";
            statusEl.style.color = "#03dac6";
            loadingOverlay.classList.add('hidden');
        });

        conn.on('data', (data) => {
            // Don't log every state update to avoid spam, unless needed
            // log("Received data"); 
            updateUI(data);
        });

        conn.on('close', () => {
            log("Connection closed by host.");
            statusEl.textContent = "Disconnected";
            statusEl.style.color = "#cf6679";
        });

        conn.on('error', (err) => {
            log("Connection Error: " + err);
        });
    }

    function sendCommand(cmd, val = null) {
        if (conn && conn.open) {
            conn.send({ cmd, val });
        }
    }

    function updateUI(state) {
        if (state.title) titleEl.textContent = state.title;

        if (state.isPlaying) {
            playPauseIcon.classList.remove('fa-play');
            playPauseIcon.classList.add('fa-pause');
        } else {
            playPauseIcon.classList.remove('fa-pause');
            playPauseIcon.classList.add('fa-play');
        }

        if (state.volume !== undefined) {
            volumeSlider.value = state.volume;
        }

        if (state.isShuffle !== undefined) {
            if (state.isShuffle) {
                shuffleBtn.classList.add('active');
            } else {
                shuffleBtn.classList.remove('active');
            }
        }
    }

    playPauseBtn.addEventListener('click', () => sendCommand('toggle'));
    prevBtn.addEventListener('click', () => sendCommand('prev'));
    nextBtn.addEventListener('click', () => sendCommand('next'));

    volumeSlider.addEventListener('input', (e) => {
        sendCommand('volume', e.target.value);
    });
});
