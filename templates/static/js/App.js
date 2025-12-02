
import { SplendidGrandPiano } from 'https://unpkg.com/smplr/dist/index.mjs';

// High-quality sampled piano using smplr and Salamander Grand Piano
class SoundFontOutput {
    constructor() {
        this.context = new (window.AudioContext || window.webkitAudioContext)();
        this.piano = new SplendidGrandPiano(this.context);
        this.activeNotes = new Map(); // Store stop functions
        console.log("Initializing Salamander Grand Piano...");

        // Preload some samples (optional but good for responsiveness)
        // The library handles loading on demand, but we can trigger it early.
    }

    sendNoteOn(note, { velocity = 100 } = {}) {
        // smplr expects velocity 0-127
        // note can be "C4", etc.

        // Stop existing note if playing (re-trigger)
        this.sendNoteOff(note);

        const stopFn = this.piano.start({
            note: note,
            velocity: velocity
        });

        this.activeNotes.set(note, stopFn);
    }

    sendNoteOff(note) {
        const stopFn = this.activeNotes.get(note);
        if (stopFn) {
            stopFn(); // This triggers the release sample/envelope
            this.activeNotes.delete(note);
        }
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
                    this.selectedOutput = new SoundFontOutput();
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
        this.outputSelect.innerHTML = '';

        // 1. Always add the Software Synthesizer option
        const softwareOption = document.createElement("option");
        softwareOption.value = "testsynth";
        softwareOption.textContent = "Salamander Grand Piano (Software)";
        this.outputSelect.appendChild(softwareOption);

        // 2. Add External Devices Group
        if (WebMidi.outputs.length > 0) {
            const group = document.createElement("optgroup");
            group.label = "External MIDI Devices";

            WebMidi.outputs.forEach(output => {
                const option = document.createElement("option");
                option.value = output.id;
                option.textContent = output.name;
                group.appendChild(option);
            });
            this.outputSelect.appendChild(group);
        }

        // 3. Auto-select logic
        // If we have a previously selected output and it still exists, keep it.
        // Otherwise, if there are external devices, pick the last one.
        // Otherwise, default to Software Synth.

        let targetId = "testsynth"; // Default

        if (WebMidi.outputs.length > 0) {
            // Prefer the last external device if available
            targetId = WebMidi.outputs[WebMidi.outputs.length - 1].id;
        }

        // If the user had already selected something, try to preserve it
        if (this.selectedOutput && this.selectedOutput.id) {
            // Check if it's still in the list (or is the synth)
            if (this.selectedOutput instanceof SoundFontOutput) {
                targetId = "testsynth";
            } else {
                const exists = WebMidi.outputs.find(o => o.id === this.selectedOutput.id);
                if (exists) {
                    targetId = this.selectedOutput.id;
                }
            }
        }

        this.outputSelect.value = targetId;

        // Trigger the change event to update the player

        if (targetId === "testsynth") {
            if (!(this.selectedOutput instanceof SoundFontOutput)) {
                this.selectedOutput = new SoundFontOutput();
                this.player = new MidiPlayer(this.selectedOutput);
                console.log("Switched to Software Synthesizer");
            }
        } else {
            const device = WebMidi.getOutputById(targetId);
            if (device && (!this.selectedOutput || this.selectedOutput.id !== device.id)) {
                this.selectedOutput = device;
                this.player = new MidiPlayer(this.selectedOutput);
                console.log("Switched to External Device:", device.name);
            }
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
