#!/usr/bin/env python3
"""The hardware probe: answers docs/design.md's four questions at the real piano.

Run on a laptop with the piano's USB-MIDI interface plugged in (needs mido + python-rtmidi):

    python3 pianod/tools/probe.py

It is interactive and harmless: a handful of single notes at moderate velocity, nothing held,
nothing loud. Have the piano switched on and in whatever its normal "play from MIDI" state is.

What it measures, and which pianod setting each one feeds:

  1. ECHO      - does solenoid playback come back on MIDI OUT?        -> PIANO_ECHO_MS (0 if not)
  2. LATENCY   - MIDI-in to key-sensor delay, if there is an echo     -> PIANO_LATENCY_MS
  3. DYNAMICS  - the quietest velocity the solenoids actually play    -> (a velocity map, later)
  4. KEYS      - velocity range of your own playing, soft to loud     -> (sanity check)
"""

import sys
import time

try:
    import mido
except ImportError:
    sys.exit("needs mido and python-rtmidi:  pip install mido python-rtmidi")

MIDDLE_C = 60


def pick_port(names, what):
    # rtmidi's own virtual port shows up once the output is open; it's never the piano
    names = [n for n in names
             if not any(s in n.lower() for s in ("through", "thru", "rtmidi"))]
    if not names:
        sys.exit(f"no MIDI {what} port found - is the interface plugged in?")
    if len(names) == 1:
        print(f"{what}: {names[0]}")
        return names[0]
    for i, n in enumerate(names):
        print(f"  {i}: {n}")
    return names[int(input(f"which {what} port? "))]


def drain(inport):
    while inport.poll() is not None:
        pass


def listen(inport, seconds):
    """Every (message, arrival time) for the next `seconds`."""
    out, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        msg = inport.poll()
        if msg is not None:
            out.append((msg, time.monotonic()))
        else:
            time.sleep(0.001)
    return out


def note(outport, pitch, velocity, hold=0.4):
    outport.send(mido.Message("note_on", note=pitch, velocity=velocity))
    sent = time.monotonic()
    time.sleep(hold)
    outport.send(mido.Message("note_off", note=pitch, velocity=0))
    return sent


def test_echo_and_latency(outport, inport):
    print("\n--- 1 & 2: echo and latency " + "-" * 40)
    print("Sending five notes; listening on MIDI IN. Don't touch the keys.")
    input("ready? [enter] ")
    drain(inport)
    delays = []
    for i, pitch in enumerate((60, 64, 67, 72, 55)):
        sent = note(outport, pitch, 70)
        for msg, arrived in listen(inport, 1.2):
            if msg.type == "note_on" and msg.velocity > 0 and msg.note == pitch:
                delays.append(arrived - sent)
                break
        time.sleep(0.3)
    if not delays:
        print("\nNO ECHO: playback does not appear on MIDI OUT.")
        print("  -> PIANO_ECHO_MS=0 (the gate is not needed)")
        print("  -> latency can't be measured this way; if loop layers sound late against live")
        print("     playing, set PIANO_LATENCY_MS by ear in ~20 ms steps.")
        return
    avg = sum(delays) / len(delays)
    print(f"\nECHO: {len(delays)}/5 notes came back, after " +
          ", ".join(f"{d * 1000:.0f}" for d in delays) + " ms")
    print(f"  -> PIANO_ECHO_MS={max(150, int(avg * 1000) + 150)}   (echo delay + margin)")
    print(f"  -> PIANO_LATENCY_MS={int(avg * 1000)}   (if the sensors fire as the hammers strike;")
    print("     trust your ears over this number when layers feel early or late)")


def test_dynamics(outport):
    print("\n--- 3: dynamics floor " + "-" * 46)
    print("The same note at falling velocities. Note the quietest one that actually sounds,")
    print("and whether the quiet ones differ from each other at all.")
    input("ready? [enter] ")
    for v in (100, 80, 60, 45, 35, 25, 18, 12, 8, 5, 2):
        print(f"  velocity {v:3}")
        note(outport, MIDDLE_C, v, hold=0.5)
        time.sleep(0.4)
    print("quietest audible velocity -> the floor; pianod can map below it later if needed.")


def test_keys(inport):
    print("\n--- 4: your own playing " + "-" * 44)
    print("Play for ~10 seconds: a few notes as softly as you can, a few as loudly as you dare,")
    print("and press the sustain pedal once.")
    input("ready? [enter] ")
    drain(inport)
    vels, pedal = [], False
    for msg, _ in listen(inport, 10):
        if msg.type == "note_on" and msg.velocity > 0:
            vels.append(msg.velocity)
        elif msg.type == "control_change" and msg.control == 64 and msg.value >= 64:
            pedal = True
    if vels:
        print(f"\n{len(vels)} notes, velocity {min(vels)}-{max(vels)}; "
              f"sustain pedal {'seen' if pedal else 'NOT seen'}")
        if not pedal:
            print("  (no CC64: check the pedal before trusting recordings)")
    else:
        print("\nNOTHING ARRIVED on MIDI IN - recording and the looper won't work until that's solved.")
        print("Check the IN/OUT plugs aren't swapped (the interface's IN goes to the piano's OUT).")


def main():
    print("PianoPi hardware probe (see docs/design.md)")
    outport = mido.open_output(pick_port(mido.get_output_names(), "output"))
    inport = mido.open_input(pick_port(mido.get_input_names(), "input"))
    print("\nFirst, the cable check: one note, middle C, right now.")
    note(outport, MIDDLE_C, 70)
    if input("did the piano play it? [y/n] ").strip().lower() != "y":
        sys.exit("fix that first: piano on, wagon MIDI button lit, wagon-to-piano cable seated\n"
                 "(and MIDIOUT=KBD OUT via R for the echo test - see docs/design.md).")
    test_echo_and_latency(outport, inport)
    test_dynamics(outport)
    test_keys(inport)
    print("\nDone. Put the two numbers into image/files/pianod.service as Environment= lines")
    print("(or export them when running pianod by hand), and note the dynamics floor in")
    print("docs/design.md.")


if __name__ == "__main__":
    main()
