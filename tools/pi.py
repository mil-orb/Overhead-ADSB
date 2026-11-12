"""Work with the Pi from your PC over SSH.

    python tools/pi.py deploy          copy the app to the Pi and restart it
    python tools/pi.py screen out.png  save a picture of what the Pi's screen is showing
    python tools/pi.py logs [lines]    the app's recent log
    python tools/pi.py status          is it running, how hot, how much memory
    python tools/pi.py restart         restart the app (also re-runs touch calibration if none is saved)
    python tools/pi.py test            run the tests on the Pi
    python tools/pi.py bench           stop the app, time the heavy paths, start it again

Where the Pi is comes from three environment variables:

    OVERHEAD_PI   user@host, default pi@overhead.local
    OVERHEAD_KEY  path to an SSH private key, default: whatever ssh would use
    OVERHEAD_DIR  folder on the Pi, default: the name of this repo's folder

deploy never copies config.json (the Pi keeps its own location and touch calibration),
the data cache, screenshots, or downloaded model sources. Nothing here needs a password:
the installer allows the app's restart without one. Anything else that needs root on the
Pi is for you to run there yourself.
"""
import io
import os
import re
import shlex
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.environ.get("OVERHEAD_PI", "pi@overhead.local")
KEY = os.environ.get("OVERHEAD_KEY", "")
DIR = os.environ.get("OVERHEAD_DIR", os.path.basename(ROOT))
SHIP = ["overhead", "models", "pi", "tools", "tests", "main.py", "requirements.txt", "README.md", "config.example.json"]
SKIP = ("__pycache__", ".pyc")

if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*", TARGET):
    sys.exit("OVERHEAD_PI should look like user@host, got %r" % TARGET)
if not re.fullmatch(r"[A-Za-z0-9_./-]+", DIR) or ".." in DIR:
    sys.exit("OVERHEAD_DIR should be a plain folder name, got %r" % DIR)


def ssh(command, data=None, capture=False):
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if KEY:
        args += ["-i", KEY, "-o", "IdentitiesOnly=yes"]
    run = subprocess.run(args + [TARGET, command], input=data, stdout=subprocess.PIPE if capture else None)
    if run.returncode != 0:
        sys.exit("ssh to %s failed (exit %d)" % (TARGET, run.returncode))
    return run.stdout


def deploy():
    buf = io.BytesIO()
    count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in SHIP:
            path = os.path.join(ROOT, name)
            if not os.path.exists(path):
                continue

            def keep(info):
                return None if info.name.endswith(SKIP) or "__pycache__" in info.name else info
            before = len(tar.getmembers())
            tar.add(path, arcname=name, filter=keep)
            count += len(tar.getmembers()) - before
    q = shlex.quote(DIR)
    ssh("mkdir -p %s && tar -xzf - -C %s && sudo -n systemctl restart overhead.service" % (q, q), buf.getvalue())
    print("copied %d files (%d KB) to %s:%s and restarted the app" % (count, len(buf.getvalue()) // 1024, TARGET, DIR))


def screen(out):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import fb2png
    raw = ssh("cat $(ls /dev/fb1 2>/dev/null || echo /dev/fb0)", capture=True)
    fb2png.convert(raw, out)
    print("saved", out)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    q = shlex.quote(DIR)
    if cmd == "deploy":
        deploy()
    elif cmd == "screen":
        screen(sys.argv[2] if len(sys.argv) > 2 else "pi-screen.png")
    elif cmd == "logs":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
        ssh("journalctl -u overhead -b --no-pager -o cat -n %d" % n)
    elif cmd == "status":
        ssh("systemctl is-active overhead.service; uptime -p; free -m | sed -n 2p; "
            "vcgencmd measure_temp 2>/dev/null; ps -o pcpu,rss,etime -C python3 | tail -1")
    elif cmd == "restart":
        ssh("sudo -n systemctl restart overhead.service && echo restarted")
    elif cmd == "test":
        ssh("cd %s && python3 -m tests" % q)
    elif cmd == "bench":
        ssh("cd %s && sudo -n systemctl stop overhead.service; python3 tools/bench.py; sudo -n systemctl start overhead.service" % q)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
