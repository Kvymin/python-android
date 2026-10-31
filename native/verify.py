import argparse
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

from elftools.elf.elffile import ELFFile


MACHINES = {"arm64-v8a": "EM_AARCH64", "armeabi-v7a": "EM_ARM"}
DEPENDENCIES = {"libpython3.10.so", "libcrypto_chaquopy.so", "libssl_chaquopy.so", "libcrypto_python.so", "libssl_python.so", "libsqlite3_python.so", "libffi.so", "libxml2.so", "libxslt.so", "libexslt.so", "libopenblas.so", "libz.so", "libc.so", "libm.so", "libdl.so", "liblog.so", "libandroid.so", "libc++_shared.so"}
NATIVE_COUNTS = {"cryptography": 1, "cffi": 1, "chaquopy_libxml2": 1, "chaquopy_libxslt": 2, "pycryptodome": 40, "lxml": 6, "numpy": 19}


def verify_library(name, data, abi):
    elf = ELFFile(io.BytesIO(data))
    if elf.header.e_machine != MACHINES[abi]:
        raise ValueError(f"Wrong ELF ABI: {name}")
    loads = [segment for segment in elf.iter_segments() if segment.header.p_type == "PT_LOAD"]
    if not loads or any(segment.header.p_align < 16384 or (segment.header.p_vaddr - segment.header.p_offset) % 16384 for segment in loads):
        raise ValueError(f"ELF does not support 16 KB pages: {name}")
    relro = [segment for segment in elf.iter_segments() if segment.header.p_type == "PT_GNU_RELRO"]
    if any((segment.header.p_vaddr + segment.header.p_memsz) % 16384 for segment in relro):
        raise ValueError(f"ELF RELRO does not support 16 KB pages: {name}")
    dynamic = elf.get_section_by_name(".dynamic")
    needed = [tag.needed for tag in dynamic.iter_tags("DT_NEEDED")]
    if not set(needed).issubset(DEPENDENCIES):
        raise ValueError(f"Unexpected ELF dependencies: {name}: {needed}")
    return {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "loadAlignment": [segment.header.p_align for segment in loads], "relroEnd": [segment.header.p_vaddr + segment.header.p_memsz for segment in relro], "needed": needed}


def verify_wheel(path, abi):
    with ZipFile(path) as wheel:
        if wheel.testzip() is not None:
            raise ValueError(f"Invalid wheel: {path}")
        libraries = [verify_library(name, wheel.read(name), abi) for name in wheel.namelist() if name.endswith(".so")]
    if len(libraries) != NATIVE_COUNTS[path.name.split("-", 1)[0]]:
        raise ValueError(f"Unexpected native library count: {path}")
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size, "libraries": libraries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--abi", required=True, choices=MACHINES)
    parser.add_argument("wheels", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps([verify_wheel(path, args.abi) for path in args.wheels], indent=2))


if __name__ == "__main__":
    main()
