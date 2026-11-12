"""Run every test file: python -m tests

Each file is a plain script of assertions that prints "ok" at the end. They need pygame
and nothing else, use made-up traffic, and never touch the network or a real screen.
"""
import glob
import os
import subprocess
import sys
import time

here = os.path.dirname(os.path.abspath(__file__))
failed = []
for path in sorted(glob.glob(os.path.join(here, "test_*.py"))):
    name = os.path.basename(path)
    t0 = time.time()
    run = subprocess.run([sys.executable, path], capture_output=True, text=True, cwd=os.path.dirname(here))
    lines = [ln for ln in run.stdout.splitlines() if ln.strip()]
    ok = run.returncode == 0 and bool(lines) and lines[-1] == "ok"
    print("%-4s %-18s %5.1f s" % ("ok" if ok else "FAIL", name, time.time() - t0))
    if not ok:
        failed.append(name)
        print("\n".join("     " + ln for ln in (run.stdout + run.stderr).splitlines()[-25:]))
print()
print("all passed" if not failed else "failed: " + ", ".join(failed))
sys.exit(1 if failed else 0)
