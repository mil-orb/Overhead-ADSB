"""Wi-Fi scan and join. Uses NetworkManager (nmcli) on the Pi, a stand-in list everywhere else.

All calls block, so the settings screen runs them on a worker thread.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass

_SPLIT = re.compile(r"(?<!\\):")


@dataclass
class Net:
    ssid: str
    signal: int = 0        # 0..100
    secure: bool = True
    in_use: bool = False
    saved: bool = False


def parse_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t` output. Colons and backslashes inside values are escaped."""
    return [f.replace("\\:", ":").replace("\\\\", "\\") for f in _SPLIT.split(line.rstrip("\n"))]


def parse_scan(text: str, saved: set) -> list[Net]:
    best: dict = {}
    for line in text.splitlines():
        f = parse_terse(line)
        if len(f) < 4 or not f[1] or f[1].startswith("-") or len(f[1]) > 64 or not f[1].isprintable():
            continue                      # hidden networks have an empty SSID; odd names are not offered
        try:
            sig = int(f[2])
        except ValueError:
            sig = 0
        n = Net(f[1], sig, f[3] not in ("", "--"), f[0].strip() == "*", f[1] in saved)
        old = best.get(n.ssid)
        if not old or n.in_use or (n.signal > old.signal and not old.in_use):
            best[n.ssid] = n
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal))


class NmWifi:
    available = True

    def _run(self, args, timeout=25):
        return subprocess.run(["nmcli"] + args, capture_output=True, text=True, timeout=timeout)

    def _saved(self) -> set:
        try:
            out = self._run(["-t", "-f", "NAME,TYPE", "connection", "show"]).stdout
        except Exception:
            return set()
        return {f[0] for f in map(parse_terse, out.splitlines()) if len(f) >= 2 and "wireless" in f[1]}

    def current(self) -> str:
        """SSID in use right now, or an empty string. Quick: does not trigger a scan."""
        try:
            out = self._run(["-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no"], 8).stdout
        except Exception:
            return ""
        for f in map(parse_terse, out.splitlines()):
            if len(f) >= 2 and f[0] == "yes":
                return f[1]
        return ""

    def scan(self) -> list[Net]:
        r = self._run(["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "scan failed")
        return parse_scan(r.stdout, self._saved())

    def connect(self, ssid: str, password: str | None):
        """Join a network and make it the one the Pi prefers on boot. Returns (ok, message)."""
        if not ssid or ssid.startswith("-") or len(ssid) > 64 or (password is not None and not 8 <= len(password) <= 63):
            return False, "WRONG PASSWORD" if ssid and password is not None else "COULD NOT CONNECT"
        try:
            if password:
                r = self._run(["device", "wifi", "connect", ssid, "password", password], 50)
            elif ssid in self._saved():
                r = self._run(["connection", "up", "id", ssid], 50)
            else:
                r = self._run(["device", "wifi", "connect", ssid], 50)
        except subprocess.TimeoutExpired:
            return False, "TIMED OUT"
        except Exception:
            return False, "COULD NOT RUN NMCLI"
        if r.returncode != 0:
            err = (r.stderr or r.stdout).lower()
            if "secrets" in err or "password" in err or "802-11-wireless-security" in err:
                if password:
                    try:                  # don't leave a profile with a bad password behind
                        self._run(["connection", "delete", "id", ssid], 10)
                    except Exception:
                        pass
                return False, "WRONG PASSWORD"
            if "not authorized" in err or "permission" in err:
                return False, "NOT ALLOWED. SEE README"
            return False, "COULD NOT CONNECT"
        try:
            self._run(["connection", "modify", "id", ssid, "connection.autoconnect", "yes",
                       "connection.autoconnect-priority", "10"], 10)
        except Exception:
            pass
        return True, "CONNECTED"


class MockWifi:
    """Fake networks so the settings screen can be tried on a PC. Any 8+ character password works."""
    available = True

    def __init__(self):
        self.current_ = "HOME-5G"
        self.saved = {"HOME-5G"}

    def current(self) -> str:
        return self.current_

    def scan(self) -> list[Net]:
        time.sleep(1.2)
        raw = [("HOME-5G", 88, True), ("HOME-2G", 71, True), ("Workshop", 64, True),
               ("xfinitywifi", 52, False), ("NETGEAR-Guest", 40, False), ("Neighbor's Wi-Fi: 2.4", 33, True),
               ("DIRECT-roku-871", 21, True)]
        nets = [Net(s, sig, sec, s == self.current_, s in self.saved) for s, sig, sec in raw]
        return sorted(nets, key=lambda n: (not n.in_use, -n.signal))

    def connect(self, ssid, password):
        time.sleep(1.6)
        if password is not None and len(password) < 8:
            return False, "WRONG PASSWORD"
        self.current_ = ssid
        self.saved.add(ssid)
        return True, "CONNECTED"


class NoWifi:
    """A Linux machine without NetworkManager. Says so instead of showing made-up networks."""
    available = False

    def current(self) -> str:
        return ""

    def scan(self) -> list[Net]:
        raise RuntimeError("nmcli not found")

    def connect(self, ssid, password):
        return False, "WI-FI SETTINGS NEED NETWORKMANAGER"


def get_wifi(real=True):
    """The Wi-Fi backend for this machine. With real=False (screenshots, tests) it is always
    the stand-in, so a developer's own networks never end up in a picture or get joined."""
    if not real:
        return MockWifi()
    if shutil.which("nmcli"):
        return NmWifi()
    import sys
    return NoWifi() if sys.platform.startswith("linux") else MockWifi()
