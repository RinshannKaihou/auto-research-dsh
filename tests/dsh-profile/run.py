#!/usr/bin/env python3
"""Boot the installed, unmodified base+web profile in a new disposable DSH home.

No network provider or user configuration is loaded. Native tools/processes do
run. Results are evidence; this probe does NOT certify all six migration gates.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsh-root", type=Path, required=True)
    parser.add_argument(
        "--keep-web",
        action="store_true",
        help="Keep restart instance in foreground for browser inspection",
    )
    args = parser.parse_args()
    dsh = args.dsh_root.resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    output = Path(tempfile.mkdtemp(prefix="ari-native-profile-")).resolve()
    home = output / "home"
    home.mkdir()
    profile = home / "profiles" / "web"
    (profile / "node_modules").mkdir(parents=True)
    (profile / "node_modules" / "auto-research-v5").symlink_to(
        repo / "apps/dsh/plugin", target_is_directory=True
    )
    overlay = output / "overlay.json"
    overlay.write_text(
        json.dumps(
            [
                {
                    "insert": [
                        {
                            "id": "ari-profile-probe",
                            "name": str(repo / "tests/dsh-profile/probe.mjs"),
                        },
                        {
                            "id": "auto-research-v5",
                            "name": "auto-research-v5",
                            "config": {
                                "python": sys.executable,
                                "pythonModulePath": str(repo / "src"),
                                "registryPath": str(output / "registry.sqlite3"),
                            },
                        },
                    ]
                }
            ]
        )
    )
    child = None
    try:
        for phase in ("initial", "restart"):
            report = output / f"{phase}.json"
            env = {
                "PATH": os.environ["PATH"],
                "DSH_HOME": str(home),
                "ARI_DSH_ROOT": str(dsh),
                "ARI_PROBE_OUTPUT": str(report),
                "ARI_PROBE_PHASE": phase,
                "DSH_TELEMETRY_DISABLED": "1",
            }
            with (output / f"{phase}.log").open("wb") as log:
                child = subprocess.Popen(
                    [
                        shutil.which("node"),
                        str(dsh / "lib/bin.js"),
                        "--profile",
                        "web",
                        "--patch",
                        str(overlay),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "0",
                        "--no-open",
                    ],
                    cwd=output,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
            deadline = time.monotonic() + 45
            while not report.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if not report.exists():
                raise RuntimeError(f"{phase} did not complete; inspect {output}/{phase}.log")
            value = json.loads(report.read_text())
            if value.get("error"):
                raise RuntimeError(f'{phase} failed: {value["error"]}')
            if phase == "restart" and args.keep_web:
                (output / "web.pid").write_text(str(child.pid))
                print(
                    json.dumps(
                        {
                            "output": str(output),
                            "pid": child.pid,
                            "browser_log": str(output / "restart.log"),
                        }
                    ),
                    flush=True,
                )
                child.wait()
                child = None
                break
            child.terminate()
            child.wait(timeout=10)
            child = None
        print(output)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
