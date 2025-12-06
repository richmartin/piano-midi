/**
 * RemoteHost.js
 * 
 * Manages the PeerJS connection on the "Host" (Laptop) side.
 * - Generates a unique Session ID.
 * - Displays a QR code for the Client to connect.
 * - Listens for commands from the Client and controls the Player.
 * - Broadcasts status updates to the Client.
 */
export class RemoteHost {
    constructor(playlist, midiPlayer) {
        this.playlist = playlist;
        this.midiPlayer = midiPlayer;
        this.peer = null;
        this.conn = null;
        this.modal = document.getElementById('remote-modal');
        this.closeBtn = document.getElementById('remote-close-btn');
        this.toggleBtn = document.getElementById('remote-toggle-btn');
        this.qrContainer = document.getElementById('qrcode');
        this.linkElement = document.getElementById('remote-link');
        this.statusElement = document.getElementById('remote-status');

        this.bindEvents();
    }

    init() {
        // Initialize PeerJS
        // We use a random ID prefixed with 'piano-' to avoid collisions on the public server
        // But actually, PeerJS assigns a random ID if we don't provide one.
        // Let's let PeerJS assign one, it's safer.
        this.peer = new Peer();

        this.peer.on('open', (id) => {
            console.log('My Peer ID is: ' + id);
            this.generateQRCode(id);
        });

        this.peer.on('connection', (conn) => {
            console.log('Remote connected!');
            this.conn = conn;
            this.statusElement.textContent = "Remote Connected!";
            this.statusElement.style.color = "#03dac6";

            // Send initial state
            this.broadcastState();

            this.conn.on('data', (data) => {
                console.log('Received command:', data);
                this.handleCommand(data);
            });

            this.conn.on('close', () => {
                console.log('Remote disconnected');
                this.conn = null;
                this.statusElement.textContent = "Remote Disconnected. Waiting...";
                this.statusElement.style.color = "#e0e0e0";
            });
        });

        this.peer.on('error', (err) => {
            console.error('PeerJS error:', err);
            this.statusElement.textContent = "Connection Error: " + err.type;
            this.statusElement.style.color = "#cf6679";
        });

        // Listen for player changes to broadcast
        document.addEventListener('playlist:trackChanged', () => this.broadcastState());
        document.addEventListener('playlist:updated', () => this.broadcastState());
        // We need to hook into play/pause state changes too.
        // The playlist emits trackChanged on play, but maybe not on pause?
        // Let's add a listener for play/pause if possible, or poll?
        // Actually, let's just broadcast whenever we handle a command, and maybe hook into the UI buttons?
        // Better: The Playlist class updates the UI. We can hook into that or just rely on events.
        // Let's add a custom event dispatch in Playlist.js for play/pause state if needed, 
        // but trackChanged might be enough for now.

        // Let's also listen for volume changes if we can.
        // MidiPlayer doesn't emit volume events. We might need to patch that or just send it when we receive a volume command.
    }

    bindEvents() {
        if (this.toggleBtn) {
            this.toggleBtn.addEventListener('click', () => {
                this.openModal();
            });
        }

        if (this.closeBtn) {
            this.closeBtn.addEventListener('click', () => {
                this.closeModal();
            });
        }

        // Close on click outside
        window.addEventListener('click', (e) => {
            if (e.target === this.modal) {
                this.closeModal();
            }
        });
    }

    openModal() {
        if (this.modal) this.modal.classList.remove('hidden');
        if (!this.peer) {
            this.init();
        }
    }

    closeModal() {
        if (this.modal) this.modal.classList.add('hidden');
    }

    generateQRCode(id) {
        // Construct the URL for the remote
        // It assumes remote.html is in the same directory structure
        const baseUrl = window.location.href.substring(0, window.location.href.lastIndexOf('/'));
        // We need to point to /remote.html, but we are likely at /index.html or /
        // Let's assume the server serves /remote.html
        // Actually, since we are using a static site generator or simple server, 
        // we should construct the path carefully.
        // If current is .../index.html, we want .../remote.html

        let remoteUrl = new URL('/remote.html', window.location.origin);
        remoteUrl.searchParams.set('id', id);

        console.log("Remote URL:", remoteUrl.toString());

        this.qrContainer.innerHTML = '';
        new QRCode(this.qrContainer, {
            text: remoteUrl.toString(),
            width: 200,
            height: 200,
            colorDark: "#000000",
            colorLight: "#ffffff",
            correctLevel: QRCode.CorrectLevel.H
        });

        this.linkElement.href = remoteUrl.toString();
        this.linkElement.textContent = remoteUrl.toString();
        this.statusElement.textContent = "Waiting for connection...";
    }

    handleCommand(data) {
        switch (data.cmd) {
            case 'play':
                this.playlist.play();
                break;
            case 'pause':
                this.playlist.pause();
                break;
            case 'toggle':
                this.playlist.togglePlayPause();
                break;
            case 'next':
                this.playlist.next();
                break;
            case 'prev':
                this.playlist.previous();
                break;
            case 'volume':
                if (this.midiPlayer) {
                    this.midiPlayer.setVolume(data.val);
                    // Update the local slider if it exists
                    const slider = document.getElementById('midi-volume');
                    if (slider) slider.value = data.val;
                }
                break;
            case 'shuffle':
                this.playlist.toggleShuffle();
                break;
            case 'play_tracks':
                // data.val is array of {title, url}
                if (data.val && data.val.length > 0) {
                    this.playlist.setQueue(data.val);
                    this.playlist.play(0);
                }
                break;
        }
        // Broadcast state after command to ensure sync
        setTimeout(() => this.broadcastState(), 100);
    }

    broadcastState() {
        if (!this.conn || !this.conn.open) return;

        const track = this.playlist.tracks[this.playlist.currentIndex];
        const state = {
            title: track ? track.title : "Not Playing",
            isPlaying: this.playlist.isPlaying,
            volume: this.midiPlayer ? this.midiPlayer.volume : 11,
            isShuffle: this.playlist.isShuffle
        };

        this.conn.send(state);
    }
}
