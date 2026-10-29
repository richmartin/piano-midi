from pianod import network


class FakeNM:
    """Answers nmcli/ip/journalctl like a Pi 4 would; records what was asked."""

    def __init__(self, join_ok=True, pmf_ok=True):
        self.calls = []
        self.join_ok, self.pmf_ok = join_ok, pmf_ok

    def __call__(self, args, timeout=20):
        self.calls.append(args)
        if args[:2] == ["nmcli", "-t"] and "device" in args and "wifi" not in args:
            return 0, "wlan0:wifi:connected:Excession\nlo:loopback:connected (externally):lo"
        if args[0] == "ip":
            return 0, "3: wlan0    inet 10.151.255.42/24 brd 10.151.255.255 scope global wlan0"
        if "list" in args:
            return 0, "Excession:70:WPA2 WPA3\nExcessionR:55:WPA2\nExcessionR:80:WPA2\nCafe\\:Free:30:\n:20:WPA2"
        if args[1:4] == ["device", "wifi", "connect"]:
            return (0, "Device 'wlan0' successfully activated") if self.join_ok else \
                (4, "Error: Connection activation failed: Secrets were required, but not provided.")
        if args[1:3] == ["connection", "up"]:
            return (0, "Connection successfully activated") if self.pmf_ok else (4, "Error: timeout")
        if args[0] == "journalctl":
            return 0, ("Oct 29 20:41:07 pianopi NetworkManager[612]: <info>  [1761766867.1234] "
                       "device (wlan0): state change: config -> need-auth\n"
                       "Oct 29 20:41:08 pianopi wpa_supplicant[580]: wlan0: CTRL-EVENT-ASSOC-REJECT status_code=16")
        return 0, ""


def test_status(monkeypatch):
    monkeypatch.setattr(network, "run", FakeNM())
    st = network.status()
    assert st["wifi"] == {"device": "wlan0", "state": "connected", "ssid": "Excession"}
    assert st["ip"] == "10.151.255.42"


def test_scan_keeps_the_strongest_of_each_and_unescapes(monkeypatch):
    monkeypatch.setattr(network, "run", FakeNM())
    nets = network.scan()
    assert [n["ssid"] for n in nets] == ["ExcessionR", "Excession", "Cafe:Free"]
    assert nets[0]["signal"] == 80 and nets[2]["secure"] is False


def test_join_retries_with_pmf_off_when_the_plain_join_fails(monkeypatch):
    nm = FakeNM(join_ok=False)
    monkeypatch.setattr(network, "run", nm)
    ok, msg = network.join("Excession", "hollywood")
    assert ok and "PMF off" in msg
    added = next(c for c in nm.calls if c[1:3] == ["connection", "add"])
    assert added[added.index("wifi-sec.pmf") + 1] == "1"


def test_a_join_that_fails_both_ways_reports_the_first_error_and_leaves_nothing(monkeypatch):
    nm = FakeNM(join_ok=False, pmf_ok=False)
    monkeypatch.setattr(network, "run", nm)
    ok, msg = network.join("Excession", "wrong")
    assert not ok and "Secrets were required" in msg
    assert nm.calls[-1][1:3] == ["connection", "delete"]


def test_an_open_network_gets_no_second_try(monkeypatch):
    nm = FakeNM(join_ok=False)
    monkeypatch.setattr(network, "run", nm)
    assert network.join("Cafe", None)[0] is False
    assert len(nm.calls) == 1


def test_log_is_tidied_for_a_small_screen(monkeypatch):
    monkeypatch.setattr(network, "run", FakeNM())
    assert network.log() == ["20:41:07 NM: device (wlan0): state change: config -> need-auth",
                             "20:41:08 wpa: wlan0: CTRL-EVENT-ASSOC-REJECT status_code=16"]
