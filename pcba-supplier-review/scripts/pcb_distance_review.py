"""Turn scoped PCB geometry into company Checklist results for the Excel generator."""

import argparse
import json
from pathlib import Path

from pcb_evaluate import evaluate_document, load_rule_catalog

METRICS = {"creepage": "爬电距离", "clearance": "电气间隙", "outline": "铜边至板边", "route-width": "开槽宽度"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def anchor(obj):
    position = ", ".join(f"{value:.3f}" for value in obj.get("xy_mm", []))
    ref = "-".join(str(value) for value in (obj.get("ref"), obj.get("pad")) if value)
    return " ".join(str(value) for value in (
        obj.get("layer"), ref, obj.get("net"),
        f"{obj['stream']} #{obj['index']} @{obj['offset']}" if obj.get("stream") else None,
        f"({position}) mm" if position else None,
    ) if value)


def endpoints(record):
    return " → ".join(anchor(record[key]) for key in ("a", "b") if record.get(key))


def minimum(records):
    """A minimum can inherit an upper witness from one scope and needs lower bounds for every scope."""
    if not records:
        return {}
    known_upper = [record for record in records if record.get("upper_mm") is not None]
    known_lower = [record for record in records if record.get("lower_mm") is not None]
    upper = min(known_upper, key=lambda record: record["upper_mm"]) if known_upper else {}
    lower = min(known_lower, key=lambda record: record["lower_mm"]) if len(known_lower) == len(records) else {}
    return {
        "lower_mm": lower.get("lower_mm"), "upper_mm": upper.get("upper_mm"),
        "path": upper.get("path") or lower.get("path"),
        "lower_path": lower.get("path"),
    }


def scoped_distance(metrics, kind, layer):
    if kind == "clearance" and layer not in {"top", "bottom", "same-face"}:
        raise ValueError("电气间隙选择 top、bottom 或 same-face。跨面空气间隙需独立证据。")
    if layer not in {"top", "bottom", "same-face", "cross-face", "all-surfaces"}:
        raise ValueError(f"未知量测范围 {layer}")

    parts = []
    if layer != "cross-face":
        names = [layer] if layer in {"top", "bottom"} else ["top", "bottom"]
        for name in names:
            faces = metrics.get("layers", {})
            if name in faces and faces[name] is None:
                continue
            face = faces.get(name) or {}
            if kind == "clearance":
                point = face.get("planar_clearance") or {}
                parts.append({"lower_mm": point.get("mm"), "upper_mm": point.get("mm"), "path": point})
            else:
                parts.append(face.get("creepage") or {})
    if layer in {"all-surfaces", "cross-face"}:
        across = metrics.get("cross_face") or {}
        for direction in ("top_to_bottom", "bottom_to_top"):
            if across.get("verified") and direction in across and across[direction] is None:
                continue
            part = across.get(direction) if across.get("verified") else None
            parts.append({"lower_mm": part.get("lower_mm"), "upper_mm": part.get("upper_mm"), "path": part} if part else {})
    return minimum(parts)


def measured(selection, geometry, scopes):
    kind = selection["kind"]
    layer = selection.get("layer", "same-face")
    if kind in {"creepage", "clearance"}:
        scope = selection["scope"]
        if scope not in scopes:
            raise ValueError(f"几何结果没有范围 {scope}")
        result = scoped_distance(scopes[scope], kind, layer)
        points = endpoints(result.get("path") or {})
        lower_points = endpoints(result.get("lower_path") or {})
        if points and lower_points and points != lower_points:
            points = f"路径端点 {points}\n下界端点 {lower_points}"
        scope_type = "板面爬电" if kind == "creepage" else "同层铜边电气间隙"
        return result, points, f"PCB 名义几何，{scope_type}，范围 {scope} / {layer}。"
    if kind == "outline":
        if layer not in {"top", "bottom", "same-face"}:
            raise ValueError("铜边至板外形只接受 top、bottom 或 same-face。")
        names = [layer] if layer != "same-face" else ["top", "bottom"]
        source = "board_edge_tracks" if selection.get("tracks_only") else "board_edge"
        edges = [(geometry.get(source, {}).get(name) or {}) for name in names]
        distances = [{"lower_mm": edge.get("mm"), "upper_mm": edge.get("mm"), "path": edge} for edge in edges]
        result = minimum(distances)
        chosen = result.get("path") or {}
        xy = ", ".join(f"{v:.3f}" for v in chosen.get("xy_mm", []))
        points = f"{anchor(chosen['a'])} → 板外形 ({xy}) mm" if chosen.get("a") else ""
        scope = "走线铜边" if source == "board_edge_tracks" else "各类铜边"
        return result, points, f"PCB {scope}至板外形。"
    if kind == "route-width":
        routes = geometry.get("geometry", {}).get("mechanical_routes", [])
        if "gm1_object" in selection:
            routes = [route for route in routes if route.get("gm1_object") == selection["gm1_object"]]
        if not routes:
            return {}, "", "当前结果没有已配准并确认为铣槽的加工图元。"
        route = min(routes, key=lambda row: row["width_mm"])
        width = route["width_mm"]
        trace = f"PCB 机械一层 Tracks6 #{route.get('native_index')} @{route.get('native_offset')}"
        if route.get("gm1_object") is not None:
            trace += f" / GM1 #{route['gm1_object']} D{route['aperture']}"
        return {"lower_mm": width, "upper_mm": width}, trace, "已声明铣槽图元的名义加工宽度。"
    raise ValueError(f"未知量测项目 {kind}")


def bind_plan(plan, geometry, rules):
    pcb = plan["pcbSafety"]
    for label in ("pcb", "gm1"):
        source = geometry.get(label)
        if source and source.get("filename") and source.get("sha256"):
            pcb.setdefault("sources", []).append(f"{source['filename']} SHA-256 {source['sha256']}")
    if geometry.get("geometry", {}).get("mechanical_route_selection_requires_review"):
        note = "本次只按原生板切口建模。机械一层有待确认的对象，当前结论限于该图档情景。"
        pcb["measurementNote"] = "".join(filter(None, [pcb.get("measurementNote"), note]))
    scopes = {entry["id"]: entry for entry in geometry.get("measurements", [])}
    for row in pcb["rows"]:
        selection = row.pop("geometry", None)
        if selection is None:
            continue
        rule = rules.get(str(row.get("rule_row")))
        metric = METRICS.get(selection.get("kind"))
        if rule is None or metric is None or rule["metric"] != metric:
            raise ValueError(f"{row['id']} 量测类型与所选公司条款不符，请核对 rule_row 与 geometry.kind")
        if any(row.get(field) is not None for field in ("measurement_mm", "lower_bound_mm", "upper_bound_mm")):
            raise ValueError(f"{row['id']} 同时提供了几何位置和手工距离")
        result, points, method = measured(selection, geometry, scopes)
        if points:
            row["location"] = "\n".join(filter(None, [row.get("location"), points]))
        else:
            row.setdefault("location", "当前范围未获得可定位的最短端点")
        bounds = result.get("lower_mm"), result.get("upper_mm")
        lower, upper = bounds
        if lower is not None and upper is not None and abs(lower - upper) <= 1e-9:
            row["measurement_mm"] = upper
        else:
            row["lower_bound_mm"], row["upper_bound_mm"] = bounds
        row["evidence"] = "".join(filter(None, [row.get("evidence"), method]))
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("geometry", help="pcb_geometry.py 生成的 JSON")
    parser.add_argument("plan", help="选择公司条款和量测范围的 JSON")
    parser.add_argument("output", help="供 Excel 生成器使用的 JSON")
    parser.add_argument("--base-review", help="合并已有的三个工作表数据")
    parser.add_argument("--rules", help="本次获准使用的规则目录 JSON")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    inputs = (args.geometry, args.plan, args.base_review, args.rules)
    if any(path and output == Path(path).resolve() for path in inputs):
        parser.error("输出文件须有独立路径。")
    rules = load_rule_catalog(args.rules) if args.rules else load_rule_catalog()
    result = evaluate_document(bind_plan(read_json(args.plan), read_json(args.geometry), rules['rules']), rules)
    if args.base_review:
        original = read_json(args.base_review)
        if original["project"] != result["project"]:
            parser.error("项目名不一致，请核对 PCB 与三表数据的发布范围。")
        result = {**original, "pcbSafety": result["pcbSafety"]}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
