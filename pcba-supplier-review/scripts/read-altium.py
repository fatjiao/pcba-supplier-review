#!/usr/bin/env python3
"""Extract evidence from local Altium design files without launching an EDA app."""

import argparse
from pathlib import Path
from runpy import run_path
import struct

from altium_io import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sch", type=Path, action="append", default=[],
                        help="本板的二进制原理图文件，可以重复指定 --sch")
    parser.add_argument("--pcb", type=Path, help="本板的二进制 PCB 文件")
    parser.add_argument("--out", type=Path, required=True, help="新建的本地证据 JSON")
    args = parser.parse_args()
    if not args.sch and args.pcb is None:
        parser.error("请提供 --sch 或 --pcb，完整板卡取证需同时提供。")

    scripts = Path(__file__).resolve().parent
    evidence = {"schema_version": 1}
    try:
        if args.sch:
            read_schematics = run_path(str(scripts / "read-altium-schematic.py"))["read_schematics"]
            evidence["schematic"] = read_schematics(args.sch)
        if args.pcb is not None:
            read_pcb = run_path(str(scripts / "read-altium-pcb.py"))["read_pcb"]
            evidence["pcb"] = read_pcb(args.pcb)
        sources = [*args.sch, args.pcb] if args.pcb is not None else args.sch
        write_json(args.out, evidence, sources)
    except ModuleNotFoundError as error:
        if error.name != "olefile":
            raise
        parser.exit(1, "缺少 olefile。请运行 python -m pip install olefile。\n")
    except (OSError, ValueError, struct.error) as error:
        parser.exit(1, f"取证失败。{error}\n")


if __name__ == "__main__":
    main()
