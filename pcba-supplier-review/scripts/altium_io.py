"""Read Altium PCB record streams and save read-only extraction results."""

import json
import os
import struct
from pathlib import Path
from tempfile import NamedTemporaryFile


UNIT_MM = 0.00000254
V6_COPPER_LAYER_CODES = frozenset((*range(1, 33), *range(39, 55)))


def legacy_copper_order(board):
    """Follow the mutually declared V6 links from the first to the last copper layer."""
    order = [1]
    while order[-1] != 32:
        current = order[-1]
        try:
            following = int(board.get(f"LAYER{current}NEXT", "0"))
            previous = int(board.get(f"LAYER{following}PREV", "0"))
        except (TypeError, ValueError):
            return []
        if following in order or following not in V6_COPPER_LAYER_CODES or previous != current:
            return []
        order.append(following)
    return order


def integer(data, offset=0, form="I"):
    return struct.unpack_from("<" + form, data, offset)[0]


def units(data, offset, count):
    return tuple(value * UNIT_MM for value in struct.unpack_from(f"<{count}i", data, offset))


def text_fields(blob):
    fields = {}
    utf8 = {}
    for field in blob.rstrip(b"\0").split(b"|"):
        key, separator, value = field.partition(b"=")
        if not separator:
            continue
        if key.startswith(b"%UTF8%"):
            utf8[key[6:].decode("ascii", errors="replace")] = value.decode("utf-8", errors="replace")
        else:
            fields[key.decode("cp936", errors="replace")] = value.decode("cp936", errors="replace")
    return {**fields, **utf8}


def records(ole, name, record_type=None, blocks=1):
    header_name, data_name = f"{name}/Header", f"{name}/Data"
    if not ole.exists(header_name) or not ole.exists(data_name):
        raise ValueError(f"Unsupported PCB document: required stream {name} is absent")
    header = ole.openstream(header_name).read()
    if len(header) < 4:
        raise ValueError(f"Truncated PCB record count in {header_name}")
    data = ole.openstream(data_name).read()
    count = integer(header)
    if count * (4 * blocks + (record_type is not None)) > len(data):
        raise ValueError(f"PCB record count exceeds available bytes in {data_name}")
    position = 0
    for index in range(count):
        offset = position
        if record_type is not None:
            if position >= len(data) or data[position] != record_type:
                raise ValueError(f"Unexpected record type in {name}[{index}] at {position}")
            position += 1
        values = []
        for _ in range(blocks):
            if position + 4 > len(data):
                raise ValueError(f"Truncated record header in {name}[{index}] at {position}")
            size = integer(data, position)
            position += 4
            end = position + size
            if end > len(data):
                raise ValueError(f"Truncated record in {name}[{index}]")
            values.append(data[position:end])
            position = end
        yield index, offset, values
    if position != len(data):
        raise ValueError(f"Unsupported extra bytes in {name} at {position}")


def write_json(output: Path, data: dict, sources: list[Path]) -> None:
    """Commit a complete report to a new path on the same filesystem."""
    if output.resolve() in {source.resolve() for source in sources}:
        raise ValueError("输出路径与原始图档相同。请选择新的 JSON 路径。")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"输出文件已存在，请使用新的路径。{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", delete=False,
                                dir=output.parent, prefix=".altium-", suffix=".tmp") as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        if os.name == "nt":
            temporary.rename(output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
