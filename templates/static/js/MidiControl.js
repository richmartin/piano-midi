/**
 * MidiControl.js
 * 
 * Handles the "how" of playing MIDI.
 * - Initializes WebMidi.js [7, 8, 4, 5]
 * - Manages the MIDI output device selection 
 * - Instantiates MidiPlayerJS 
 * - Creates the "bridge" to send parser events to the selected output device. [6]
 */
class MidiPlayer {
    constructor(outputSelectElementId) {
        this.outputSelect = document.getElementById(outputSelectElementId);
        this.selectedOutput = null;
        this.player = null;
        
        // This event tells the Playlist manager that the track is done
        this.trackEndEvent = new Event('midi:trackEnded');
    }

    async init() {
        console.log("Initializing MidiControl...");
        if (!this.outputSelect) {
            console.error(`MIDI output select element #${outputSelectElementId} not found.`);
            return;
        }

        try {
            // Request MIDI access [8, 9]
            await WebMidi.enable();
            console.log("WebMidi enabled.");
            
            this.populateDevices();
            
            // Listen for device changes
            WebMidi.addListener("connected", () => this.populateDevices());
            WebMidi.addListener("disconnected", () => this.populateDevices());
            
            // Listen for user selection
            this.outputSelect.addEventListener("change", () => {
                this.selectedOutput = WebMidi.getOutputById(this.outputSelect.value);
                console.log("MIDI Output changed to:", this.selectedOutput?.name);
            });

        } catch (err) {
            console.error("Could not enable WebMidi.", err);
            alert("Could not access MIDI devices. Please ensure your browser supports Web MIDI and you grant permission.");
        }
        
        this.initPlayer();
    }
    
    populateDevices() {
        console.log("Populating MIDI output devices...");
        // Clear existing options
        this.outputSelect.innerHTML = '<option value="">Select MIDI Output...</option>';
        
        if (WebMidi.outputs.length === 0) {
            this.outputSelect.innerHTML = '<option value="">No MIDI devices found</option>';
            this.selectedOutput = null;
            return;
        }

        // Add all available outputs [5]
        WebMidi.outputs.forEach(output => {
            const option = document.createElement("option");
            option.value = output.id;
            option.textContent = output.name;
            this.outputSelect.appendChild(option);
        });
        
        // Try to auto-select the first device
        if (WebMidi.outputs.length > 0) {
            this.selectedOutput = WebMidi.outputs[WebMidi.outputs.length - 1]
            this.outputSelect.value = this.selectedOutput.id;
            console.log("Auto-selected MIDI Output:", this.selectedOutput.name);
        }
    }

    initPlayer() {
        console.log("Initializing MidiPlayerJS...");
        
        // --- THIS IS THE FIX ---
        // The global variable from the CDN is 'MidiPlayerJS', not 'MidiPlayer' 
        this.player = new Player((event) => {
            this.handleMidiEvent(event);
        });
        // --- END FIX ---

        // Listen for end of file event [3]
        this.player.on('endOfFile', () => {
            console.log("MidiPlayer: End of File.");
            this.stopAllNotes(); // Send all-notes-off just in case
            document.dispatchEvent(this.trackEndEvent);
        });
    }

    handleMidiEvent(event) {
        // If no piano is selected, do nothing.
        if (!this.selectedOutput) return;

        // Get the MIDI channel (1-16)
        const channel = this.selectedOutput.channels[event.channel];
        console.log(`MIDI Event: ${event.name} on channel ${event.channel}`);

        switch (event.name) {
            case 'Note on':
                // Send a 'noteon' message [5]
                channel.playNote(event.noteName, {
                    velocity: (event.velocity / 127) // WebMidi uses 0-1
                });
                break;
            case 'Note off':
                // Send a 'noteoff' message
                channel.stopNote(event.noteName);
                break;
            case 'Set tempo':
                this.player.setTempo(event.data);
                break;
            case 'Control Change':
                // Pass through control changes, e.g., sustain pedal (CC 64)
                channel.sendControlChange(event.number, event.value);
                break;
            // Add other cases as needed (e.g., 'Pitch Bend')
        }
    }
    
    loadAndPlay(midiFileUrl) {
        console.log(`Loading and playing: ${midiFileUrl}`);
        if (this.player.isPlaying()) {
            this.player.stop();
        }
        
        this.player.loadUrl(midiFileUrl).then(() => {
            this.player.play();
        }).catch(err => {
            console.error("Error loading MIDI file:", err);
        });
    }
    
    pause() {
        console.log("Pausing player.");
        this.player.pause();
        this.stopAllNotes(); // Prevent stuck notes
    }
    
    resume() {
        console.log("Resuming player.");
        this.player.play();
    }

    stop() {
        console.log("Stopping player.");
        this.player.stop();
        this.stopAllNotes();
    }
    
    stopAllNotes() {
        // Safety measure: send "all notes off" to all channels
        if (this.selectedOutput) {
            for (let i = 1; i <= 16; i++) {
                this.selectedOutput.channels[i].sendAllNotesOff();
            }
        }
    }
}
