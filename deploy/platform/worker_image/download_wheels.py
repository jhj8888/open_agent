"""Connected preparation only: official pip target selection with lockfile hashes.

The target marker environment is captured from the pinned Linux base container.
Do not use this downloader at image build time or in the deployed application.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from packaging.requirements import Requirement


def select(requirements: str, environment: dict) -> tuple[str, list[str]]:
    required = {
        "implementation_name",
        "implementation_version",
        "os_name",
        "platform_machine",
        "platform_release",
        "platform_system",
        "platform_version",
        "python_full_version",
        "platform_python_implementation",
        "python_version",
        "sys_platform",
    }
    if (
        set(environment) != required
        or environment["sys_platform"] != "linux"
        or environment["platform_machine"] != "x86_64"
    ):
        raise ValueError("Incomplete or unsupported target environment")
    output, names, pending = [], [], ""
    for raw in requirements.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending += line.removesuffix("\\").strip() + " "
        if line.endswith("\\"):
            continue
        pieces = re.split(r"\s+--hash=", pending.strip())
        request = Requirement(pieces[0])
        specs = list(request.specifier)
        if (
            request.url
            or len(specs) != 1
            or specs[0].operator != "=="
            or "*" in specs[0].version
        ):
            raise ValueError("Only exact exported requirements are supported")
        hashes = pieces[1:]
        if not hashes or any(
            not re.fullmatch(r"sha256:[a-f0-9]{64}", value) for value in hashes
        ):
            raise ValueError("Every requirement must preserve its SHA256 hashes")
        if request.marker is None or request.marker.evaluate(environment=environment):
            request.marker = None
            name = re.sub(r"[-_.]+", "-", request.name).lower()
            if name in names:
                raise ValueError("Ambiguous target dependency versions")
            names.append(name)
            output.append(
                str(request)
                + " \\\n    "
                + " \\\n    ".join("--hash=" + value for value in hashes)
            )
        pending = ""
    if pending.strip() or not names:
        raise ValueError("Incomplete requirements export")
    return "\n".join(output) + "\n", names


def main(requirements: Path, metadata: Path, destination: Path):
    if destination.is_symlink():
        raise ValueError("Linked wheel destination")
    target = json.loads(metadata.read_text())
    if (
        target["libc"][0] != "glibc"
        or target["environment"]["implementation_name"] != "cpython"
    ):
        raise ValueError("Unsupported runtime image")
    major, minor = map(int, target["libc"][1].split("."))
    if major != 2 or not 17 <= minor <= 50:
        raise ValueError("Unsupported glibc version")
    source = requirements.read_bytes()
    selected, names = select(source.decode(), target["environment"])
    destination.mkdir(parents=True, exist_ok=True)
    selection = destination / "selected-requirements.txt"
    selection.write_bytes(selected.encode())
    args = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--no-deps",
        "--require-hashes",
        "--index-url",
        "https://pypi.org/simple",
        "--python-version",
        target["environment"]["python_full_version"],
        "--implementation",
        "cp",
        "--abi",
        "cp" + target["environment"]["python_version"].replace(".", ""),
        "--abi",
        "abi3",
        "--abi",
        "none",
        "--dest",
        str(destination),
        "-r",
        str(selection),
        "--retries",
        "2",
        "--timeout",
        "30",
    ]
    platforms = [
        "manylinux_2_" + str(version) + "_x86_64" for version in range(minor, 4, -1)
    ]
    platforms += [
        "manylinux2014_x86_64",
        "manylinux2010_x86_64",
        "manylinux1_x86_64",
        "linux_x86_64",
    ]
    for platform in platforms:
        args += ["--platform", platform]
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("PIP_")
    }
    environment.update(
        PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1"
    )
    with (destination / "download.log").open("wb") as log:
        result = subprocess.run(
            args, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=900
        )
    if result.returncode:
        raise RuntimeError("Target wheel preparation failed; inspect download.log")
    report = {
        "base_image": target["base_image"],
        "target_environment": target["environment"],
        "platforms": platforms,
        "requirements_sha256": hashlib.sha256(source).hexdigest(),
        "selection_sha256": hashlib.sha256(selected.encode()).hexdigest(),
        "selected_packages": names,
        "pip_version": subprocess.check_output(
            [sys.executable, "-m", "pip", "--version"], text=True
        ).strip(),
        "downloaded_wheels": len(list(destination.glob("*.whl"))),
    }
    (destination / "selection.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"selected": len(names), "downloaded_wheels": report["downloaded_wheels"]}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    main(args.requirements, args.metadata, args.destination)
