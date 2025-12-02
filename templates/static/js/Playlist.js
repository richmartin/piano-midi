/**
 * Playlist.js
 * 
 * Manages the "what" of playing music.
 * - Holds the queue of tracks
 * - Manages playback state (playing, paused, shuffle)
 * - Responds to UI controls (play, next, prev)
 * - Listens for 'midi:trackEnded' to advance to the next track.
 */
class Playlist {
    constructor(midiController) {
        this.midiController = midiController;
        this.tracks = []; // <--- THIS WAS THE BUG
        this.currentIndex = 0;
        this.isPlaying = false;
        this.isShuffle = false;

        // UI Elements
        this.playPauseBtn = document.getElementById('play-pause-button');
        this.nextBtn = document.getElementById('next-button');
        this.prevBtn = document.getElementById('prev-button');
        this.shuffleBtn = document.getElementById('shuffle-button');
        this.nowPlayingTitle = document.getElementById('now-playing-title');

        // --- THIS IS THE FIX ---
        // Create both icons and add them to the button
        this.playIcon = document.createElement('i');
        this.playIcon.classList.add('fas', 'fa-play');

        this.pauseIcon = document.createElement('i');
        this.pauseIcon.classList.add('fas', 'fa-pause');

        // Clear the button and add the new icons
        this.playPauseBtn.innerHTML = '';
        this.playPauseBtn.appendChild(this.playIcon);
        this.playPauseBtn.appendChild(this.pauseIcon);

        this.updatePlayButton(false); // Show play icon by default
        // --- END FIX ---

        // Listen for the track ended event from MidiControl
        document.addEventListener('midi:trackEnded', () => this.handleTrackEnd());
    }

    bindEvents() {
        this.playPauseBtn.addEventListener('click', () => this.togglePlayPause());
        this.nextBtn.addEventListener('click', () => this.next());
        this.prevBtn.addEventListener('click', () => this.previous());
        this.shuffleBtn.addEventListener('click', () => this.toggleShuffle());
    }

    jumpTo(index) {
        if (index >= 0 && index < this.tracks.length) {
            this.currentIndex = index;
            this.play();
        }
    }

    emitUpdate() {
        document.dispatchEvent(new CustomEvent('playlist:updated', {
            detail: { tracks: this.tracks, currentIndex: this.currentIndex }
        }));
    }

    emitTrackChanged() {
        document.dispatchEvent(new CustomEvent('playlist:trackChanged', {
            detail: { track: this.tracks[this.currentIndex], nextTrack: this.tracks[this.currentIndex + 1] }
        }));
    }

    load(tracks, playImmediately = true) {
        if (!tracks || tracks.length === 0) {
            console.warn("Load called with empty playlist.");
            return;
        }

        console.log(`Loading playlist with ${tracks.length} tracks.`);
        this.tracks = tracks;
        this.currentIndex = 0;
        this.emitUpdate();

        if (playImmediately) {
            if (this.isShuffle) {
                this.currentIndex = Math.floor(Math.random() * this.tracks.length);
            }
            this.play();
        }
    }

    addToQueue(tracks) {
        if (!tracks || tracks.length === 0) return;
        this.tracks.push(...tracks);
        console.log(`Added ${tracks.length} tracks to queue.`);
        this.emitUpdate();

        // If queue was empty and we weren't playing, start.
        if (!this.isPlaying && this.tracks.length === tracks.length) {
            this.play();
        } else {
            // Update next up if we are playing the last track
            if (this.currentIndex === this.tracks.length - tracks.length - 1) {
                this.emitTrackChanged();
            }
        }
    }

    playNext(tracks) {
        if (!tracks || tracks.length === 0) return;

        if (this.tracks.length === 0) {
            this.load(tracks, true);
        } else {
            // Insert after current index
            this.tracks.splice(this.currentIndex + 1, 0, ...tracks);
            this.emitUpdate();
            // Skip to it
            this.next();
        }
    }

    play() {
        if (this.tracks.length === 0) {
            console.warn("Play called but no tracks are loaded.");
            return;
        }

        this.isPlaying = true;
        this.updatePlayButton(true);
        this.emitTrackChanged();

        const track = this.tracks[this.currentIndex];
        this.nowPlayingTitle.textContent = track.title;

        this.midiController.player.loadUrl(track.url).then(() => {
            this.midiController.player.play();
        });
    }

    pause() {
        this.isPlaying = false;
        this.midiController.player.pause();
        this.updatePlayButton(false);
    }

    togglePlayPause() {
        if (this.isPlaying) {
            this.pause();
        } else {
            this.play();
        }
    }

    next() {
        if (this.tracks.length === 0) return;

        if (this.isShuffle) {
            this.currentIndex = Math.floor(Math.random() * this.tracks.length);
        } else {
            this.currentIndex = (this.currentIndex + 1) % this.tracks.length;
        }

        this.play();
    }

    previous() {
        if (this.tracks.length === 0) return;

        if (this.isShuffle) {
            this.currentIndex = Math.floor(Math.random() * this.tracks.length);
        } else {
            this.currentIndex = (this.currentIndex - 1 + this.tracks.length) % this.tracks.length;
        }

        this.play();
    }

    toggleShuffle() {
        this.isShuffle = !this.isShuffle;
        this.shuffleBtn.classList.toggle('active', this.isShuffle);
        console.log("Shuffle set to:", this.isShuffle);
    }

    handleTrackEnd() {
        if (this.isPlaying) {
            // Automatically play the next track
            this.next();
        }
    }

    updatePlayButton(isPlaying) {
        // --- THIS IS THE FIX ---
        if (isPlaying) {
            this.playIcon.style.display = 'none';
            this.pauseIcon.style.display = 'inline';
        } else {
            this.playIcon.style.display = 'inline';
            this.pauseIcon.style.display = 'none';
        }
        // --- END FIX ---
    }
}
