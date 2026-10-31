import argparse
import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

from native.verify import MACHINES, packages_for_abi, verify_library, verify_wheel


ROOT = Path(__file__).resolve().parent


def read_report(path, abi, inputs):
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["abi"] != abi or report["inputs"] != inputs:
        raise ValueError(f"Unexpected build inputs: {path}")
    return report


def copy_asset(source, output, expected):
    if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise ValueError(f"SHA-256 mismatch: {source}")
    shutil.copyfile(source, output / source.name)


def verify_wheel_set(wheels, inputs, abi):
    expected = {(name.replace("-", "_"), package["version"]) for name, package in packages_for_abi(inputs, abi).items()}
    actual = {tuple(wheel["file"].split("-")[:2]) for wheel in wheels}
    if len(wheels) != len(expected) or actual != expected:
        raise ValueError(f"Incomplete or unexpected wheels: {abi}")


def verify_runtime(path, report, abi):
    with ZipFile(path) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != {library["path"] for library in report["libraries"]}:
            raise ValueError(f"Invalid native archive: {path}")
        for library in report["libraries"]:
            if verify_library(library["path"], archive.read(library["path"]), abi) != library:
                raise ValueError(f"Native provenance mismatch: {library['path']}")


def package(artifacts, output, producer):
    output.mkdir(parents=True, exist_ok=True)
    wheels = {"producer": producer, "abis": {}}
    runtime = {"producer": producer, "abis": {}}
    wheel_inputs = json.loads((ROOT / "native/sources.json").read_text(encoding="utf-8"))
    runtime_inputs = json.loads((ROOT / "native/runtime-sources.json").read_text(encoding="utf-8"))
    for abi in MACHINES:
        wheel_dir = artifacts / ("wheels-" + abi)
        report = read_report(wheel_dir / f"provenance-{abi}.json", abi, wheel_inputs)
        verify_wheel_set(report["wheels"], wheel_inputs, abi)
        for wheel in report["wheels"]:
            path = wheel_dir / wheel["file"]
            if path.name != wheel["file"] or verify_wheel(path, abi) != wheel:
                raise ValueError(f"Wheel provenance mismatch: {path}")
            copy_asset(path, output, wheel["sha256"])
        wheels["abis"][abi] = report
        runtime_dir = artifacts / ("runtime-" + abi)
        report = read_report(runtime_dir / f"runtime-provenance-{abi}.json", abi, runtime_inputs)
        path = runtime_dir / report["file"]
        if path.name != report["file"]:
            raise ValueError(f"Invalid native filename: {path}")
        verify_runtime(path, report, abi)
        copy_asset(path, output, report["sha256"])
        runtime["abis"][abi] = report
    for name, report in [("wheels-manifest.json", wheels), ("native-bundles-manifest.json", runtime)]:
        (output / name).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    producer = {"repository": args.repository, "commit": args.commit, "runId": args.run_id, "url": f"https://github.com/{args.repository}/actions/runs/{args.run_id}", "tag": args.tag}
    package(args.artifacts, args.output, producer)


if __name__ == "__main__":
    main()
