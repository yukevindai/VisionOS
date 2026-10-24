#!/usr/bin/env python3
"""Choose and boot an already-installed iPhone simulator runtime/device."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def run(*command: str) -> str:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    return completed.stdout


def runtime_version(runtime_id: str) -> tuple[int, ...]:
    match = re.search(r"iOS[- ](\d+(?:[-.]\d+)*)$", runtime_id, re.IGNORECASE)
    if not match:
        return ()
    return tuple(int(part) for part in re.split(r"[-.]", match.group(1)))


def available_iphones() -> list[tuple[tuple[int, ...], str, str, str, str]]:
    payload = json.loads(run("xcrun", "simctl", "list", "devices", "available", "-j"))
    candidates: list[tuple[tuple[int, ...], str, str, str, str]] = []
    for runtime_id, devices in payload.get("devices", {}).items():
        version = runtime_version(runtime_id)
        if not version:
            continue
        for device in devices:
            name = str(device.get("name", ""))
            udid = str(device.get("udid", ""))
            if device.get("isAvailable", True) and name.startswith("iPhone") and udid:
                candidates.append((version, name.casefold(), udid, str(device.get("state", "")), name))
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot", action="store_true", help="Boot the selected device and wait until ready")
    parser.add_argument("--github-output", type=Path, help="Write the selected UDID to a GitHub Actions output file")
    args = parser.parse_args()

    candidates = available_iphones()
    if not candidates:
        print("No available iPhone simulator with an installed iOS runtime was found.", file=sys.stderr)
        return 1

    # Prefer the newest installed runtime, then a stable model-name ordering.
    version = max(candidate[0] for candidate in candidates)
    choices = sorted((candidate for candidate in candidates if candidate[0] == version), key=lambda item: item[1])
    _, _, udid, state, name = choices[0]

    if args.boot and state != "Booted":
        subprocess.run(["xcrun", "simctl", "boot", udid], check=True)
    if args.boot:
        subprocess.run(["xcrun", "simctl", "bootstatus", udid, "-b"], check=True)

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"udid={udid}\n")
    print(f"Selected {name} ({udid}) on installed iOS runtime {'.'.join(map(str, version))}")
    if args.github_output:
        print(f"udid={udid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
