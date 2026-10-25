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
