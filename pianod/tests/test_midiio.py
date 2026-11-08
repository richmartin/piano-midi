from pianod.midiio import CollectOut, EchoGate


def test_panic_covers_pedals_sounding_notes_and_all_notes_off():
    out = CollectOut()
    out.note_on(60, 90)
    out.note_on(64, 90)
    out.panic()
    assert ("cc", 64, 0) in out.sent and ("cc", 67, 0) in out.sent
    assert ("off", 60) in out.sent and ("off", 64) in out.sent
    assert out.sent[-1] == ("cc", 123, 0)
    assert not out.active


class TestEchoGate:
    def test_sent_note_coming_back_is_an_echo(self):
        g = EchoGate(window=0.25, clock=lambda: 0.0)
        g.sent(60, now=1.0)
        assert g.is_echo("on", 60, now=1.1)
        assert g.is_echo("off", 60, now=1.3)         # its note-off too, even past the window

    def test_a_note_outside_the_window_is_the_player(self):
        g = EchoGate(window=0.25, clock=lambda: 0.0)
        g.sent(60, now=1.0)
        assert not g.is_echo("on", 60, now=1.4)

    def test_one_echo_per_send(self):
        """We send C once; the piano echoes it once; the player also plays C right after.
        Only the first incoming C is swallowed."""
        g = EchoGate(window=0.25, clock=lambda: 0.0)
        g.sent(60, now=1.0)
        assert g.is_echo("on", 60, now=1.05)
        assert not g.is_echo("on", 60, now=1.1)

    def test_other_notes_pass(self):
        g = EchoGate(window=0.25, clock=lambda: 0.0)
        g.sent(60, now=1.0)
        assert not g.is_echo("on", 62, now=1.05)
        assert not g.is_echo("off", 62, now=1.1)

    def test_window_zero_disables_the_gate(self):
        g = EchoGate(window=0, clock=lambda: 0.0)
        g.sent(60, now=1.0)
        assert not g.is_echo("on", 60, now=1.0)


def test_the_panels_own_usb_midi_port_is_never_taken_for_the_piano(monkeypatch):
    import sys
    import types

    opened = []
    fake = types.SimpleNamespace(
        get_output_names=lambda: ["PyBadge:PyBadge CircuitPython usb_midi. 20:0", "Midi Through:Midi Through Port-0 14:0",
                                  "UM-ONE:UM-ONE MIDI 1 24:0"],
        get_input_names=lambda: ["PyBadge:PyBadge CircuitPython usb_midi. 20:0", "UM-ONE:UM-ONE MIDI 1 24:0"],
        open_output=lambda name: opened.append(("out", name)) or object(),
        open_input=lambda name: opened.append(("in", name)) or object())
    monkeypatch.setitem(sys.modules, "mido", fake)
    from pianod import midiio
    midiio.open_ports()
    assert ("out", "UM-ONE:UM-ONE MIDI 1 24:0") in opened
    assert not any("PyBadge" in n for _, n in opened)
