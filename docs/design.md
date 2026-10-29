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
https://home.fam-mart.in), `PIANO_MIDI_OUT`, `PIANO_MIDI_IN`, `PIANO_ECHO_MS`. Everything about
how this particular piano responds is in `calibration.json` instead (see the playback model).

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
   **62–70 ms** for note-ons at velocity 70, 45–51 ms for note-offs (DELAY IN: 513–520 ms).
   Softer notes take longer — see below; it's per velocity and key in the calibration.
3. **Dynamics floor** — the minimum solenoid velocity. Middle C, realtime, sent velocity → what
   the key sensors reported → peak loudness on the laptop mic:

   | sent | 100 | 80 | 60 | 45 | 35 | 30 | 25 | 20 | 15 | ≤12 |
   |---|---|---|---|---|---|---|---|---|---|---|
   | sensor | 91 | 78 | 64 | 53 | 46 | 43 | 40 | 36 | 33 | 33 |
   | dBFS | −14.9 | −18.6 | −22.3 | −25.8 | −28.6 | −30.1 | −31.7 | −33.7 | −35.3 | −35.5 |

   **Floor: sent 15** (sensor 33); everything below plays the same. The range is compressed —
   sent 15–100 lands as 33–91 — so replaying a take with its recorded velocities comes out
   louder in quiet passages, every pass. Hence the velocity stage of the playback model.
   Your own playing (keys test): 30–86, sustain pedal reported on MIDI OUT.
4. **Boot time** — power-on to ready, for the smart-plug warm-up delay (server side, later).

## What goes wrong between a file and the strings (measured 2026-10-29)

Satie's Gnossienne No. 1 through pianod at volume 8, every note matched against the key sensors:
126 of 980 note-ons never sounded. Not a black-key problem (a sweep of all 88 keys at velocities
12–30, notes held 250 ms, sounded every one) but a **timing** one:

- **Short notes are cut off before the hammer lands.** A soft note takes ~145 ms from MIDI to
  strike (velocity 15; ~118 ms at 30, ~80 ms at 60). A note-off before then pulls the solenoid back
  and the note never sounds: held under 80 ms, 100% lost at velocity 15. The Gnossienne's grace
  notes are exactly this. Shortest hold that sounds, per key, single trial each:

  | velocity | min hold | strike |
  |---|---|---|
  | 15 | 100 ms (59 keys), 130 ms (28), 60 ms (1) | 124–184 ms |
  | 30 | 80 ms (73 keys), 60–100 ms (the rest) | 104–143 ms |
  | 60 | 40 ms (all 88; 20 ms never sounds) | 68–102 ms |

  Velocity dominates; the key barely matters. Min hold ≈ strike time − 40–50 ms (the hammer
  coasts once thrown).
- **Fast re-strikes of the same key** drop too: re-struck alone within 150 ms of its release, 51%.
- **The velocity map sank a quiet file onto the floor**: 74% of its notes went out at 15.
  The library comes from many sources (converted piano rolls, modern recordings), so its
  velocities don't mean any one thing — they need fitting per file, not one global map.
- **Soft notes land late.** Strike time spans ~70–185 ms with velocity, so uncompensated, the soft
  notes of a chord land up to ~75 ms after the loud ones.

## The playback model

Every note goes through two independent stages before it reaches the piano:

1. **What the music wants** — per file. Fit the file's own velocity spread onto the piano's usable
   range, then apply volume. Gives `iv`, the loudness wanted, in the piano's own sensor units.
   Performance takes skip the fit: they were recorded in those units.
2. **What this piano needs to produce it** — per key `k`, from the calibration:
   - **Velocity**: `ov = clamp(iv · sv(k) + mv(k), 1, 127)` — linear is a fair fit above the
     floor (a measured curve can replace it). Calibrated by loudness on the mic, not the sensors
     alone: a 40-year-old sensor can be off while its hammer is fine.
   - **Latency**: send the note-on `l_on(ov, k)` early so it strikes on time, and the note-off
     `l_off(k)` early (the dampers: ~45–50 ms). Pedals get their own `l(cc)`.
   - **Duration**: the hold actually sent is `id + l_on − l_off`, which already lengthens soft
     notes; then enforce `max(md(ov, k), …)` on it.
   - **Re-strike**: a key needs `mr(k)` after its note-off before it can sound again; if the next
     note-on comes sooner, shorten the previous note rather than lose the new one.
   - **Pedal**: with the sustain pedal down, soft notes strike much weaker, so a note that strikes
     under the pedal uses the key's pedal curve (`sv_p`, `mv_p`, floor `v_min_p`) when that asks
     for more than the plain one.

Because events now leave early by different amounts, the player looks ahead by the largest
latency (~200 ms) and sends in *send-time* order, not sound-time order. The calibration is one
file, `calibration.json` on the data partition; pianod loads it at start and after every
calibration. Without one, it falls back to the measured defaults above.

## Calibrate

A full calibration cycle run by the Pi itself — started from the badge (SELECT → Calibrate) or
`piano calibrate`, with an **optional delayed start** (default 10 minutes, counting down on the
badge, B cancels) so it can run with nobody home. Up to an hour; it's meant to be left alone.

- **Needs a microphone on the Pi** (the Pi 4's jack is output only: a USB mic, or a USB sound card
  with a mic in), fixed in one place under the lid, so calibrations are comparable over time. It
  uses the first capture device it finds, or `PIANO_MIC`, and records the mic's name in the
  calibration, so a change of mic isn't mistaken for every key drifting.
- **No mic, no Calibrate.** pianod takes half a second from the mic every 30 s and reports it in
  its state (`mic`: found and delivering sound, or why not); the badge greys the menu item out
  and says why, and pianod refuses `calibrate` without one.
- **Preflight**: a test note must come back from the key sensors (proves wagon MIDI on,
  MIDIOUT=KBD OUT, MIDIIN=REALTIME from the ~66 ms echo — DELAY IN shows as ~515 ms), and the
  mic must hear it above the room's noise floor. Fails with a message saying which setting.
  It runs **as soon as a calibration is asked for, before any countdown**, so a problem shows
  while whoever started it is still in the room — and again when the countdown ends.
- **Per key**, one note at a time, waiting for the damper and the room to go quiet between notes
  (the earlier laptop run failed exactly here: notes masked by the previous one ringing):
  velocity → mic loudness and sensor velocity (~8 points), `l_on` per velocity, `l_off`, `md`
  by searching the hold at a few velocities, `mr` by searching the re-strike gap.
- **Interruptions**: an unexpected key press (someone playing) or a loud room pauses it; it waits
  for quiet and carries on. Pianod refuses playback and Performance mode while calibrating;
  only cancel is accepted. Progress (key, stage, time left) is pushed like any other state.
- **First live run** (4 keys, laptop, 2026-10-29): ~21 s a key, so ~32 minutes for all 88.
  Strike 145 ms at velocity 12 down to 50 ms at 100; min hold 115–130 / 93 / 48 ms at 15 / 30
  / 60; re-strike gap only 40–45 ms. **Velocity saturates at 100**: 127 plays no louder, so the
  velocity fit ignores the flat top. On a laptop the first capture device may be an empty
  headset socket — `PIANO_MIC=default` there; preflight catches it.
- **Re-strikes are measured with the sustain pedal down, 3 tries out of 3.** The first full
  run (single tries, no pedal, 15 ms margin) gave F4 an mr of 38 ms, and the Gnossienne then
  lost 19 of 74 pedalled F4 re-strikes at exactly that gap. On F4 a 30 ms re-strike sounds 4/4
  without the pedal and 0/4 with it; 40 ms sounds 4/4 either way. Now F4 measures 77 ms
  (30 ms margin). That costs time: ~33 s a key, ~48 minutes for all 88 (~55 with the pedal curve).
- **With the pedal down, per key**: the curve again at 12 / 20 / 30 / 45 / 60, pedal held,
  sensor only (lifting the dampers is noisy). Near the floor a note sounds only sometimes, so from the bottom
  up each velocity is tried up to 3 times and counts only if every try sounds; above the first
  that does, one try each (~5 more minutes). Found because, after the second full calibration,
  the Gnossienne still lost 23 of 980 notes, all soft accompaniment. Notes fired together were
  the first suspect, but measured alone they cost 0–1 sensor units and never missed (192 notes in
  chords of 2–4). The pedal was it: in the Gnossienne, soft notes under the pedal missed 9%
  (without it, 1%). Measured on its own, C3 never sounds at 20–25 with the pedal down and
  only weakly at 30; the mic hears it 15–20 dB quieter (at 45 only ~3 dB); C4 at 20 is ~10 dB
  quieter. Other keys held down cost 2–3 sensor units. Why the pedal weakens the hammers isn't
  known: perhaps the pedal solenoid, held on, loads the supply they share.
  With the pedal curve the Gnossienne lost 4 of 980 (0.4%; was 23), all soft notes under the
  pedal on C3 / G#3 / C4; timing 10-90% within -13 to +38 ms. Soft notes under the pedal need
  +9 velocity (median over the keys), up to +24.
- **Results**: written atomically as a new `calibration.json`, the previous one kept, and a
  summary on the badge — including keys that changed noticeably since last time, which is how a
  failing solenoid or sensor gets noticed.

## Later (server side, not in this repo yet)

- murdoch app at piano.fam-mart.in: web remote speaking the same protocol (the Pi dials out),
  recordings synced up into the library as performers, smart-plug power orchestration via the
  existing Mosquitto/zigbee2mqtt (plug on → warm-up wait → play; queue empty + no keys played +
  N minutes → panic, grace, plug off). Power metering is the "piano is actually on" signal.
- If someone is playing the keys (MIDI IN outside Performance mode), don't start remote playback
  and don't power off — `Pianod.last_input` already tracks this.
