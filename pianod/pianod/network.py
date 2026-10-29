"""The Pi's WiFi, through NetworkManager: what it's on, what's around, joining one, and its log.

From hotpi's system.py. The panel is the only screen the Pi has, so it has to be able to put the
Pi on a network and, when that fails, show why: the log lives in RAM (journald is volatile on the
read-only root), but while the Pi is up the panel can read it.
"""

from __future__ import annotations

import re
import socket
import subprocess


def run(args, timeout=20) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def status() -> dict:
    """{"wifi": {"device", "state", "ssid"} or None, "ip", "hostname"}."""
    rc, out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    wifi = next((l.split(":") for l in out.splitlines() if ":wifi:" in l), None) if rc == 0 else None
    info = {"wifi": None, "ip": None, "hostname": socket.gethostname()}
    if wifi:
        dev, _type, state, conn = (wifi + ["", "", "", ""])[:4]
        info["wifi"] = {"device": dev, "state": state, "ssid": conn or None}
        rc, out = run(["ip", "-4", "-o", "addr", "show", dev])
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
        info["ip"] = m.group(1) if m else None
    elif rc != 0:
        info["error"] = out.splitlines()[-1] if out else "nmcli failed"
    return info


def scan() -> list[dict]:
    """The networks in range, strongest first: {"ssid", "signal", "secure"}."""
    rc, out = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                   "--rescan", "yes"], timeout=30)
    nets = {}
    for line in out.splitlines() if rc == 0 else []:
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 3 or not parts[0]:
            continue
        ssid = parts[0].replace("\\:", ":")
        sig = int(parts[1] or 0)
        if ssid not in nets or nets[ssid]["signal"] < sig:
            nets[ssid] = {"ssid": ssid, "signal": sig, "secure": parts[2] not in ("", "--")}
    return sorted(nets.values(), key=lambda n: -n["signal"])[:12]


def join(ssid: str, psk: str | None) -> tuple[bool, str]:
    """Join and remember (on the data partition) a network. If that fails on a secured one, try
    once more with management-frame protection off: the Pi 4's brcmfmac often can't join
    WPA2/WPA3 transition networks (phone hotspots) with it on."""
    rc, out = run(["nmcli", "device", "wifi", "connect", ssid] + (["password", psk] if psk else []),
                  timeout=45)
    if rc == 0 or not psk:
        return rc == 0, _last(out, "joined" if rc == 0 else "failed")
    first = _last(out, "failed")
    run(["nmcli", "connection", "delete", "id", ssid])
    rc, out = run(["nmcli", "connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid,
                   "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk, "wifi-sec.pmf", "1"])
    if rc == 0:
        rc, out = run(["nmcli", "connection", "up", "id", ssid], timeout=45)
        if rc == 0:
            return True, "joined (with PMF off)"
        run(["nmcli", "connection", "delete", "id", ssid])
    return False, first


def log(lines: int = 40) -> list[str]:
    """NetworkManager's and wpa_supplicant's last lines, oldest first, without the timestamps'
    date and the host name."""
    rc, out = run(["journalctl", "-u", "NetworkManager", "-u", "wpa_supplicant", "-n", str(lines),
                   "-o", "short", "--no-pager", "-q"])
    if rc != 0:
        return [_last(out, "no log")]
    # "Oct 29 20:41:07 pianopi NetworkManager[612]: <info>  [1761...] device ..." -> "20:41:07 ..."
    tidy = []
    for line in out.splitlines():
        m = re.match(r"\w{3} +\d+ (\d\d:\d\d:\d\d) \S+ ([^:\[]+)(?:\[\d+\])?: (.*)", line)
        if m:
            msg = re.sub(r"^<\w+>\s+\[[\d.]+\]\s*", "", m.group(3))
            tidy.append(f"{m.group(1)} {'wpa' if 'wpa' in m.group(2) else 'NM'}: {msg}")
        else:
            tidy.append(line)
    return tidy


def _last(out: str, default: str) -> str:
    return out.splitlines()[-1] if out else default
