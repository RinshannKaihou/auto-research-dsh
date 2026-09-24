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
import sqlite3
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsh-root", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plugin-root", type=Path)
    source.add_argument("--package", type=Path, help="Install this npm tarball into the isolated profile")
    parser.add_argument("--blank", action="store_true", help="Blank-session browser fixture; reject every model request")
    parser.add_argument("--sample-project", type=Path, help="Clone an existing sample ledger and frozen objects into the blank browser profile")
    parser.add_argument(
        "--keep-web",
        action="store_true",
        help="Keep restart instance in foreground for browser inspection",
    )
    args = parser.parse_args()
    if args.sample_project and not args.blank:
        parser.error("--sample-project requires --blank")
    dsh = args.dsh_root.resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    output = Path(tempfile.mkdtemp(prefix="ari-native-profile-")).resolve()
    sample_clone = None
    if args.sample_project:
        source_project = args.sample_project.resolve(strict=True)
        sample_clone = output / "sample-research"
        (sample_clone / ".research").mkdir(parents=True)
        # The plugin's session association is a ledger write. Never attach a
        # disposable browser session directly to the real research database.
        source_db = source_project / ".research" / "state.sqlite3"
        with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as original:
            with sqlite3.connect(sample_clone / ".research" / "state.sqlite3") as copy:
                original.backup(copy)
        shutil.copytree(source_project / ".research" / "objects", sample_clone / ".research" / "objects")
        presentation = source_project / "research.presentation.json"
        if presentation.is_file():
            shutil.copy2(presentation, sample_clone / presentation.name)
    home = output / "home"
    home.mkdir()
    profile = home / "profiles" / "web"
    if args.package:
        install_env = {**os.environ, "DSH_HOME": str(home), "DSH_TELEMETRY_DISABLED": "1"}
        with (output / "install.log").open("wb") as log:
            subprocess.run(
                [shutil.which("node"), str(dsh / "lib/bin.js"), "plugin", "--profile", "web", "add", str(args.package.resolve(strict=True)), "--offline"],
                cwd=output, env=install_env, stdout=log, stderr=log, check=True,
            )
        plugin_root = profile / "node_modules" / "auto-research-v5"
    else:
        (profile / "node_modules").mkdir(parents=True)
        plugin_root = args.plugin_root.resolve(strict=True)
        (profile / "node_modules" / "auto-research-v5").symlink_to(plugin_root, target_is_directory=True)
    overlay_entries = [
        {
            "id": "ari-profile-probe",
            "name": str(repo / "tests/dsh-profile" / ("blank-probe.mjs" if args.blank else "workflow-probe.mjs")),
        }
    ]
    if not args.package:
        overlay_entries.append(
            {
                "id": "auto-research-v5",
                "name": "auto-research-v5",
                "config": {
                    "python": sys.executable,
                    "pythonModulePath": str(plugin_root / "python"),
                    "registryPath": str(output / "registry.sqlite3"),
                },
            }
        )
    overlay = output / "overlay.json"
    overlay.write_text(
        json.dumps(
            [
                {
                    "insert": overlay_entries
                }
            ]
        )
    )
    child = None
    try:
        for phase in (("initial",) if args.blank else ("initial", "restart")):
            report = output / f"{phase}.json"
            env = {
                "PATH": os.environ["PATH"],
                "DSH_HOME": str(home),
                "ARI_DSH_ROOT": str(dsh),
                "ARI_PROBE_OUTPUT": str(report),
                "ARI_PROBE_PHASE": phase,
                "ARI_PLUGIN_PYTHON": str(plugin_root / "python"),
                "DSH_TELEMETRY_DISABLED": "1",
            }
            if sample_clone:
                env["ARI_SAMPLE_PROJECT"] = str(sample_clone)
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
            deadline = time.monotonic() + 60
            while not report.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if not report.exists():
                raise RuntimeError(f"{phase} did not complete; inspect {output}/{phase}.log")
            value = json.loads(report.read_text())
            if value.get("error"):
                raise RuntimeError(f'{phase} failed: {value["error"]}')
            if (phase == "restart" or args.blank) and args.keep_web:
                (output / "web.pid").write_text(str(child.pid))
                print(
                    json.dumps(
                        {
                            "output": str(output),
                            "pid": child.pid,
                            "browser_log": str(output / f"{phase}.log"),
                        }
                    ),
                    flush=True,
                )
                child.wait()
                child = None
                break
            if phase == "initial" and value.get("crash_ready"):
                # Deliberately bypass graceful DSH disposal so the restart probe
                # observes a real process crash with a still-owned native job.
                child.kill()
            else:
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
