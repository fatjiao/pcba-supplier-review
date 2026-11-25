#!/usr/bin/env python3
"""Read component, pad and net evidence from an Altium binary PCB document.

Record indices are zero based. Byte offsets refer to the respective Data stream.
The extracted layer stack is CAD metadata. Use pcb_geometry.py for distances.
"""

from argparse import ArgumentParser
from collections import defaultdict
from hashlib import sha256
import math
import os
from pathlib import Path
import re
import struct

from altium_io import integer, legacy_copper_order, records, text_fields, units, write_json


NO_OWNER_OR_NET = 0xFFFF
WIDE_TEXT_INDEX_OFFSET = 115
EXTENDED_TEXT_MIN_LENGTH = 123
V6_LAYER_NAMES = {
    1: "Top Layer", 32: "Bottom Layer", 33: "Top Overlay", 34: "Bottom Overlay",
    35: "Top Paste", 36: "Bottom Paste", 37: "Top Solder", 38: "Bottom Solder",
    55: "Drill Guide", 56: "Keep-Out Layer", 73: "Drill Drawing", 74: "Multi-Layer",
    75: "Connections", 76: "Background", 77: "DRC Error Markers", 78: "Selections",
    79: "Visible Grid 1", 80: "Visible Grid 2", 81: "Pad Holes", 82: "Via Holes",
    **{layer: f"Mid Layer {layer - 1}" for layer in range(2, 32)},
    **{layer: f"Internal Plane {layer - 38}" for layer in range(39, 55)},
    **{layer: f"Mechanical {layer - 56}" for layer in range(57, 73)},
}


def properties(ole, name):
    for index, offset, (body,) in records(ole, name):
        fields = text_fields(body)
        if not fields:
            raise ValueError(f"{name}[{index}] has no readable PCB properties")
        yield index, offset, fields


def length_mm(raw):
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*(mm|mil|in)\s*",
                         raw or "", re.IGNORECASE)
    if match is None:
        return None
    millimeters = float(match[1]) * {"mm": 1, "mil": 0.0254, "in": 25.4}[match[2].lower()]
    return millimeters if math.isfinite(millimeters) else None


def optional_int(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def layer_stack(board):
    layers = defaultdict(dict)
    for field, value in board.items():
        match = re.fullmatch(r"V9_STACK_LAYER(\d+)_(NAME|LAYERID|COPTHICK)", field)
        if match:
            layers[int(match[1])][match[2]] = value
    detailed_copper = [{"stack_index": i, "name": entry.get("NAME") or f"Stack layer {i}",
                        "layer_id": optional_int(entry.get("LAYERID")), "copper_thickness": entry["COPTHICK"]}
                       for i, entry in sorted(layers.items()) if (length_mm(entry.get("COPTHICK")) or 0) > 0]
    legacy_order = legacy_copper_order(board)
    if detailed_copper:
        copper = detailed_copper
        method = "Board6 V9 nonzero copper thickness"
    elif legacy_order:
        copper = [{"name": board.get(f"LAYER{layer}NAME") or V6_LAYER_NAMES[layer], "layer_code": layer}
                  for layer in legacy_order]
        method = "Board6 V6 copper layer links"
    else:
        copper = []
        method = None
    substacks = any(board.get(key) for key in ("V9_SUBSTACK1_ID", "LAYERSUBSTACK_V8_1ID"))
    return {
        "source": "Board6/Data", "method": method, "copper_layers": copper,
        "copper_layer_count": len(copper) if method else None, "multiple_substacks": substacks,
        "distance_geometry_candidate": len(copper) == 2 and not substacks,
    }


def decode_pascal(blob, label, warnings):
    if not blob or blob[0] != len(blob) - 1:
        raise ValueError(f"{label} has an invalid length-prefixed string")
    try:
        return blob[1:].decode("cp936")
    except UnicodeDecodeError:
        warnings.append(f"{label} used a fallback code page for legacy text")
        return blob[1:].decode("cp1252", errors="replace")


def wide_strings(ole):
    """Decode the indexed UTF-16LE entries independently of the text count."""
    if not ole.exists("WideStrings6/Data"):
        return {}
    with ole.openstream("WideStrings6/Data") as stream:
        data = stream.read()
    position = 0
    strings = {}
    while position < len(data):
        if len(data) - position < 8:
            raise ValueError(f"Truncated WideStrings6 header at {position}")
        index, size = struct.unpack_from("<II", data, position)
        end = position + 8 + size
        if size % 2 or end > len(data):
            raise ValueError(f"Truncated UTF-16LE string in WideStrings6[{index}] at {position}")
        value = data[position + 8:end].decode("utf-16-le").split("\0", 1)[0]
        if index in strings and strings[index]["text"] != value:
            raise ValueError(f"Conflicting entries in WideStrings6[{index}]")
        strings.setdefault(index, {"text": value, "offset": position})
        position = end
    return strings


def clean_text(value):
    value = (value or "").strip()
    return value or None


def valid_ref(value):
    return bool(value and value.strip("?") and value.casefold() not in {"designator", ".designator"})


def text_evidence(ole, components, warnings):
    scope = "Texts6"
    if not ole.exists(f"{scope}/Header") and not ole.exists(f"{scope}/Data"):
        warnings.append("Texts6 is absent; source identifiers cannot establish physical PCB designators")
        return []
    unicode = wide_strings(ole)
    orphan_texts = []
    short_owner_records = 0
    for index, offset, (body, legacy) in records(ole, scope, 5, blocks=2):
        if len(body) < 40:
            raise ValueError(f"Truncated Texts6[{index}] at {offset}")
        owner = integer(body, 7, "H")
        if len(body) < EXTENDED_TEXT_MIN_LENGTH:
            short_owner_records += owner != NO_OWNER_OR_NET
            continue
        if not (body[40] or body[41]):
            continue
        unicode_index = integer(body, WIDE_TEXT_INDEX_OFFSET)
        entry = unicode.get(unicode_index)
        evidence = {"index": index, "offset": offset, "owner_index": owner if owner != NO_OWNER_OR_NET else None,
                    "layer_code": body[0], "layer": V6_LAYER_NAMES.get(body[0]),
                    "is_comment": bool(body[40]), "is_designator": bool(body[41])}
        if entry is None:
            evidence["text"] = decode_pascal(legacy, f"Texts6[{index}]", warnings)
        else:
            evidence.update(text=entry["text"], wide_string_index=unicode_index, wide_string_offset=entry["offset"])
        if owner >= len(components):
            orphan_texts.append(evidence)
            continue
        if body[41]:
            components[owner]["designators"].append(evidence)
        if body[40]:
            components[owner]["comments"].append(evidence)
    if short_owner_records:
        warnings.append(f"{short_owner_records} component Texts6 records do not have designator/comment flags")
    return orphan_texts


def pcb_components(ole):
    rows = []
    for index, offset, fields in properties(ole, "Components6"):
        x, y = fields.get("X"), fields.get("Y")
        xy = [length_mm(x), length_mm(y)]
        rows.append({"index": index, "offset": offset, "source_ref": fields.get("SOURCEDESIGNATOR"),
                     "component_designator": fields.get("DESIGNATOR"),
                     "source_comment": fields.get("COMMENT"), "pattern": fields.get("PATTERN"),
                     "layer": fields.get("LAYER"), "xy_mm": xy if all(v is not None for v in xy) else None,
                     "position_raw": [x, y], "source_unique_id": fields.get("SOURCEUNIQUEID"),
                     "unique_id": fields.get("UNIQUEID"), "source_library_reference": fields.get("SOURCELIBREFERENCE"),
                     "source_hierarchical_path": fields.get("SOURCEHIERARCHICALPATH"),
                     "footprint_description": fields.get("FOOTPRINTDESCRIPTION"),
                     "designators": [], "comments": [], "pad_indices": []})
    return rows


def distinct_text(entries, *, designator=False):
    names = {}
    for entry in entries:
        value = clean_text(entry["text"])
        if value is not None and (not designator or valid_ref(value)):
            names.setdefault(value.casefold() if designator else value, value)
    return list(names.values())


def finalize_components(components):
    physical = defaultdict(list)
    sources = defaultdict(list)
    ambiguous = []
    unidentified = []
    comment_conflicts = []
    renamed = []
    for component in components:
        index = component["index"]
        candidates = distinct_text(component["designators"], designator=True)
        source = clean_text(component["source_ref"])
        if valid_ref(source):
            sources[source.casefold()].append((source, index))
        if len(candidates) == 1:
            ref = candidates[0]
            physical[ref.casefold()].append((ref, index))
            component.update(ref=ref, physical_ref=ref, ref_state="physical")
            if valid_ref(source) and source.casefold() != ref.casefold():
                renamed.append(index)
        elif candidates:
            component.update(ref=None, physical_ref=None, ref_state="ambiguous_physical")
            ambiguous.append(index)
        else:
            placed = clean_text(component["component_designator"])
            fallback = placed if valid_ref(placed) else source
            if not valid_ref(fallback):
                fallback = None
                unidentified.append(index)
            component.update(ref=fallback, physical_ref=None, ref_state="unverified" if fallback else "unidentified")
        texts = [value for value in distinct_text(component["comments"]) if value.casefold() not in {"comment", ".comment"}]
        if len(texts) > 1:
            component["comment"] = None
            comment_conflicts.append(index)
        else:
            component["comment"] = texts[0] if texts else component["source_comment"]
    duplicate_physical = duplicate_refs(physical)
    for duplicate in duplicate_physical:
        for index in duplicate["component_indices"]:
            components[index]["ref_state"] = "duplicate_physical"
    return {
        "physical_designator_duplicates": duplicate_physical, "source_designator_duplicates": duplicate_refs(sources),
        "ambiguous_physical_designators": ambiguous, "unidentified_components": unidentified,
        "multiple_comments": comment_conflicts, "physical_ref_differs_from_source": renamed,
    }


def duplicate_refs(groups):
    return [{"ref": entries[0][0], "component_indices": [index for _, index in entries]}
            for entries in groups.values() if len(entries) > 1]


def pcb_pads(ole, components, nets, layer_names, warnings):
    rows = []
    for index, offset, (name, _, _, _, body, _) in records(ole, "Pads6", 2, blocks=6):
        if len(body) < 61:
            raise ValueError(f"Truncated Pads6[{index}] at {offset}")
        net_id, owner_id = integer(body, 3, "H"), integer(body, 7, "H")
        if net_id != NO_OWNER_OR_NET and net_id >= len(nets):
            raise ValueError(f"Pads6[{index}] has an invalid Nets6 index {net_id}")
        owner = owner_id if owner_id != NO_OWNER_OR_NET else None
        layer = body[0]
        diameter = units(body, 45, 1)[0]
        row = {"index": index, "offset": offset, "owner_index": owner,
               "pad": decode_pascal(name, f"Pads6[{index}]", warnings),
               "net_index": net_id if net_id != NO_OWNER_OR_NET else None,
               "net": nets[net_id]["name"] if net_id != NO_OWNER_OR_NET else None,
               "xy_mm": units(body, 13, 2), "layer_code": layer, "layer": layer_names.get(layer),
               "hole_diameter_mm": diameter, "plated": bool(body[60]) if diameter else None}
        if len(body) >= 120 and (body[114] or body[117]):
            row["drill_to_layer_code"] = body[114]
            row["drill_from_layer_code"] = body[117]
        if owner is not None:
            if owner < len(components):
                components[owner]["pad_indices"].append(index)
            else:
                warnings.append(f"Pads6[{index}] owner index {owner} is outside Components6")
        rows.append(row)
    return rows


def read_pcb(path: Path) -> dict:
    """Return indexed, provenance-bearing evidence from a readable native PCB."""
    import olefile

    path = Path(path)
    warnings = []
    with path.open("rb") as handle:
        original = os.fstat(handle.fileno())
        digest = sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        handle.seek(0)
        with olefile.OleFileIO(handle) as ole:
            board_records = list(properties(ole, "Board6"))
            if len(board_records) != 1:
                raise ValueError("Expected one Board6 metadata record in a single-board PCB document")
            board = board_records[0][2]
            components = pcb_components(ole)
            orphan_texts = text_evidence(ole, components, warnings)
            conflicts = finalize_components(components)
            nets = []
            for index, offset, fields in properties(ole, "Nets6"):
                if not fields.get("NAME"):
                    raise ValueError(f"Nets6[{index}] has no network name")
                nets.append({"index": index, "offset": offset, "name": fields["NAME"], "unique_id": fields.get("UNIQUEID")})
            layer_names = {code: board.get(f"LAYER{code}NAME") or name for code, name in V6_LAYER_NAMES.items()}
            pads = pcb_pads(ole, components, nets, layer_names, warnings)
        current = os.fstat(handle.fileno())
        if (original.st_size, original.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
            raise ValueError("PCB document changed while evidence was being read")
    return {"schema_version": 1, "format": "altium_pcb_binary",
            "source": {"filename": path.name, "sha256": digest.hexdigest(), "size_bytes": original.st_size},
            "board": {"kind": board.get("KIND"), "version": board.get("VERSION")},
            "layer_stack": layer_stack(board), "components": components, "nets": nets, "pads": pads,
            "conflicts": conflicts, "orphan_component_texts": orphan_texts, "warnings": warnings}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--pcb", type=Path, required=True, help="Readable native Altium PCB document")
    parser.add_argument("--out", type=Path, required=True, help="Separate JSON evidence file")
    args = parser.parse_args()
    try:
        write_json(args.out, read_pcb(args.pcb), sources=[args.pcb])
    except (ImportError, OSError, ValueError, struct.error) as error:
        parser.exit(2, f"PCB evidence: {error}\n")


if __name__ == "__main__":
    main()
