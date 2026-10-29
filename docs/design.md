# PianoPi design

A Raspberry Pi 4 permanently cabled to the Yamaha player piano (1980s, USB-MIDI DIN interface),
with an Adafruit PyBadge LC on its USB port as the control panel on the music desk. Modelled on
[hotpi](https://github.com/richmartin/hotpi) — read `hotpi/docs/PyBadge_Pi_Learnings.md` before
touching the badge or the image; this repo copies those patterns deliberately.

## Goals

1. **The piano plays with no server and no internet.** The Pi holds a full mirror of the MIDI
   library (~14 MB); browsing and playing never touch the network. murdoch adds extras — the web
   remote, library updates, smart-plug power — and losing it only removes those extras.
2. **Usable from the piano stool.** The badge browses the library, runs the transport, and is the
   whole interface for Performance mode. No phone needed.
3. **A loop station on an acoustic piano.** Performance mode records what is played on the keys
   and plays it back through the solenoids: record, loop, overdub, undo, save.
4. **The SD card survives.** Read-only root (overlayroot), data partition for everything that
   must persist, atomic writes, hardware watchdog. The Pi is NOT behind the piano's smart plug.

## The pieces

| Piece | Where | Job |
|---|---|---|
| `pianod/` | the Pi, systemd | Source of truth: the player, the looper, the library mirror, the queue |
| `badge/` | PyBadge LC | Shows what pianod pushes; sends it requests |
| `image/` | built on a laptop | The Pi's SD card, first boot ready |
| `regenerate_library.py` | murdoch | The librarian: builds the site + library.json from the gdrive source tree |

One protocol everywhere: newline-delimited JSON, requests with an `id`, replies echoing it, and
pushed `{"type": "state", ...}` once a second and on every change. The badge speaks it over the
usb_cdc data port; the `piano` CLI over a Unix socket; the web remote (later) will relay it from
murdoch over an outbound connection from the Pi.

## pianod

- **player.py** — plays an SMF to the piano. Pure `Cursor` over the event list; one worker
  thread; volume 1–11 as velocity scaling (11 = as written, 1 = silence), like the old browser
  player. Pause/stop send pedals-up + note-offs + All Notes Off (a "panic"), but only when
  something was actually sent (`MidiOut.dirty`), so idle transport fiddling stays silent.
- **looper.py** — Performance mode. `record()` → `close_loop()` (loop) or `finish_take()` (plays
  once); `toggle_overdub()` while playing commits a layer per pass of the loop, so undo peels
  overdubs back a pass at a time. Driven by `poll(now)` from the daemon's main loop every 5 ms;
  every method takes an explicit `now`, so the tests run without threads or sleeps.
- **library.py** — the mirror. `sync()` fetches library.json, validates it before replacing
  anything, then fetches whatever files are missing (they're immutable by id). Recordings live
  beside it and appear in the library as composer "Recordings".
- **midiio.py** — the only file that touches hardware. Port discovery by name substring
  (`PIANO_MIDI_OUT/IN`), and the **EchoGate**: if solenoid playback re-appears on the piano's
  MIDI OUT, recording during playback would capture every layer again, so the gate drops one
  incoming note-on per recently-sent note (window `PIANO_ECHO_MS`, 0 to disable).
- **daemon.py** — hotpi's heatd shape: `handle()` under one lock, `_serve_lines` per client,
  serial/socket server threads, systemd watchdog notify.

Configuration is environment variables, set in `pianod.service` if they ever need changing:
`PIANO_DATA`, `PIANO_SOCK`, `PIANO_SERIAL`, `PIANO_SERVER` (library source, default
https://home.fam-mart.in), `PIANO_MIDI_OUT`, `PIANO_MIDI_IN`, `PIANO_LATENCY_MS`, `PIANO_ECHO_MS`, `PIANO_VELOCITY_MAP`
(default on: every note-on goes through the inverse of the measured DKW10 curve; `0` sends
velocities as written).

## The panel (badge)

One meaning per button, for enclosure labels: **A** = do music (play/pause; in Performance:
record → close loop → overdub), **B** = stop (in Performance: end take / stop / play),
**START** = shuffle everything (in Performance: save the take, named on the on-screen keyboard),
**SELECT** = menu, d-pad = navigate (home: volume and prev/next). Layout maths live in
`layout.py`, pure Python, tested on the laptop — including that every hint fits in 26 characters.

## The piano: Yamaha Disklavier "Wagon Grand", control wagon DKW10

Wagon settings pianod needs (both on the wagon's front panel):

- **MIDI** button lit (red). Off, the wagon ignores MIDI IN and the piano plays nothing.
- **MIDIOUT=KBD OUT**, toggled by the **R** (part cancel) button while in MIDI mode. The other
  setting, DELAY OUT, sends MIDI IN back out after the wagon's playback delay — it looks like an
  echo but carries none of the keys, so recording silently captures nothing.
- **MIDIIN=REALTIME**. The other setting, DELAY IN, holds incoming MIDI ~515 ms before playing it
  (it's what DELAY OUT keeps external synths in step with); realtime plays in ~66 ms.

The REC button is for floppies and does nothing in MIDI mode. The wagon-to-piano cable has worked
loose once: if MIDI is lit and the piano stays silent, reseat it.

## Hardware questions (answer at the piano before trusting the defaults)

Run a laptop with a 20-line mido probe against the real piano:

1. **Echo** — does solenoid playback appear on MIDI OUT? **Yes** (2026-10-29, KBD OUT): every
   note and its note-off, velocity as the sensors measured it (sent 70, reported 72–75).
   `PIANO_ECHO_MS=250`.
2. **Latency** — MIDI-in-to-hammer delay; send a note, time its echo. With MIDIIN=REALTIME
   **62–70 ms** for note-ons, 45–51 ms for note-offs (DELAY IN: 513–520 ms). `PIANO_LATENCY_MS=65`,
   which the looper uses to play layers early so they land with live playing. Trust ears over
   this if layers drift.
3. **Dynamics floor** — the minimum solenoid velocity. Middle C, realtime, sent velocity → what
   the key sensors reported → peak loudness on the laptop mic:

   | sent | 100 | 80 | 60 | 45 | 35 | 30 | 25 | 20 | 15 | ≤12 |
   |---|---|---|---|---|---|---|---|---|---|---|
   | sensor | 91 | 78 | 64 | 53 | 46 | 43 | 40 | 36 | 33 | 33 |
   | dBFS | −14.9 | −18.6 | −22.3 | −25.8 | −28.6 | −30.1 | −31.7 | −33.7 | −35.3 | −35.5 |

   **Floor: sent 15** (sensor 33); everything below plays the same. The range is compressed —
   sent 15–100 lands as 33–91 — so replaying a take with its recorded velocities comes out
   louder in quiet passages, every pass. Hence the inverse map (send the velocity whose sensor
   reading matches the one recorded): `midiio.VelocityMap`, applied to every note sent.
   Your own playing (keys test): 30–86, sustain pedal reported on MIDI OUT.
4. **Boot time** — power-on to ready, for the smart-plug warm-up delay (server side, later).

## Later (server side, not in this repo yet)

- murdoch app at piano.fam-mart.in: web remote speaking the same protocol (the Pi dials out),
  recordings synced up into the library as performers, smart-plug power orchestration via the
  existing Mosquitto/zigbee2mqtt (plug on → warm-up wait → play; queue empty + no keys played +
  N minutes → panic, grace, plug off). Power metering is the "piano is actually on" signal.
- If someone is playing the keys (MIDI IN outside Performance mode), don't start remote playback
  and don't power off — `Pianod.last_input` already tracks this.
