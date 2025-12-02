
// Simple sinewave synthesizer for testing when I don't have my piano in my pocket.
class TestSynthOutput {
    constructor(context = null) {
        this.context = context || new (window.AudioContext || window.webkitAudioContext)();
        this.voices = new Map();
    }
    _noteToFreq(note) {
        const A4 = 440;
        const map = {
            C: -9, "C#": -8, Db: -8, D: -7, "D#": -6, Eb: -6, E: -5,
            F: -4, "F#": -3, Gb: -3, G: -2, "G#": -1, Ab: -1,
            A: 0, "A#": 1, Bb: 1, B: 2
        };
        const [, letter, accidental, octaveStr] = note.match(/^([A-G])(#|b)?(\d)$/);
        const semis = map[letter + (accidental || "")];
        const octave = parseInt(octaveStr);
        const n = semis + (octave - 4) * 12;
        return A4 * Math.pow(2, n / 12);
    }
    sendNoteOn(note, { velocity = 100 } = {}) {
        const freq = this._noteToFreq(note);
        const osc = this.context.createOscillator();
        const gain = this.context.createGain();
        osc.type = "sine";
        const vol = velocity / 127 * 0.3;
        gain.gain.setValueAtTime(vol, this.context.currentTime);
        osc.frequency.value = freq;
        osc.connect(gain).connect(this.context.destination);
        osc.start();
        this.voices.set(note, { osc, gain });
    }
    sendNoteOff(note) {
        const v = this.voices.get(note);
        if (!v) return;
        const now = this.context.currentTime;
        v.gain.gain.exponentialRampToValueAtTime(0.001, now + 0.1);
        v.osc.stop(now + 0.1);
        this.voices.delete(note);
    }
}


class MidiPlayer {
    constructor(output = null) {
        this.output = output;
        this.events = [];
        this.playing = false;
        this.playhead = 0;
        this.lookahead = 25;
        this.scheduleAhead = 100;
        this.currentlyPlaying = new Set();
        this.startTime = 0;
        this.nextEventIndex = 0;
    }

    async loadArrayBuffer(arrayBuffer) {
        const midi = new Midi(arrayBuffer);
        this.events = [];
        midi.tracks.forEach(track => {
            track.notes.forEach(note => {
                this.events.push({
                    type: "noteon",
                    time: note.time,
                    name: note.name,
                    velocity: Math.floor(note.velocity * 127)
                });
                this.events.push({
                    type: "noteoff",
                    time: note.time + note.duration,
                    name: note.name
                });
            });
        });
        this.events.sort((a, b) => a.time - b.time);
        this.playhead = 0;
        this.nextEventIndex = 0;
    }

    async loadUrl(url) {
        const response = await fetch(url);
        const data = await response.arrayBuffer();
        return await this.loadArrayBuffer(data);
    }

    play() {
        if (this.playing || !this.output) return;
        this.playing = true;
        this.startTime = performance.now() - this.playhead * 1000;
        this._scheduler();
    }

    pause() {
        if (!this.playing) return;
        this.playing = false;
        this.currentlyPlaying.forEach(n => this.output.sendNoteOff(n));
        this.currentlyPlaying.clear();
        this.playhead = (performance.now() - this.startTime) / 1000;
    }

    stop() {
        this.pause();
        this.jumpTo(0);
    }

    jumpTo(time) {
        this.currentlyPlaying.forEach(n => this.output.sendNoteOff(n));
        this.currentlyPlaying.clear();
        this.playhead = time;
        this.nextEventIndex = this.events.findIndex(e => e.time >= time);
        if (this.nextEventIndex < 0) this.nextEventIndex = this.events.length;
    }

    _scheduler() {
        if (!this.playing) return;
        const now = performance.now();
        const elapsed = (now - this.startTime) / 1000;
        while (
            this.nextEventIndex < this.events.length &&
            this.events[this.nextEventIndex].time < elapsed + this.scheduleAhead / 1000
        ) {
            const e = this.events[this.nextEventIndex];
            const delay = e.time * 1000 + this.startTime - now;
            if (e.type === "noteon") {
                this.currentlyPlaying.add(e.name);
                if (this.output.sendNoteOn) {
                    // this.output.sendNoteOn(e.name, { velocity: e.velocity });
                    this.output.sendNoteOn(e.name, { attack: e.velocity / 127 });
                }
            } else if (e.type === "noteoff") {
                this.currentlyPlaying.delete(e.name);
                if (this.output.sendNoteOff)
                    this.output.sendNoteOff(e.name);
            }
            this.nextEventIndex++;
        }
        if (this.nextEventIndex < this.events.length) {
            setTimeout(() => this._scheduler(), this.lookahead);
        } else {
            this.playing = false;
            document.dispatchEvent(new Event('midi:trackEnded'));
        }
    }
}

class MidiController {
    constructor(outputSelectElementId) {
        this.outputSelect = document.getElementById(outputSelectElementId);
        this.selectedOutput = null;
        this.player = null;
    }

    async init() {
        console.log("Initializing MidiController...");
        if (!this.outputSelect) {
            console.error(`MIDI output select element #${outputSelectElementId} not found.`);
            return;
        }

        try {
            await WebMidi.enable();
            console.log("WebMidi enabled.");
            this.populateDevices();

            // Listen for device changes
            WebMidi.addListener("connected", () => this.populateDevices());
            WebMidi.addListener("disconnected", () => this.populateDevices());

            // Listen for user selection
            this.outputSelect.addEventListener("change", () => {
                if (this.outputSelect.value == "testsynth") {
                    this.selectedOutput = new TestSynthOutput();
                } else {
                    this.selectedOutput = WebMidi.getOutputById(this.outputSelect.value);
                }
                console.log("MIDI Output changed to:", this.selectedOutput?.name);
                this.player.stop();
                this.player = new MidiPlayer(this.selectedOutput);
            });

        } catch (err) {
            console.error("Could not enable WebMidi.", err);
            alert("Could not access MIDI devices. Please ensure your browser supports Web MIDI and you grant permission.");
        }
        if (this.selectedOutput) {
            this.player = new MidiPlayer(this.selectedOutput);
        }
    }

    populateDevices() {
        console.log("Populating MIDI output devices...");
        // Clear existing options
        this.outputSelect.innerHTML = '<option value="">Select MIDI Output...</option>';

        if (WebMidi.outputs.length === 0) {
            const option = document.createElement("option");
            option.value = "testsynth";
            option.textContent = "Test Synthesizer (no MIDI device)";
            this.outputSelect.appendChild(option);
        } else {
            WebMidi.outputs.forEach(output => {
                const option = document.createElement("option");
                option.value = output.id;
                option.textContent = output.name;
                this.outputSelect.appendChild(option);
            });
        }

        // Auto-select the last device
        if (WebMidi.outputs.length > 0) {
            this.selectedOutput = WebMidi.outputs[WebMidi.outputs.length - 1]
            this.outputSelect.value = this.selectedOutput.id;
            console.log("Auto-selected MIDI Output:", this.selectedOutput.name);
        }
    }
}

/**
 * App.js
 * 
 * Initializes the application.
 * - Creates the core player and playlist modules.
 * - Connects the page-specific buttons (if they exist) to the playlist.
 */
document.addEventListener('DOMContentLoaded', () => {
    console.log("App loading...");

    const midiController = new MidiController('midi-output-device');
    midiController.init();

    const playlist = new Playlist(midiController);
    playlist.bindEvents();

    const pagePlayButton = document.getElementById('page-play-button');
    const pageShuffleButton = document.getElementById('page-shuffle-button');

    // Check if PAGE_PLAYLIST_DATA exists (embedded in composer/file.html)
    if (typeof PAGE_PLAYLIST_DATA !== 'undefined') {
        if (pagePlayButton) {
            pagePlayButton.addEventListener('click', () => {
                console.log("Page Play button clicked");
                playlist.isShuffle = false; // Reset shuffle
                playlist.shuffleBtn.classList.remove('active');
                playlist.load(PAGE_PLAYLIST_DATA, true);
            });
        }

        if (pageShuffleButton) {
            pageShuffleButton.addEventListener('click', () => {
                console.log("Page Shuffle button clicked");
                playlist.isShuffle = true;
                playlist.shuffleBtn.classList.add('active');
                playlist.load(PAGE_PLAYLIST_DATA, true);
            });
        }
    }

    document.querySelectorAll('.track-item-clickable').forEach(item => {
        item.addEventListener('click', () => {
            const trackUrl = item.dataset.trackUrl;
            const trackTitle = item.dataset.trackTitle;
            console.log(`Track item clicked: ${trackTitle}`);
            if (trackUrl && trackTitle) {
                const singleTrackPlaylist = [
                    {
                        "title": trackTitle,
                        "url": trackUrl
                    }
                ];
                playlist.isShuffle = false; // turn off shuffle for single track
                playlist.shuffleBtn.classList.remove('active');
                playlist.load(singleTrackPlaylist, true); // Load and play
            }
        });
    });

    console.log("App ready.");
});
