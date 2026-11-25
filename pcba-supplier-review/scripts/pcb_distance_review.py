"""Turn scoped PCB geometry into company Checklist results for the Excel generator."""

import argparse
import json
from pathlib import Path

from pcb_evaluate import evaluate_document, load_rule_catalog

METRICS = {"creepage": "爬电距离", "clearance": "电气间隙", "outline": "铜边至板边", "route-width": "开槽宽度"}
OUTER_FACES = {"top", "bottom", "same-face"}
MEASUREMENT_LAYERS = OUTER_FACES | {"cross-face", "all-surfaces"}


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
    if kind == "clearance" and layer not in OUTER_FACES:
        raise ValueError("电气间隙选择 top、bottom 或 same-face。跨面空气间隙需独立证据。")
    if layer not in MEASUREMENT_LAYERS:
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


def is_outer_clearance_only(geometry):
    return geometry.get("geometry", {}).get("coverage") == "outer_clearance_only"


def outer_scope_label(scope, layer, metrics):
    groups = []
    for side in ("a", "b"):
        selector = metrics.get(side, {})
        objects = []
        for key, title in (("nets", "网络"), ("refs", "位号"), ("pads", "焊盘"), ("objects", "原生图元")):
            values = selector.get(key, [])
            if not values:
                continue
            if key == "pads":
                labels = [f"{item['ref']}-{item['pad']}" for item in values[:6]]
            elif key == "objects":
                labels = [f"{item['stream']} #{item['index']}" for item in values[:6]]
            else:
                labels = [str(item) for item in values[:6]]
            extra = f"等 {len(values)} 项" if len(values) > len(labels) else ""
            objects.append(f"{title} {', '.join(labels)}{extra}")
        if objects:
            groups.append(f"{side.upper()}（{'、'.join(objects)}）")
    detail = f"，{' 至 '.join(groups)}" if groups else ""
    return f"多层板外层二维名义量测，指定对象范围 {scope} / {layer}{detail}"


def measured(selection, geometry, scopes):
    kind = selection["kind"]
    if kind not in METRICS:
        raise ValueError(f"未知量测项目 {kind}")
    layer = selection.get("layer", "same-face")
    outer_only = is_outer_clearance_only(geometry)
    if outer_only:
        if layer not in MEASUREMENT_LAYERS:
            raise ValueError(f"未知量测范围 {layer}")
        if kind != "clearance" or layer not in OUTER_FACES:
            scope = selection.get("scope")
            location = f"指定对象范围 {scope} / {layer}" if scope else f"请求量测范围 {layer}"
            metric = METRICS[kind]
            return {}, location, (
                f"多层板外层二维名义模型仅提供同面铜边间隙，当前 {metric}（{layer}）缺少量测证据。"
                "可另用同版独立量测作为条款依据。"
            )
    if kind in {"creepage", "clearance"}:
        scope = selection["scope"]
        if scope not in scopes:
            raise ValueError(f"几何结果没有范围 {scope}")
        result = scoped_distance(scopes[scope], kind, layer)
        points = endpoints(result.get("path") or {})
        lower_points = endpoints(result.get("lower_path") or {})
        if points and lower_points and points != lower_points:
            points = f"路径端点 {points}\n下界端点 {lower_points}"
        if outer_only:
            label = outer_scope_label(scope, layer, scopes[scope])
            location = f"{label}\n{points}" if points else f"{label}\n当前范围未获得可定位的最短端点"
            return result, location, f"{label}。判定限于此范围内的同面外层铜边。"
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
        traces = []
        if route.get("native_index") is not None:
            stream = route.get("native_stream") or "图元"
            location = f"PCB {stream} #{route['native_index']}"
            if route.get("native_offset") is not None:
                location += f" @{route['native_offset']}"
            traces.append(location)
        if route.get("gm1_object") is not None:
            source = (geometry.get("gm1") or {}).get("filename") or "加工 Gerber"
            traces.append(f"{source} #{route['gm1_object']} D{route['aperture']}")
        return {"lower_mm": width, "upper_mm": width}, " / ".join(traces), "已确认隔离槽图元的名义加工宽度。"
    raise ValueError(f"未知量测项目 {kind}")


def bind_plan(plan, geometry, rules):
    pcb = plan["pcbSafety"]
    outer_only = is_outer_clearance_only(geometry)
    for label in (("pcb", "scopes") if outer_only else ("pcb", "gm1")):
        source = geometry.get(label)
        if source and source.get("filename") and source.get("sha256"):
            pcb.setdefault("sources", []).append(f"{source['filename']} SHA-256 {source['sha256']}")
    if outer_only:
        note = "多层板外层二维名义模型仅覆盖用户指定对象的同面铜边间隙。其余适用项目需同版独立证据。"
        pcb["measurementNote"] = " ".join(filter(None, [pcb.get("measurementNote"), note]))
    elif geometry.get("geometry", {}).get("mechanical_route_selection_requires_review"):
        note = "本次加工模型采用原生板切口和无镀铜孔。附加机械图元的加工用途待确认。"
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
        existing_evidence = row.get("evidence")
        if outer_only:
            row["measurement_basis"] = "pcb_nominal"
            existing_evidence = str(existing_evidence or row.get("note") or "").strip()
            if row.get("applicable") is False and not existing_evidence:
                # Model limitations are not evidence that a company rule is inapplicable.
                row["applicable"] = None
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
        row["evidence"] = (" " if outer_only else "").join(filter(None, [existing_evidence, method]))
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
