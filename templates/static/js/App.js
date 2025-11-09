/**
 * App.js
 * 
 * Initializes the application.
 * - Creates the core player and playlist modules.
 * - Connects the page-specific buttons (if they exist) to the playlist.
 */
document.addEventListener('DOMContentLoaded', () => {
    console.log("App loading...");
    
    // 1. Initialize the core MIDI engine
    const midiControl = new MidiControl('midi-output-device');
    midiControl.init();

    // 2. Initialize the playlist manager
    const playlist = new Playlist(midiControl);
    playlist.bindEvents();

    // 3. Connect page-specific "Play All" and "Shuffle All" buttons
    const pagePlayButton = document.getElementById('page-play-button');
    const pageShuffleButton = document.getElementById('page-shuffle-button');

    // Check if PAGE_PLAYLIST_DATA exists (embedded in composer/file.html)
    if (typeof PAGE_PLAYLIST_DATA!== 'undefined') {
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

    // 4. --- THIS IS NEW: Add listeners for individual track clicks ---
    // This addresses your issue of clicking a track and having nothing happen.
    document.querySelectorAll('.track-item-clickable').forEach(item => {
        item.addEventListener('click', () => {
            const trackUrl = item.dataset.trackUrl;
            const trackTitle = item.dataset.trackTitle;
            
            console.log(`Track item clicked: ${trackTitle}`);
            
            if (trackUrl && trackTitle) {
                const singleTrackPlaylist = [trackUrl]; 
                playlist.isShuffle = false; // Always play single track
                playlist.shuffleBtn.classList.remove('active');
                playlist.load(singleTrackPlaylist, true); // Load and play
            }
        });
    });
    
    console.log("App ready.");
});
