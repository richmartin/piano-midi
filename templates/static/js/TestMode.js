export class TestMode {
    constructor(midiController) {
        this.midiController = midiController;
        this.modal = null;
        this.activeButton = null;
        this.originalText = "";
    }

    init() {
        const buildDisplay = document.getElementById('build-number-display');
        if (buildDisplay) {
            buildDisplay.addEventListener('click', () => {
                this.openModal();
            });
        }
        this.injectModal();

        // Listen for track end to reset UI
        document.addEventListener('midi:trackEnded', () => {
            if (this.activeButton) {
                this.resetUI();
            }
        });

        // Listen for note events for visualization
        document.addEventListener('midi:noteOn', (e) => {
            if (this.activeButton) {
                this.logNote(e.detail);
            }
        });

        // Listen for CC events
        document.addEventListener('midi:controlChange', (e) => {
            if (this.activeButton) {
                this.logControlChange(e.detail);
            }
        });
    }

    injectModal() {
        const modalHtml = `
            <div id="test-modal" class="test-modal hidden">
                <div class="test-modal-content">
                    <div class="test-modal-header">
                        <h3>MIDI Test Mode</h3>
                        <button id="test-modal-close" class="close-btn"><i class="fas fa-times"></i></button>
                    </div>
                    <div class="test-modal-body">
                        <p>Select a test pattern to verify your MIDI setup.</p>
                        <div class="test-buttons">
                            <button id="test-chromatic" class="action-button">Chromatic Scale (Full)</button>
                            <button id="test-velocity" class="action-button">Velocity Test</button>
                            <button id="test-sustain" class="action-button">Sustain Pedal Test</button>
                        </div>
                        <div id="test-log" class="test-log"></div>
                    </div>
                </div>
            </div>
        `;
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        this.modal = document.getElementById('test-modal');
        document.getElementById('test-modal-close').addEventListener('click', () => this.closeModal());

        this.bindTestButton('test-chromatic', () => this.playChromaticScale());
        this.bindTestButton('test-velocity', () => this.playVelocityTest());
        this.bindTestButton('test-sustain', () => this.playSustainTest());
    }

    bindTestButton(id, playFn) {
        const btn = document.getElementById(id);
        btn.addEventListener('click', () => {
            if (this.activeButton === btn) {
                // Stop requested
                this.stopTest();
            } else {
                // Start requested
                this.startTest(btn, playFn);
            }
        });
    }

    startTest(btn, playFn) {
        if (this.activeButton) return; // Already running something (should be disabled anyway)

        this.activeButton = btn;
        this.originalText = btn.textContent;
        btn.textContent = "Stop";
        btn.classList.add('active-test'); // Optional styling hook

        // Disable other buttons
        const allButtons = this.modal.querySelectorAll('.action-button');
        allButtons.forEach(b => {
            if (b !== btn) b.disabled = true;
        });

        playFn();

        // Clear log
        const log = document.getElementById('test-log');
        if (log) log.innerHTML = '';
    }

    stopTest() {
        if (this.midiController.player) {
            this.midiController.player.stop();
        }
        this.resetUI();
    }

    resetUI() {
        if (!this.activeButton) return;

        this.activeButton.textContent = this.originalText;
        this.activeButton.classList.remove('active-test');
        this.activeButton = null;

        // Enable all buttons
        const allButtons = this.modal.querySelectorAll('.action-button');
        allButtons.forEach(b => {
            b.disabled = false;
        });
    }

    openModal() {
        if (this.modal) this.modal.classList.remove('hidden');
    }

    closeModal() {
        if (this.modal) this.modal.classList.add('hidden');
        this.stopTest();
    }

    async playMidi(midi) {
        if (!this.midiController.player) return;

        // Convert Tone.js Midi object to array buffer
        const arrayBuffer = midi.toArray();
        await this.midiController.player.loadArrayBuffer(arrayBuffer);
        this.midiController.player.play();
    }

    playChromaticScale() {
        console.log("Generating Chromatic Scale...");
        const midi = new Midi();
        const track = midi.addTrack();

        // A0 (21) to C8 (108)
        let time = 0;
        const duration = 0.2;
        for (let i = 21; i <= 108; i++) {
            track.addNote({
                midi: i,
                time: time,
                duration: duration,
                velocity: 0.8
            });
            time += duration;
        }
        this.playMidi(midi);
    }

    playVelocityTest() {
        console.log("Generating Velocity Test...");
        const midi = new Midi();
        const track = midi.addTrack();

        // Middle C (60)
        let time = 0;
        const duration = 0.5;
        // Increasing
        for (let v = 10; v <= 127; v += 10) {
            track.addNote({
                midi: 60,
                time: time,
                duration: duration,
                velocity: v / 127
            });
            time += duration;
        }
        // Decreasing
        for (let v = 127; v >= 10; v -= 10) {
            track.addNote({
                midi: 60,
                time: time,
                duration: duration,
                velocity: v / 127
            });
            time += duration;
        }
        this.playMidi(midi);
    }

    playSustainTest() {
        console.log("Generating Sustain Test...");
        const midi = new Midi();
        const track = midi.addTrack();

        // Chord C Major
        const chord = [60, 64, 67, 72];

        // 1. Play staccato without sustain
        let time = 0;
        chord.forEach(note => {
            track.addNote({ midi: note, time: time, duration: 0.1, velocity: 0.8 });
        });
        time += 1;

        // 2. Play with sustain
        // Sustain ON (value 1 because tonejs/midi normalizes it, and App.js multiplies by 127)
        // Time shifted slightly earlier to ensure it's processed before notes
        track.addCC({ number: 64, value: 1, time: time - 0.05 });

        chord.forEach(note => {
            track.addNote({ midi: note, time: time, duration: 0.1, velocity: 0.8 });
        });

        // Hold for 2 seconds
        time += 2;

        // Sustain OFF
        track.addCC({ number: 64, value: 0, time: time });

        this.playMidi(midi);
    }

    logNote(note) {
        const log = document.getElementById('test-log');
        if (!log) return;

        const entry = document.createElement('div');
        entry.className = 'log-entry';
        // Format: Note[v:Velocity, d:Duration]
        // Duration is in seconds, maybe round to 2 decimals
        const duration = note.duration ? note.duration.toFixed(2) : '?';
        entry.textContent = `${note.name}[v:${note.velocity}, d:${duration}s]`;

        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }

    logControlChange(cc) {
        const log = document.getElementById('test-log');
        if (!log) return;

        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.style.color = '#ffcc00'; // Yellow for CC
        entry.textContent = `CC[c:${cc.controller}, v:${cc.value}]`;

        log.appendChild(entry);
        log.scrollTop = log.scrollHeight;
    }
}
