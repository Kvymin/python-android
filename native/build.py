import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

import yaml

from verify import verify_wheel


DIRECTORY = Path(__file__).resolve().parent


def run(*command, cwd=None):
    subprocess.run(command, cwd=cwd, check=True)


def download(url, destination, expected):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        urllib.request.urlretrieve(url, destination)
    actual = hashlib.sha256(destination.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch: {destination}")


def extract_source(archive, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if not (destination / member.name).resolve().is_relative_to(destination.resolve()) or member.issym() or member.islnk():
                raise ValueError(f"Invalid source path: {member.name}")
        source.extractall(destination)


def prepare_numpy_recipe(package, source, work, chaquopy):
    upstream = chaquopy / "server/pypi/packages/numpy"
    metadata = yaml.safe_load((upstream / "meta.yaml").read_text())
    if metadata["package"] != {"name": "numpy", "version": package["version"]}:
        raise ValueError("NumPy recipe does not match sources.json")
    metadata["source"] = {"path": str(source)}
    metadata["build"]["number"] = 2
    metadata["requirements"]["host"].insert(0, "python")
    recipe = work / "recipes/numpy"
    shutil.copytree(upstream / "patches", recipe / "patches", dirs_exist_ok=True)
    (recipe / "meta.yaml").write_text(yaml.safe_dump(metadata))
    return recipe


def download_host_wheels(abi, pypi):
    for name in ["libffi", "openblas", "libgfortran"]:
        package = "chaquopy-" + name
        filename = abi[name]
        download(f"https://chaquo.com/pypi-13.1/{package}/{filename}", pypi / "dist" / package / filename, abi[name + "Sha256"])


def prepare_recipe(name, package, work, chaquopy):
    archive = work / "downloads" / package["url"].rsplit("/", 1)[1]
    download(package["url"], archive, package["sha256"])
    extract_source(archive, work / "sources")
    source = work / "sources" / package.get("directory", f"{name}-{package['version']}")
    if name == "numpy":
        return prepare_numpy_recipe(package, source, work, chaquopy)
    if name in ["chaquopy-libxml2", "chaquopy-libxslt"]:
        run("patch", "-p1", "-i", str(DIRECTORY / (name.removeprefix("chaquopy-") + ".patch")), cwd=source)
    requirements = {"host": []}
    build = {"number": 4 if name.startswith("chaquopy-") else 2}
    if name == "cryptography":
        cargo = source / "Cargo.toml"
        original = cargo.read_text()
        before = 'features = ["abi3", "abi3t"]'
        if original.count(before) != 1:
            raise ValueError("Unexpected PyO3 feature declaration")
        cargo.write_text(original.replace(before, 'features = ["abi3", "abi3t", "abi3-py310"]'))
        requirements["host"] = ["python", "openssl"]
        requirements["build"] = ["rust", "maturin 1.14.1", "cffi 2.0.0", "setuptools 80.9.0"]
        build["script_env"] = ["CRYPTOGRAPHY_BUILD_OPENSSL_NO_LEGACY=1"]
    elif name == "cffi":
        requirements["host"] = ["python", "chaquopy-libffi 3.3"]
        requirements["build"] = ["setuptools 80.9.0"]
    elif name == "chaquopy-libxslt":
        requirements["host"] = ["chaquopy-libxml2 2.9.8"]
    elif name == "lxml":
        requirements["host"] = ["python", "chaquopy-libxml2 2.9.8", "chaquopy-libxslt 1.1.32"]
        requirements["build"] = ["setuptools 80.9.0", "Cython 3.0.11"]
    elif name == "pycryptodome":
        requirements["host"] = ["python"]
        requirements["build"] = ["setuptools 80.9.0"]
    recipe = work / "recipes" / name
    recipe.mkdir(parents=True, exist_ok=True)
    if name.startswith("chaquopy-"):
        script = recipe / "build.sh"
        shutil.copyfile(chaquopy / "server/pypi/packages" / name / "build.sh", script)
        if name == "chaquopy-libxml2":
            extra = json.loads((DIRECTORY / "sources.json").read_text())["buildInputs"]["xz"]
            extra_archive = work / "downloads" / extra["file"]
            download(extra["url"], extra_archive, extra["sha256"])
            extract_source(extra_archive, work / "sources")
            build["script_env"] = ["TV_XML_XZ_SOURCE=" + str(work / "sources" / ("xz-" + extra["version"]))]
            script.write_text((DIRECTORY / "libxml2-build.sh").read_text())
        script.chmod(0o755)
    metadata = {"package": {"name": name, "version": package["version"]}, "source": {"path": str(source)}, "build": build, "requirements": requirements}
    (recipe / "meta.yaml").write_text(yaml.safe_dump(metadata))
    return recipe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--abi", required=True, choices=["arm64-v8a", "armeabi-v7a"])
    parser.add_argument("--chaquopy", required=True, type=Path)
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("Chaquopy build-wheel requires Linux x86_64")
    lock = json.loads((DIRECTORY / "sources.json").read_text())
    chaquopy = args.chaquopy.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=chaquopy, text=True).strip()
    if revision != lock["chaquopy"]:
        raise ValueError("Chaquopy revision does not match sources.json")
    rust = subprocess.check_output(["rustc", "--version"], text=True).strip()
    if rust.split()[1] != lock["rust"]:
        raise ValueError("Rust version does not match sources.json")
    run("git", "apply", str(DIRECTORY / "chaquopy.patch"), cwd=chaquopy)
    pypi = chaquopy / "server/pypi"
    work = pypi / "build-tv"
    abi = lock["abis"][args.abi]
    target_name = f"target-{lock['target']}-{args.abi}.zip"
    target = chaquopy / "maven/com/chaquo/python/target" / lock["target"] / target_name
    download(f"https://chaquo.com/maven-central/com/chaquo/python/target/{lock['target']}/{target_name}", target, abi["targetSha256"])
    download_host_wheels(abi, pypi)
    output = DIRECTORY / "build/output"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for name in ["chaquopy-libxml2", "chaquopy-libxslt", "cffi", "cryptography", "pycryptodome", "lxml", "numpy"]:
        recipe = prepare_recipe(name, lock["packages"][name], work, chaquopy)
        run("python", str(pypi / "build-wheel.py"), "--python", "3.10", "--api-level", "24", "--abi", args.abi, str(recipe), cwd=pypi)
        wheels = list((pypi / "dist" / name).glob(f"*android_24_{args.abi.replace('-', '_')}.whl"))
        if len(wheels) != 1:
            raise ValueError(f"Expected one {name} wheel, found {len(wheels)}")
        result = verify_wheel(wheels[0], args.abi)
        shutil.copyfile(wheels[0], output / wheels[0].name)
        results.append(result)
    report = {"abi": args.abi, "inputs": lock, "rust": rust, "wheels": results}
    (output / f"provenance-{args.abi}.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
