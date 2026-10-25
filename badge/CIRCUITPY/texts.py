# Everything the panel says, in one place, so wording can be fixed without touching logic.
# The screen is 26 characters wide in the small font; hints have to fit in that.
# One language (English) for now; the structure matches hotpi's so a second is easy.

T = {
    # home (library mode)
    "idle": "Nothing playing",
    "idle_hint": "SELECT: choose music",
    "playing": "PLAYING",
    "paused": "PAUSED",
    "next": "Next: {s}",
    "vol": "Vol {n}",
    "no_contact": ("No contact with the Pi", "Is pianod running?", "This panel still works;", "the piano won't hear it."),
    "no_piano": "piano not connected",
    "hint_home": "A play B stop SEL menu",

    # menu
    "menu": "Menu",
    "m_browse": "Browse composers",
    "m_recordings": "Recordings",
    "m_perform": "Performance mode",
    "m_shuffle": "Shuffle everything",
    "m_sync": "Update the library",
    "m_info": "About this panel",
    "hint_choose": "UP/DOWN  > choose  < back",
    "hint_back": "< back",

    # browsing
    "works_of": "{s}",
    "w_play": "Play this",
    "w_play_all": "Play all by composer",
    "w_shuffle_all": "Shuffle all by composer",
    "no_recordings": "No recordings yet",
    "no_recordings_body": "Performance mode saves takes here.",

    # performance mode
    "perf_title": "PERFORMANCE",
    "p_empty": "READY",
    "p_empty_body": "A starts recording",
    "p_recording": "RECORDING",
    "p_recording_body": "A loop it   B end take",
    "p_playing": "PLAYING",
    "p_playing_hint": "A overdub  B stop",
    "p_overdub": "OVERDUB",
    "p_overdub_hint": "A overdub off  B stop",
    "p_stopped": "STOPPED",
    "p_stopped_hint": "A record new  B play",
    "p_layers": "{n} layer{s}  {t}",
    "p_take": "take",
    "p_loop": "loop",
    "hint_perf": "UP undo DOWN redo ST save",
    "save_title": "Name this take",
    "cleared": "Cleared",
    "clear_q": "Clear the recording?",
    "c_keep": "Keep it",
    "c_clear": "Clear it",

    # sync
    "syncing": "Updating the library...",
    "synced": "Library updated",
    "synced_body": "{works} pieces ({fetched} new)",

    # info
    "info_title": "About",
    "i_link": "Pi link: {s}",
    "i_ok": "ok",
    "i_lost": "LOST",
    "i_midi": "Piano MIDI: {s}",
    "i_works": "Library: {n} pieces",
    "i_mode": "Mode: {s}",

    # shared
    "ok": "Done",
    "saved": "Saved",
    "not_done": "That didn't work",
    "no_answer": "No answer from the Pi",
    "no_answer_body": "The link may be down. Try again in a moment.",
    "sorry": "Something went wrong",
    "sorry_body": "Back to the start. Details are on the console.",
    "restart": "Restarting...",
    "hint_kb": "A type B delete ST abc/ABC",
    "hint_kb2": "d-pad move  OK when done",
}


def t(key, **kw):
    s = T.get(key, key)
    return s.format(**kw) if kw else s
