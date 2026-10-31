import io
import os
from pathlib import Path
import shlex
import subprocess

from elftools.elf.elffile import ELFFile


def read_interface(data):
    elf = ELFFile(io.BytesIO(data))
    dynamic = elf.get_section_by_name(".dynamic")
    soname = next(tag.soname for tag in dynamic.iter_tags("DT_SONAME"))
    definitions = elf.get_section_by_name(".gnu.version_d")
    versions = {}
    for definition, auxiliary in definitions.iter_versions():
        names = [entry.name for entry in auxiliary]
        if definition.entry["vd_ndx"] > 1:
            versions[definition.entry["vd_ndx"]] = names
    table = elf.get_section_by_name(".dynsym")
    version_table = elf.get_section_by_name(".gnu.version")
    symbols = {}
    for index, symbol in enumerate(table.iter_symbols()):
        version = version_table.get_symbol(index).entry["ndx"]
        if not isinstance(version, int):
            continue
        version_index = version & 0x7fff
        if version_index not in versions or symbol.entry["st_shndx"] == "SHN_UNDEF":
            continue
        name = versions[version_index][0]
        if symbol.name == name:
            continue
        if symbol.name in symbols:
            raise ValueError("Multiple symbol versions are not supported: " + symbol.name)
        record = {"version": name, "type": symbol.entry["st_info"]["type"], "binding": symbol.entry["st_info"]["bind"]}
        if record["type"] in ["STT_OBJECT", "STT_TLS"]:
            record["size"] = symbol.entry["st_size"]
        symbols[symbol.name] = record
    return {"soname": soname, "versions": {names[0]: names[1:] for names in versions.values()}, "symbols": symbols}


def write_version_script(profile, path):
    lines = []
    for index, (version, parents) in enumerate(profile["versions"].items()):
        if len(parents) > 1:
            raise ValueError("Multiple version parents are not supported: " + version)
        lines.append(version + " {")
        names = sorted(name for name, record in profile["symbols"].items() if record["version"] == version)
        if names:
            lines.append("  global:")
            lines.extend("    " + name + ";" for name in names)
        if index == 0:
            lines.extend(["  local:", "    *;"])
        lines.append("}" + (" " + parents[0] if parents else "") + ";")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def link_library(source, output, compiler, flags):
    profile = read_interface((source / "reference.so.3").read_bytes())
    version_script = source / "gfortran.map"
    write_version_script(profile, version_script)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = compiler + ["-shared", *flags, "-Wl,--no-undefined,-z,relro,-z,now", "-Wl,-soname," + profile["soname"], "-Wl,--version-script=" + str(version_script), "-Wl,--whole-archive", str(source / "libgfortran.a"), "-Wl,--no-whole-archive", str(source / "libgcc.a"), "-lm", "-ldl", "-o", str(output)]
    subprocess.run(command, check=True)
    linked = read_interface(output.read_bytes())
    if linked["soname"] != profile["soname"] or linked["symbols"] != profile["symbols"] or linked["versions"].keys() != profile["versions"].keys():
        raise ValueError("Relinked libgfortran changed its versioned public interface")
    print(f"libgfortran: preserved {len(profile['symbols'])} versioned symbols and SONAME {profile['soname']}")


if __name__ == "__main__":
    link_library(Path.cwd(), Path(os.environ["PREFIX"]) / "lib/libgfortran.so.3", shlex.split(os.environ["CC"]), shlex.split(os.environ["LDFLAGS"]))
