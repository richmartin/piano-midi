# Piano

Everything for the Yamaha player piano: the MIDI library site, and PianoPi — the Raspberry Pi +
PyBadge control device that lives at the piano.

- **`regenerate_library.py`** builds the static library site (home.fam-mart.in) from the MIDI
  source tree on gdrive: browse by composer/performer, play in the browser over Web MIDI.
- **`pianod/`** runs on a Pi 4 cabled to the piano's MIDI interface: plays the library locally
  (it mirrors the whole thing), and is a loop station in Performance mode — record your playing,
  loop it back through the solenoids, overdub, save. `bin/piano` is the CLI.
- **`badge/`** is the control panel: an Adafruit PyBadge LC on the Pi's USB port, on the music
  desk. `badge/deploy.sh` ships it (directly, or through the Pi over SSH).
- **`image/`** builds the Pi's SD card on a laptop: read-only root, data partition, pianod as a
  service, first boot ready with no screen or keyboard. `sudo image/build.sh`.
- **`docs/design.md`** is the architecture and the open hardware questions.

Modelled on [hotpi](https://github.com/richmartin/hotpi); its
`docs/PyBadge_Pi_Learnings.md` is required reading for the badge and the image.

## Tests

```bash
pip install -r pianod/requirements.txt     # mido, pyserial, pytest (laptop only; the Pi uses apt)
python3 -m pytest pianod/tests badge/tests
```

The whole system minus the piano runs on a laptop: `PIANO_DATA=/tmp/piano PIANO_SOCK=/tmp/piano.sock
pianod/bin/pianod` with the badge plugged into the laptop, then `piano sync` and play — with
python-rtmidi installed and a USB-MIDI interface, straight into the real piano.

## Status

Built 2026-10-25, not yet run against the real piano. Before trusting the defaults, answer the
four hardware questions in docs/design.md (echo, latency, dynamics floor, boot time) with a
laptop at the piano; the image build is adapted from hotpi's but has not been run yet.
