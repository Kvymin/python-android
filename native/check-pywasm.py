import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


VERSIONS = ["1.0.8", "1.1.0", "1.1.1", "1.2.0", "1.2.1", "1.2.2", "1.2.3", "2.0.0", "2.0.1", "2.1.0", "2.1.1", "2.1.2", "2.1.3", "2.1.4", "2.1.5", "2.1.6", "2.2.0", "2.2.1", "2.2.2", "2.2.3"]
PROBE = r'''
import importlib.metadata
import json
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
phase = "import"
try:
    import pywasm
    assert importlib.metadata.version("pywasm") == sys.argv[2]
    phase = "execution"
    path = Path(sys.argv[1]) / "void.wasm"
    path.write_bytes(bytes.fromhex("0061736d010000000108026000006000017f020c0103656e76047a65726f000003020101070a0106616e7377657200010a080106001000412a0b"))
    calls = []
    if sys.argv[2] == "1.0.8":
        runtime = pywasm.load(str(path), {"env": {"zero": lambda context: calls.append(1)}})
        result = runtime.exec("answer", [])
        assert result == 42
    else:
        runtime = pywasm.Runtime()
        def callback(machine, arguments):
            calls.append(1)
            return []
        runtime.imports = {"env": {"zero": runtime.allocate_func_host(pywasm.FuncType([], []), callback)}}
        instance = runtime.instance_from_file(str(path))
        result = runtime.invocate(instance, "answer", [])
        assert result == [42]
    assert calls == [1]
    print(json.dumps({"supported": True, "phase": phase, "result": result, "callbacks": len(calls)}))
except Exception as error:
    print(json.dumps({"supported": False, "phase": phase, "error": type(error).__name__ + ": " + str(error)}))
'''


def check_version(version, directory):
    package = directory / version
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--no-deps", "--target", str(package), "pywasm==" + version], check=True)
    probe = subprocess.run([sys.executable, "-c", PROBE, str(package), version], text=True, capture_output=True, check=True)
    result = {"version": version, **json.loads(probe.stdout)}
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    versions = VERSIONS[-1:] if args.latest else VERSIONS
    with tempfile.TemporaryDirectory(prefix="pywasm-", dir=args.output.parent.resolve()) as directory:
        results = [check_version(version, Path(directory)) for version in versions]
    supported = [result["version"] for result in results if result["supported"]]
    report = {"python": sys.version, "latest_checked": versions[-1], "latest_usable": supported[-1] if supported else None, "results": results}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not supported:
        raise RuntimeError("No checked pywasm version passed the import and WASM callback probe")


if __name__ == "__main__":
    main()
