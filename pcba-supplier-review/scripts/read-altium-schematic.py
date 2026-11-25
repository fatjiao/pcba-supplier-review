"""Extract source records from supported Altium binary schematics without Altium Designer."""

import argparse
from collections import Counter, defaultdict
import hashlib
import io
from pathlib import Path
import struct


OLE_SIGNATURE = bytes.fromhex("D0 CF 11 E0 A1 B1 1A E1")
STREAM = "FileHeader"
LEGACY_ENCODING = "gb18030"
PIN_DIRECTIONS = ((1, 0), (0, 1), (-1, 0), (0, -1))
GRAPHICS = {
    "4": "text_labels",
    "17": "power_ports",
    "18": "ports",
    "25": "net_labels",
    "27": "wires",
    "29": "junctions",
}


def read_fields(payload, record):
    source_fields = {}
    utf8_fields = {}
    for item in payload.rstrip(b"\0").split(b"|"):
        key_bytes, separator, value = item.partition(b"=")
        if not item:
            continue
        if not separator or not key_bytes:
            raise ValueError(f"invalid text field at {STREAM} byte {record['byte_offset']}")
        key = key_bytes.decode("ascii").upper()
        if key.startswith("%UTF8%"):
            utf8_fields.setdefault(key[6:], ("utf-8", value))
        else:
            source_fields.setdefault(key, (LEGACY_ENCODING, value))
    source_fields.update(utf8_fields)

    fields = {}
    diagnostics = []
    for key, (encoding, value) in source_fields.items():
        try:
            fields[key] = value.decode(encoding)
        except UnicodeDecodeError:
            fields[key] = value.decode(encoding, errors="replace")
            diagnostics.append({
                "record_index": record["record_index"],
                "byte_offset": record["byte_offset"],
                "field": key,
                "encoding": encoding,
                "raw_value_hex": value.hex(),
            })
    return fields, diagnostics


def read_file_header(data):
    records = []
    diagnostics = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise ValueError(f"incomplete record header at {STREAM} byte {offset}")
        raw_length, = struct.unpack_from("<I", data, offset)
        record = {
            "record_index": len(records) - 1,
            "byte_offset": offset,
            "payload_size_bytes": raw_length & 0xFFFFFF,
            "encoding_flag": raw_length >> 24,
        }
        end = offset + 4 + record["payload_size_bytes"]
        if end > len(data):
            raise ValueError(f"record extends beyond {STREAM} at byte {offset}")
        if record["encoding_flag"] == 0:
            try:
                fields, issues = read_fields(data[offset + 4:end], record)
            except UnicodeDecodeError as error:
                raise ValueError(f"invalid text field key at {STREAM} byte {offset}") from error
            record["fields"] = fields
            diagnostics.extend(issues)
        records.append(record)
        offset = end

    if not records:
        raise ValueError(f"empty {STREAM} stream")
    header = records[0].get("fields", {}).get("HEADER", "")
    if "SCHEMATIC CAPTURE BINARY FILE" not in header.upper():
        raise ValueError(f"unsupported schematic signature in {STREAM}")
    return records, diagnostics


def record_type(record):
    return record.get("fields", {}).get("RECORD")


def unique_field(records, key):
    if len(records) == 1:
        return records[0]["fields"].get(key)
    return None


def pin_connection_point(record):
    fields = record["fields"]
    fractional_fields = ("LOCATION.X_FRAC", "LOCATION.Y_FRAC", "PINLENGTH_FRAC")
    try:
        if any(int(fields.get(key, 0)) for key in fractional_fields):
            return None
        x = int(fields["LOCATION.X"])
        y = int(fields["LOCATION.Y"])
        length = int(fields["PINLENGTH"])
        flags = int(fields["PINCONGLOMERATE"])
    except (KeyError, ValueError):
        return None
    if length < 0 or flags < 0:
        return None
    dx, dy = PIN_DIRECTIONS[flags & 3]
    return {"x": x + length * dx, "y": y + length * dy}


def component(source, children, used):
    owned = children[source["record_index"]]
    designators = [record for record in owned if record_type(record) == "34"]
    parameters = [record for record in owned if record_type(record) == "41"]
    pins = [
        {**record, "connection_point": pin_connection_point(record)}
        for record in owned if record_type(record) == "2"
    ]
    comments = [record for record in parameters if record["fields"].get("NAME", "").casefold() == "comment"]
    pcb_models = []
    for link in owned:
        if record_type(link) != "44":
            continue
        for model in children[link["record_index"]]:
            fields = model.get("fields", {})
            if fields.get("RECORD") == "45" and fields.get("MODELTYPE", "").upper() == "PCBLIB":
                pcb_models.append({
                    **model,
                    "name": fields.get("MODELNAME"),
                    "is_current": fields.get("ISCURRENT", "").upper() == "T",
                    "implementation_list_record_index": link["record_index"],
                    "implementation_list_byte_offset": link["byte_offset"],
                })
                used.add(link["record_index"])
    used.update(record["record_index"] for record in designators + parameters + pins + pcb_models)
    return {
        **source,
        "designator": unique_field(designators, "TEXT"),
        "comment": unique_field(comments, "TEXT"),
        "designators": designators,
        "parameters": parameters,
        "pins": pins,
        "pcb_models": pcb_models,
    }


def inventory(records):
    children = defaultdict(list)
    for record in records[1:]:
        owner = record.get("fields", {}).get("OWNERINDEX")
        if owner is not None:
            try:
                children[int(owner)].append(record)
            except ValueError as error:
                raise ValueError(f"invalid OWNERINDEX at {STREAM} byte {record['byte_offset']}") from error

    used = set()
    components = []
    graphics = {name: [] for name in GRAPHICS.values()}
    sheet_records = []
    for record in records[1:]:
        kind = record_type(record)
        if kind == "1":
            components.append(component(record, children, used))
        elif kind in GRAPHICS:
            graphics[GRAPHICS[kind]].append(record)
        elif kind == "31":
            sheet_records.append(record)
        else:
            continue
        used.add(record["record_index"])

    unmodeled_records = [
        {"record_type": record_type(record), **{key: value for key, value in record.items() if key != "fields"}}
        for record in records[1:] if record["record_index"] not in used
    ]
    return components, graphics, sheet_records, unmodeled_records


def read_document(path, olefile):
    source = path.resolve(strict=True)
    source_bytes = source.read_bytes()
    if not source_bytes.startswith(OLE_SIGNATURE):
        preview = source_bytes[:512]
        if preview.startswith((b"\xff\xfe", b"\xfe\xff")):
            preview = preview.decode("utf-16", errors="replace").encode("utf-8")
        if b"SCHEMATIC CAPTURE ASCII FILE" in preview.upper():
            raise ValueError("unsupported Altium ASCII schematic. Supply the binary schematic or a same-revision export")
        raise ValueError("unrecognized schematic format. Expected an Altium OLE binary schematic")

    with olefile.OleFileIO(io.BytesIO(source_bytes), write_mode=False, raise_defects=olefile.DEFECT_INCORRECT) as file:
        streams = file.listdir()
        if [STREAM] not in streams:
            raise ValueError(f"OLE file does not contain a top-level {STREAM} stream")
        data = file.openstream(STREAM).read()

    records, decode_diagnostics = read_file_header(data)
    components, graphics, sheet_records, unmodeled_records = inventory(records)
    binary_count = sum(bool(record["encoding_flag"]) for record in unmodeled_records)
    warnings = []
    if unmodeled_records:
        warnings.append(f"{len(unmodeled_records)} unmodeled source records are listed by location and size.")
    if binary_count:
        warnings.append(f"{binary_count} records use an unsupported binary encoding flag and may include pin data.")
    if decode_diagnostics:
        warnings.append(f"{len(decode_diagnostics)} field values have text decoding issues. Original value bytes are recorded.")
    return {
        "source": {"path": str(source), "sha256": hashlib.sha256(source_bytes).hexdigest(), "size_bytes": len(source_bytes)},
        "ole_streams": streams,
        "file_header": {
            "size_bytes": len(data),
            "record_count": len(records),
            "header_record": records[0],
            "record_type_counts": dict(Counter(record_type(record) or "untyped" for record in records[1:])),
            "unmodeled_record_type_counts": dict(Counter(record["record_type"] or "untyped" for record in unmodeled_records)),
            "unmodeled_records": unmodeled_records,
            "decode_diagnostics": decode_diagnostics,
        },
        "warnings": warnings,
        "sheet_records": sheet_records,
        "components": components,
        **graphics,
    }


def read_schematics(paths: list[Path]) -> dict:
    """Read each document independently. No project compilation or cross-file resolution is performed."""
    if not paths:
        raise ValueError("at least one schematic is required")
    try:
        import olefile
    except ModuleNotFoundError as error:
        raise ValueError("Missing dependency olefile. Install with python -m pip install olefile") from error

    documents = []
    for path in paths:
        try:
            documents.append(read_document(path, olefile))
        except (OSError, ValueError) as error:
            raise ValueError(f"{path}: {error}") from error
    return {
        "schema_version": 1,
        "coverage": "source_records_only",
        "record_index_convention": "FileHeader header is -1. Subsequent records are indexed from 0.",
        "byte_offset_convention": "Zero-based within FileHeader, at the start of the length prefix.",
        "legacy_text_encoding": LEGACY_ENCODING,
        "pin_connection_point_convention": "Computed from integer source coordinates, length and orientation. Null when these fields are unavailable or fractional.",
        "documents": documents,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sch", action="append", type=Path, required=True, help="Input schematic. Repeat for multiple documents.")
    parser.add_argument("--out", type=Path, required=True, help="Path for the source-record JSON output.")
    args = parser.parse_args()
    from altium_io import write_json

    try:
        write_json(args.out, read_schematics(args.sch), args.sch)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
