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
    "m_calibrate": "Calibrate the piano",
    "m_calibrate_nomic": "Calibrate (needs a mic)",
    "m_network": "Network (WiFi)",
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

    # calibration
    "cal_when": "Calibrate: start when?",
    "cal_no_mic": "Plug in a mic",
    "cal_now": "Now",
    "cal_in": "In {n} minutes",
    "cal_title": "CALIBRATING",
    "cal_waiting": "STARTS IN",
    "cal_preflight": "CHECKING",
    "cal_measuring": "KEY {k}",
    "cal_paused": "PAUSED",
    "cal_keys": "{n} of {of} keys",
    "cal_eta": "about {m} min left",
    "cal_leave": "Leave the room quiet",
    "cal_cancel_q": "Stop calibrating?",
    "c_go_on": "Keep going",
    "c_stop": "Stop it",
    "cal_done": "Calibrated",
    "cal_failed": "Calibration failed",
    "cal_cancelled": "Calibration stopped",
    "hint_cal": "B cancel",

    # sync
    "syncing": "Updating the library...",
    "synced": "Library updated",
    "synced_body": "{works} pieces ({fetched} new)",

    # network
    "net_title": "Network",
    "net_checking": "Checking the network...",
    "wifi": "WiFi: {s}",
    "not_connected": "not connected",
    "net_state": "State: {s}",
    "ip": "IP:   {s}",
    "join": "Join a network...",
    "net_log": "Show the WiFi log",
    "net_reading": "Reading the log...",
    "refresh": "Refresh",
    "scanning": "Looking for networks...",
    "no_nets": "No networks found",
    "no_nets_body": "Try again in a moment.",
    "choose_net": "Choose a network",
    "password": "Password: {s}",
    "joining": "Joining (up to a minute)",
    "ok_wifi": "Connected",

    # info
    "info_title": "About",
    "i_link": "Pi link: {s}",
    "i_ok": "ok",
    "i_lost": "LOST",
    "i_midi": "Piano MIDI: {s}",
    "i_works": "Library: {n} pieces",
    "i_mode": "Mode: {s}",
    "i_mic": "Mic: {s}",
    "i_none": "none working",

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
    "hint_kb_sym": "A type B del ST abc/ABC/#!",
}


def t(key, **kw):
    s = T.get(key, key)
    return s.format(**kw) if kw else s
