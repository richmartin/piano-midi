
import { SplendidGrandPiano } from 'https://unpkg.com/smplr/dist/index.mjs';

// High-quality sampled piano using smplr and Salamander Grand Piano
class SoundFontOutput {
    constructor() {
        this.context = new (window.AudioContext || window.webkitAudioContext)();
        this.piano = new SplendidGrandPiano(this.context);
        this.activeNotes = new Map(); // Store stop functions
        this.sustainedNotes = new Map(); // Store stop functions for sustained notes
        this.sustain = false;
        console.log("Initializing Salamander Grand Piano...");

        // Preload some samples (optional but good for responsiveness)
        // The library handles loading on demand, but we can trigger it early.
    }

    sendNoteOn(note, { velocity = 100, attack, time = 0, duration } = {}) {
        // smplr expects velocity 0-127
        // note can be "C4", etc.

        // Stop existing note if playing (re-trigger)
        this.sendNoteOff(note, { time });

        let finalVelocity = velocity;
        if (attack !== undefined && attack !== null) {
            finalVelocity = Math.floor(attack * 127);
        }

        const startTime = this.context.currentTime + time;

        const stopFn = this.piano.start({
            note: note,
            velocity: finalVelocity,
            time: startTime,
            duration: duration // If provided, smplr handles the stop
        });

        // Only track for manual stopping if no duration was provided
        if (!duration) {
            this.activeNotes.set(note, stopFn);
        }
    }

    sendNoteOff(note, { time = 0 } = {}) {
        const stopFn = this.activeNotes.get(note);
        if (stopFn) {
            if (this.sustain) {
                // Move to sustained notes
                this.sustainedNotes.set(note, stopFn);
                this.activeNotes.delete(note);
            } else {
                // Schedule stop if possible, otherwise rely on immediate stop
                // smplr stop functions usually stop immediately. 
                // We can use setTimeout for rough scheduling if needed, 
                // but if we used duration in NoteOn, this might not be called or needed.
                if (time > 0) {
                    setTimeout(() => stopFn(), time * 1000);
                } else {
                    stopFn();
                }
                this.activeNotes.delete(note);
            }
        }
    }

    sendControlChange(controller, value, { time = 0 } = {}) {
        // Schedule CC changes? 
        // For sustain, we can use setTimeout to update the state at the right time.
        setTimeout(() => {
            if (controller === 64) {
                if (value >= 64) {
                    this.sustain = true;
                } else {
                    this.sustain = false;
                    // Release all sustained notes
                    this.sustainedNotes.forEach(stopFn => stopFn());
                    this.sustainedNotes.clear();
                }
            }
        }, time * 1000);
    }

    stopAll() {
        this.sustain = false;
        this.activeNotes.forEach(stopFn => {
            try { stopFn(); } catch (e) { console.warn("Error stopping note:", e); }
        });
        this.activeNotes.clear();
        this.sustainedNotes.forEach(stopFn => {
            try { stopFn(); } catch (e) { console.warn("Error stopping sustained note:", e); }
        });
        this.sustainedNotes.clear();

        // If the piano has a global stop, use it (smplr doesn't seem to expose one directly on the instance easily, 
        // but we've handled individual notes).
        if (this.piano && this.piano.stop) {
            try { this.piano.stop(); } catch (e) { }
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
        this.volume = 11; // 1-11
    }

    setVolume(v) {
        this.volume = Math.max(1, Math.min(11, v));
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
                    velocity: Math.floor(note.velocity * 127),
                    duration: note.duration
                });
                this.events.push({
                    type: "noteoff",
                    time: note.time + note.duration,
                    name: note.name
                });
            });
            // Add Control Changes (specifically Sustain)
            if (track.controlChanges) {
                Object.keys(track.controlChanges).forEach(ccNum => {
                    if (parseInt(ccNum) === 64) {
                        track.controlChanges[ccNum].forEach(cc => {
                            this.events.push({
                                type: "controlchange",
                                time: cc.time,
                                controller: 64,
                                value: Math.floor(cc.value * 127)
                            });
                        });
                    }
                });
            }
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

        if (this.output) {
            if (typeof this.output.stopAll === 'function') {
                this.output.stopAll();
            } else {
                // External MIDI or generic output
                this.currentlyPlaying.forEach(n => {
                    if (this.output.sendNoteOff) this.output.sendNoteOff(n);
                });

                // Send All Notes Off (CC 123) and Sustain Off (CC 64)
                if (this.output.sendControlChange) {
                    try {
                        this.output.sendControlChange(64, 0); // Sustain Off
                        this.output.sendControlChange(123, 0); // All Notes Off
                    } catch (e) {
                        console.warn("Error sending MIDI panic:", e);
                    }
                }
            }
        }
        this.currentlyPlaying.clear();
        this.playhead = (performance.now() - this.startTime) / 1000;
    }

    stop() {
        this.pause();
        this.jumpTo(0);
    }

    jumpTo(time) {
        if (this.output) {
            if (typeof this.output.stopAll === 'function') {
                this.output.stopAll();
            } else {
                this.currentlyPlaying.forEach(n => {
                    if (this.output.sendNoteOff) this.output.sendNoteOff(n);
                });
                // Send All Notes Off (CC 123) and Sustain Off (CC 64)
                if (this.output.sendControlChange) {
                    try {
                        this.output.sendControlChange(64, 0); // Sustain Off
                        this.output.sendControlChange(123, 0); // All Notes Off
                    } catch (e) {
                        console.warn("Error sending MIDI panic:", e);
                    }
                }
            }
        }
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
            // Calculate precise delay in seconds
            const delayMs = e.time * 1000 + this.startTime - now;
            const delaySeconds = Math.max(0, delayMs / 1000);

            if (e.type === "noteon") {
                this.currentlyPlaying.add(e.name);

                // Apply Volume Scaling
                // Volume 1-11. Factor = (v-1)/10.
                // v=1 -> 0, v=11 -> 1.
                const volumeFactor = (this.volume - 1) / 10;
                const scaledVelocity = e.velocity * volumeFactor;

                // Support SoundFontOutput
                if (this.output.piano && this.output.sendNoteOn) {
                    this.output.sendNoteOn(e.name, {
                        attack: scaledVelocity / 127,
                        time: delaySeconds,
                        duration: e.duration
                    });
                }
                // Support WebMidi.js Output
                else if (this.output.playNote) {
                    const absoluteTime = WebMidi.time + delayMs;
                    this.output.playNote(e.name, {
                        channels: e.channel || 1,
                        velocity: scaledVelocity / 127,
                        time: absoluteTime,
                        duration: e.duration ? e.duration * 1000 : undefined
                    });
                }

                document.dispatchEvent(new CustomEvent('midi:noteOn', { detail: { ...e, velocity: scaledVelocity } }));
            } else if (e.type === "noteoff") {
                this.currentlyPlaying.delete(e.name);

                if (this.output.piano && this.output.sendNoteOff) {
                    this.output.sendNoteOff(e.name, { time: delaySeconds });
                }
                else if (this.output.stopNote) {
                    // WebMidi.js
                    const absoluteTime = WebMidi.time + delayMs;
                    this.output.stopNote(e.name, {
                        channels: e.channel || 1,
                        time: absoluteTime
                    });
                }

            } else if (e.type === "controlchange") {
                // SoundFontOutput
                if (this.output.piano && this.output.sendControlChange) {
                    this.output.sendControlChange(e.controller, e.value, { time: delaySeconds });
                }
                // WebMidi.js
                else if (this.output.sendControlChange) {
                    const absoluteTime = WebMidi.time + delayMs;
                    this.output.sendControlChange(e.controller, e.value, {
                        channels: e.channel || 1,
                        time: absoluteTime
                    });
                }

                document.dispatchEvent(new CustomEvent('midi:controlChange', { detail: e }));
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
        this.volumeSlider = document.getElementById('midi-volume');
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
                if (this.volumeSlider) {
                    this.player.setVolume(parseInt(this.volumeSlider.value));
                }
            });

            // Listen for volume changes
            if (this.volumeSlider) {
                this.volumeSlider.addEventListener('input', (e) => {
                    const v = parseInt(e.target.value);
                    if (this.player) {
                        this.player.setVolume(v);
                    }
                });
            }

        } catch (err) {
            console.error("Could not enable WebMidi.", err);
            alert("Could not access MIDI devices. Please ensure your browser supports Web MIDI and you grant permission.");
        }
        if (this.selectedOutput) {
            this.player = new MidiPlayer(this.selectedOutput);
            if (this.volumeSlider) {
                this.player.setVolume(parseInt(this.volumeSlider.value));
            }
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

import { Navigation } from './Navigation.js';
import { TestMode } from './TestMode.js';

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

    const testMode = new TestMode(midiController);
    testMode.init();

    const playlist = new Playlist(midiController);
    playlist.bindEvents();

    // Initialize Navigation
    const nav = new Navigation();

    // Function to bind page-specific events
    const bindPageEvents = () => {
        console.log("Binding page events...");
        const pagePlayButton = document.getElementById('page-play-button');
        const pageShuffleButton = document.getElementById('page-shuffle-button');
        const pageDataElement = document.getElementById('page-data');

        let pagePlaylistData = null;
        if (pageDataElement && pageDataElement.dataset.playlist) {
            try {
                pagePlaylistData = JSON.parse(pageDataElement.dataset.playlist);
            } catch (e) {
                console.error("Failed to parse page playlist data", e);
            }
        }

        if (pagePlaylistData) {
            if (pagePlayButton) {
                // Remove old listeners to avoid duplicates (though swapping DOM handles this mostly)
                // But since we are swapping innerHTML of main, the buttons are new elements.
                pagePlayButton.addEventListener('click', () => {
                    console.log("Page Play button clicked");
                    playlist.isShuffle = false; // Reset shuffle
                    playlist.shuffleBtn.classList.remove('active');
                    playlist.load(pagePlaylistData, true);
                });
            }

            if (pageShuffleButton) {
                pageShuffleButton.addEventListener('click', () => {
                    console.log("Page Shuffle button clicked");
                    playlist.isShuffle = true;
                    playlist.shuffleBtn.classList.add('active');
                    playlist.load(pagePlaylistData, true);
                });
            }
        }

        // Play Now Buttons
        document.querySelectorAll('.play-now-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation(); // Prevent bubbling
                const trackUrl = btn.dataset.trackUrl;
                const trackTitle = btn.dataset.trackTitle;
                console.log(`Play Now clicked: ${trackTitle}`);

                if (trackUrl && trackTitle) {
                    const track = { "title": trackTitle, "url": trackUrl };
                    playlist.isShuffle = false;
                    playlist.shuffleBtn.classList.remove('active');
                    playlist.playNext([track]);
                }
            });
        });

        // Add to Queue Buttons
        document.querySelectorAll('.add-queue-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const trackUrl = btn.dataset.trackUrl;
                const trackTitle = btn.dataset.trackTitle;
                console.log(`Add to Queue clicked: ${trackTitle}`);

                if (trackUrl && trackTitle) {
                    const track = { "title": trackTitle, "url": trackUrl };
                    playlist.addToQueue([track]);

                    // Visual feedback (optional)
                    const icon = btn.querySelector('i');
                    const originalClass = icon.className;
                    icon.className = 'fas fa-check';
                    setTimeout(() => icon.className = originalClass, 1000);
                }
            });
        });
    };

    // Bind events for the initial page load
    bindPageEvents();

    // Re-bind events when a new page is loaded via Navigation.js
    document.addEventListener('page:loaded', (e) => {
        console.log(`Page loaded via SPA: ${e.detail.url}`);
        bindPageEvents();
    });

    // --- Playlist UI Logic ---
    const nextUpTitle = document.getElementById('next-up-title');
    const playlistToggleBtn = document.getElementById('playlist-toggle-btn');
    const playlistModal = document.getElementById('playlist-modal');
    const playlistCloseBtn = document.getElementById('playlist-close-btn');
    const playlistItems = document.getElementById('playlist-items');

    // Toggle Playlist Modal
    if (playlistToggleBtn && playlistModal) {
        playlistToggleBtn.addEventListener('click', () => {
            playlistModal.classList.toggle('hidden');
            renderPlaylist(); // Re-render when opening
        });
    }

    if (playlistCloseBtn && playlistModal) {
        playlistCloseBtn.addEventListener('click', () => {
            playlistModal.classList.add('hidden');
        });
    }

    // Render Playlist
    const renderPlaylist = () => {
        if (!playlistItems) return;
        playlistItems.innerHTML = '';

        playlist.tracks.forEach((track, index) => {
            const li = document.createElement('li');
            li.className = `playlist-track ${index === playlist.currentIndex ? 'active' : ''}`;
            li.innerHTML = `
                <span class="playlist-track-index">${index + 1}</span>
                <span class="playlist-track-title">${track.title}</span>
            `;
            li.addEventListener('click', () => {
                playlist.jumpTo(index);
                renderPlaylist(); // Update active state
            });
            playlistItems.appendChild(li);
        });

        // Scroll active into view if needed
        const activeItem = playlistItems.querySelector('.active');
        if (activeItem) {
            activeItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    };

    // Listen for Playlist Updates
    document.addEventListener('playlist:updated', (e) => {
        console.log("Playlist updated, re-rendering UI...");
        renderPlaylist();
        updateNextUp(e.detail.tracks, e.detail.currentIndex);
    });

    document.addEventListener('playlist:trackChanged', (e) => {
        console.log("Track changed, updating UI...");
        renderPlaylist();

        // Update Next Up
        const nextTrack = e.detail.nextTrack;
        if (nextTrack) {
            nextUpTitle.textContent = nextTrack.title;
        } else {
            nextUpTitle.textContent = "---";
        }
    });

    const updateNextUp = (tracks, currentIndex) => {
        if (tracks && tracks.length > currentIndex + 1) {
            nextUpTitle.textContent = tracks[currentIndex + 1].title;
        } else {
            nextUpTitle.textContent = "---";
        }
    };

    console.log("App ready.");
});
