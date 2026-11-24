"""Read Altium binary PCB outer-layer copper and bound nominal board-surface distances."""

from argparse import ArgumentParser
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
import struct

try:
    import numpy as np
    import olefile
    import shapely
    from shapely.affinity import affine_transform, rotate, translate
    from shapely.geometry import LineString, Point, Polygon, box
    from shapely.ops import nearest_points
    from shapely.strtree import STRtree
except ImportError as error:
    raise SystemExit("Install the PCB geometry dependency first: python -m pip install olefile shapely") from error


UNIT_MM = 0.00000254
OUTER = {1: "top", 32: "bottom"}
MECHANICAL_ONE = 57
GEOMETRY_TOLERANCE_MM = 0.000001
ALIGNMENT_TOLERANCE_MM = 0.01
SAMPLE_STEP_MM = 0.01


def integer(data, offset=0, form="I"):
    return struct.unpack_from("<" + form, data, offset)[0]


def units(data, offset, count):
    return tuple(value * UNIT_MM for value in struct.unpack_from(f"<{count}i", data, offset))


def text_fields(blob):
    fields = blob.rstrip(b"\0").decode("cp936", errors="replace").split("|")
    return dict(field.split("=", 1) for field in fields if "=" in field)


def records(ole, name, record_type=None, blocks=1):
    if not ole.exists(f"{name}/Data") or not ole.exists(f"{name}/Header"):
        raise ValueError(f"Unsupported PCB document: required stream {name} is absent")
    data = ole.openstream(f"{name}/Data").read()
    count = integer(ole.openstream(f"{name}/Header").read())
    position = 0
    for index in range(count):
        offset = position
        if record_type is not None:
            if data[position] != record_type:
                raise ValueError(f"Unexpected record type in {name}[{index}] at {position}")
            position += 1
        values = []
        for _ in range(blocks):
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


def named_records(ole, name):
    return [text_fields(values[0]) for _, _, values in records(ole, name)]


@dataclass(frozen=True)
class Copper:
    layer: str
    net: str | None
    ref: str | None
    pad: str | None
    stream: str
    index: int
    offset: int
    polygon: object

    def anchor(self, point):
        return {
            "net": self.net,
            "ref": self.ref,
            "pad": self.pad,
            "layer": self.layer,
            "stream": self.stream,
            "index": self.index,
            "offset": self.offset,
            "xy_mm": point_coordinates(point),
        }


@dataclass(frozen=True)
class Route:
    stream: str
    index: int
    offset: int
    width: float
    centerline: object
    gm1_object: int | None = None
    aperture: int | None = None

    @property
    def void(self):
        return self.centerline.buffer(self.width / 2, quad_segs=64)


@dataclass(frozen=True)
class Opening:
    id: str
    polygon: object
    stream: str
    index: int
    offset: int


def point_coordinates(point):
    return [round(point.x, 9), round(point.y, 9)]


def pad_polygon(x, y, width, height, shape, rotation, alternate, corner_percent):
    left, bottom = -width / 2, -height / 2
    right, top = width / 2, height / 2
    if alternate == 9:
        radius = min(width, height) * corner_percent / 200
        polygon = box(left + radius, bottom + radius, right - radius, top - radius)
        if radius > 0:
            polygon = polygon.buffer(radius, quad_segs=64)
    elif alternate not in (0, shape):
        raise ValueError(f"Unsupported alternate pad shape code {alternate}")
    elif shape == 1:
        minor = min(width, height)
        endpoints = [(left + minor / 2, bottom + minor / 2), (right - minor / 2, top - minor / 2)]
        axis = Point(endpoints[0]) if endpoints[0] == endpoints[1] else LineString(endpoints)
        polygon = axis.buffer(minor / 2, quad_segs=64)
    elif shape == 2:
        polygon = box(left, bottom, right, top)
    elif shape == 3:
        chamfer = min(width, height) * (1 - math.sqrt(0.5)) / 2
        polygon = Polygon(
            [(left + chamfer, bottom), (right - chamfer, bottom), (right, bottom + chamfer),
             (right, top - chamfer), (right - chamfer, top), (left + chamfer, top),
             (left, top - chamfer), (left, bottom + chamfer)]
        )
    else:
        raise ValueError(f"Unsupported pad shape code {shape}")
    return translate(rotate(polygon, rotation, origin=(0, 0)), x, y)


def region_polygon(data):
    metadata_end = 22 + integer(data, 18)
    fields = text_fields(data[22:metadata_end])
    cursor = metadata_end
    rings = []
    for _ in range(1 + integer(data, 14, "H")):
        count = integer(data, cursor)
        cursor += 4
        ring = [
            tuple(x * UNIT_MM for x in struct.unpack_from("<dd", data, cursor + 16 * i))
            for i in range(count)
        ]
        cursor += 16 * count
        rings.append(ring)
    if not rings or len(rings[0]) < 3:
        return fields, None
    polygon = Polygon(rings[0], rings[1:])
    return fields, shapely.make_valid(polygon)


def arc_axis(x, y, radius, start, end):
    degrees = (end - start) % 360 or 360
    pieces = max(4, math.ceil(degrees / 0.25))
    t = np.linspace(math.radians(start), math.radians(start + degrees), pieces + 1)
    return LineString(np.column_stack((x + radius * np.cos(t), y + radius * np.sin(t))))


def polygon_parts(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry]
    return [part for child in getattr(geometry, "geoms", []) for part in polygon_parts(child)]


def verify_stack(ole):
    board = named_records(ole, "Board6")[0]
    if any(board.get(key) for key in ("V9_SUBSTACK1_ID", "LAYERSUBSTACK_V8_1ID")):
        raise ValueError("Multiple board stacks are outside the supported two-layer geometry model")
    copper_stack = [
        board.get(key.removesuffix("_COPTHICK") + "_NAME", key)
        for key, value in board.items()
        if key.startswith("V9_STACK_LAYER") and key.endswith("_COPTHICK")
        and value and value not in ("0", "0mil", "0mm")
    ]
    if copper_stack and len(copper_stack) != 2:
        raise ValueError(f"Found {len(copper_stack)} copper layers; the script supports two-layer boards")
    if not copper_stack and (board.get("LAYER1NEXT"), board.get("LAYER32PREV")) != ("32", "1"):
        raise ValueError("Could not verify a supported two-layer stack from the PCB document")
    return copper_stack or ["Top Layer", "Bottom Layer"]


def read_native(path):
    copper, cutouts, holes, mechanical = [], [], [], []
    plated_slots = 0
    with olefile.OleFileIO(str(path)) as ole:
        stack = verify_stack(ole)
        nets = [item["NAME"] for item in named_records(ole, "Nets6")]
        components = named_records(ole, "Components6")

        def add(layer, stream, index, offset, data, polygon, pad=None):
            net_no, owner = integer(data, 3, "H"), integer(data, 7, "H")
            if net_no != 65535 and net_no >= len(nets):
                raise ValueError(f"Invalid net index in {stream}[{index}]")
            net = nets[net_no] if net_no < len(nets) else None
            ref = components[owner].get("SOURCEDESIGNATOR") if owner < len(components) else None
            copper.append(Copper(OUTER[layer], net, ref, pad, stream, index, offset, polygon))

        for index, offset, values in records(ole, "Pads6", 2, blocks=6):
            pad = values[0][1:].decode("cp936", errors="replace")
            data, extra = values[4:]
            layer = data[0]
            x, y, tx, ty, _, _, bx, by, diameter = units(data, 13, 9)
            angle = integer(data, 52, "d")
            hole_shape = extra[262] if len(extra) >= 596 else 0
            if diameter and hole_shape not in (0, 2):
                raise ValueError(f"Unsupported drill shape {hole_shape} in Pads6[{index}]")
            if diameter and data[60] and hole_shape == 2:
                plated_slots += 1
            if diameter and not data[60]:
                center = Point(x, y)
                if hole_shape == 2:
                    length = max(units(extra, 263, 1)[0], diameter)
                    axis_length = length - diameter
                    axis = LineString([(-axis_length / 2, 0), (axis_length / 2, 0)])
                    drill_angle = integer(extra, 267, "d") + angle
                    center = translate(rotate(axis, drill_angle, origin=(0, 0)), x, y)
                holes.append(Opening(f"npth-{index}", center.buffer(diameter / 2, quad_segs=64), "Pads6", index, offset))
                continue
            for side, width, height, code, alternate_at, corner_at in (
                (1, tx, ty, 49, 532, 564), (32, bx, by, 51, 563, 595),
            ):
                if layer not in (side, 74) or min(width, height) <= 0:
                    continue
                alternate = extra[alternate_at] if len(extra) >= 596 else 0
                corner = extra[corner_at] if len(extra) >= 596 else 0
                polygon = pad_polygon(x, y, width, height, data[code], angle, alternate, corner)
                add(side, "Pads6", index, offset, data, polygon, pad)

        for index, offset, (data,) in records(ole, "Vias6", 3):
            x, y, diameter, _ = units(data, 13, 4)
            if diameter <= 0:
                continue
            for side in set(data[29:31]) & OUTER.keys():
                add(side, "Vias6", index, offset, data, Point(x, y).buffer(diameter / 2, quad_segs=64))

        for stream, record_type in (("Tracks6", 4), ("Arcs6", 1)):
            for index, offset, (data,) in records(ole, stream, record_type):
                layer = data[0]
                if layer not in (*OUTER, MECHANICAL_ONE) or data[2] == 2:
                    continue
                if stream == "Tracks6":
                    x1, y1, x2, y2, width = units(data, 13, 5)
                    axis = LineString([(x1, y1), (x2, y2)])
                else:
                    x, y, radius = units(data, 13, 3)
                    start, end = struct.unpack_from("<dd", data, 25)
                    width = units(data, 41, 1)[0]
                    axis = arc_axis(x, y, radius, start, end)
                if width <= 0:
                    continue
                if layer == MECHANICAL_ONE:
                    mechanical.append(Route(stream, index, offset, width, axis))
                else:
                    add(layer, stream, index, offset, data, axis.buffer(width / 2, quad_segs=64))

        for index, offset, (data,) in records(ole, "Fills6", 6):
            if data[0] not in OUTER or data[2] == 2:
                continue
            x1, y1, x2, y2 = units(data, 13, 4)
            polygon = box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            add(data[0], "Fills6", index, offset, data, rotate(polygon, integer(data, 29, "d")))

        for index, offset, (data,) in records(ole, "Regions6", 11):
            fields, polygon = region_polygon(data)
            if polygon is None:
                continue
            if fields.get("ISBOARDCUTOUT") == "TRUE":
                cutouts.append(Opening(f"board-cutout-{index}", polygon, "Regions6", index, offset))
            elif data[0] in OUTER and data[2] != 2 and fields.get("KIND", "0") == "0":
                add(data[0], "Regions6", index, offset, data, polygon)

        board_regions = [region_polygon(data)[1] for _, _, (data,) in records(ole, "BoardRegions", 11)]
        outlines = [part for geometry in board_regions if geometry is not None for part in polygon_parts(geometry)]
        if not outlines:
            raise ValueError("No supported BoardRegions outline in the PCB document")
    if not copper:
        raise ValueError("No supported outer-layer copper primitives in the PCB document")
    return copper, shapely.union_all(outlines), cutouts + holes, mechanical, plated_slots, stack


def gerber_routes(path, apertures):
    if not apertures or not all(isinstance(item, int) and item > 0 for item in apertures):
        raise ValueError("Provide the confirmed routing apertures in mechanical_route.apertures for GM1")
    try:
        from gerbonara import GerberFile, graphic_objects
        from gerbonara.apertures import CircleAperture
        from gerbonara.utils import MM
    except ImportError as error:
        raise ValueError("GM1 parsing requires gerbonara: python -m pip install gerbonara") from error
    objects = GerberFile.open(str(path)).objects
    routes = []
    for index, item in enumerate(objects):
        aperture = getattr(item, "aperture", None)
        code = getattr(aperture, "original_number", None)
        if code not in apertures:
            continue
        if not isinstance(aperture, CircleAperture) or aperture.hole_dia or getattr(item, "polarity_dark", True) is False:
            raise ValueError(f"Unsupported selected GM1 aperture D{code} on object {index}")
        if isinstance(item, graphic_objects.Line):
            coords = [(item.x1, item.y1), (item.x2, item.y2)]
        elif isinstance(item, graphic_objects.Arc):
            subdivisions = list(item.approximate(max_error=0.001, unit=MM, clip_max_error=False))
            coords = [(subdivisions[0].x1, subdivisions[0].y1), *[(part.x2, part.y2) for part in subdivisions]]
        else:
            raise ValueError(f"Selected GM1 aperture D{code} has a non-routing object {index}")
        coordinate_unit = item.unit if isinstance(item, graphic_objects.Line) else subdivisions[0].unit
        axis = LineString([(MM(x, coordinate_unit), MM(y, coordinate_unit)) for x, y in coords])
        routes.append(Route("GM1", index, 0, MM(aperture.diameter, aperture.unit), axis, index, code))
    if set(apertures) != {route.aperture for route in routes}:
        raise ValueError("A confirmed routing aperture is absent from the selected GM1 file")
    return routes


def register_routes(plotted, native):
    """Solve a translation using route signatures, then validate all matched centerlines."""
    candidates = []
    for route in plotted:
        anchor = route.centerline.centroid
        matches = []
        for item in native:
            if abs(route.width - item.width) > ALIGNMENT_TOLERANCE_MM:
                continue
            if abs(route.centerline.length - item.centerline.length) > 2 * ALIGNMENT_TOLERANCE_MM:
                continue
            target = item.centerline.centroid
            offset = target.x - anchor.x, target.y - anchor.y
            axis = translate(route.centerline, *offset)
            if axis.hausdorff_distance(item.centerline) <= ALIGNMENT_TOLERANCE_MM:
                matches.append((item, offset))
        if not matches:
            raise ValueError(f"GM1 object {route.gm1_object} lacks a matching native Mechanical1 primitive")
        candidates.append(matches)
    anchor = min(candidates, key=len)
    solutions = []
    for _, proposed in anchor:
        match_set, offsets = [], []
        for options in candidates:
            matching = [
                (math.dist(proposed, offset), item, offset)
                for item, offset in options if math.dist(proposed, offset) <= ALIGNMENT_TOLERANCE_MM
            ]
            if not matching:
                break
            _, item, offset = min(matching, key=lambda choice: choice[0])
            match_set.append(item)
            offsets.append(offset)
        if len(match_set) != len(plotted):
            continue
        if len({(item.stream, item.index) for item in match_set}) != len(match_set):
            continue
        dx, dy = (median(values) for values in zip(*offsets))
        errors = [
            translate(a.centerline, dx, dy).hausdorff_distance(b.centerline)
            for a, b in zip(plotted, match_set)
        ]
        if max(errors) > ALIGNMENT_TOLERANCE_MM:
            continue
        if not any(math.dist((dx, dy), previous[0]) <= ALIGNMENT_TOLERANCE_MM for previous in solutions):
            solutions.append(((dx, dy), match_set, max(errors)))
    if len(solutions) != 1:
        raise ValueError("Cannot uniquely register the selected GM1 routes to native Mechanical1")
    (dx, dy), matched, error = solutions[0]
    routes = [
        Route(b.stream, b.index, b.offset, a.width, translate(a.centerline, dx, dy), a.index, a.aperture)
        for a, b in zip(plotted, matched)
    ]
    return routes, {"method": "matched_native_mechanical1", "translation_mm": [dx, dy],
                    "maximum_centerline_error_mm": error, "matched_primitives": len(routes)}


def explicit_registration(plotted, native, matches):
    if not isinstance(matches, list) or len(matches) < 2:
        raise ValueError("Supply at least two independently identified GM1 and PCB datum correspondences")
    deltas = []
    points = set()
    for match in matches:
        gm1, pcb = tuple(match["gm1_mm"]), tuple(match["pcb_mm"])
        if len(gm1) != 2 or len(pcb) != 2 or not all(math.isfinite(value) for value in gm1 + pcb):
            raise ValueError("Datum correspondences must be two finite millimeter coordinates each")
        points.add(gm1)
        deltas.append(tuple(p - g for p, g in zip(pcb, gm1)))
    if len(points) < 2:
        raise ValueError("Datum correspondences must represent distinct positions")
    dx, dy = (median(values) for values in zip(*deltas))
    error = max(math.dist((dx, dy), value) for value in deltas)
    if error > ALIGNMENT_TOLERANCE_MM:
        raise ValueError("Datum correspondences do not support an unrotated GM1 translation")
    calibrated = []
    for route in plotted:
        axis = translate(route.centerline, dx, dy)
        closest = [
            item for item in native if abs(item.width - route.width) <= ALIGNMENT_TOLERANCE_MM
            and item.centerline.hausdorff_distance(axis) <= ALIGNMENT_TOLERANCE_MM
        ]
        if len(closest) == 1:
            item = closest[0]
            calibrated.append(Route(item.stream, item.index, item.offset, route.width, axis, route.index, route.aperture))
        else:
            calibrated.append(Route("GM1", route.index, 0, route.width, axis, route.index, route.aperture))
    return calibrated, {
        "method": "supplied_datum_correspondences", "translation_mm": [dx, dy], "maximum_datum_error_mm": error,
        "matched_primitives": sum(item.stream != "GM1" for item in calibrated),
    }


def min_shape_distance(rows, target):
    if not rows:
        return None
    row = min(rows, key=lambda item: item.polygon.distance(target))
    a, xy = nearest_points(row.polygon, target)
    return {"mm": a.distance(xy), "a": row.anchor(a), "xy_mm": point_coordinates(xy)}


def route_report(routes, native_openings, by_layer, outline):
    voids = [(route, route.void) for route in routes]
    groups = polygon_parts(shapely.union_all([void for _, void in voids]))
    details = []
    for route, void in voids:
        details.append({
            "gm1_object": route.gm1_object,
            "aperture": route.aperture,
            "width_mm": route.width,
            "centerline_mm": [list(point) for point in route.centerline.coords],
            "native_stream": route.stream if route.stream != "GM1" else None,
            "native_index": route.index if route.stream != "GM1" else None,
            "native_offset": route.offset if route.stream != "GM1" else None,
            "copper": {layer: min_shape_distance(rows, void) for layer, rows in by_layer.items()},
            "native_cutouts": [
                opening.id for opening in native_openings if void.intersection(opening.polygon).area > 0.000001
            ],
            "overlap_mm2": {
                layer: sum(row.polygon.intersection(void).area for row in rows if row.polygon.intersects(void))
                for layer, rows in by_layer.items()
            },
        })
    group_details = [
        {"bounds_mm": list(group.bounds), "outside_outline_mm2": group.difference(outline).area,
         "gm1_objects": [route.gm1_object for route, void in voids
                         if route.gm1_object is not None and void.intersects(group)]}
        for group in groups
    ]
    return details, group_details


def seam_coordinates(geometry, step):
    if geometry.geom_type == "Point":
        return [np.array([geometry.y])]
    if geometry.geom_type == "LineString":
        start, end = geometry.bounds[1], geometry.bounds[3]
        count = max(2, math.ceil((end - start) / step) + 1)
        return [np.linspace(start, end, count)]
    return [points for part in getattr(geometry, "geoms", []) for points in seam_coordinates(part, step)]


def build_seams(surface, routes, openings):
    """Sample transverse crossings; distances to the endpoints have a 2-Lipschitz bound."""
    seam_specs = set()
    for route in routes:
        coords = route.centerline.coords
        first, last = coords[0], coords[-1]
        if first == last:
            continue
        angle = math.atan2(last[1] - first[1], last[0] - first[0]) % math.pi
        ny, nx = math.cos(angle), -math.sin(angle)
        center = route.centerline.centroid
        center_x = nx * center.x + ny * center.y
        for fraction in (-0.467, -0.30, 0, 0.30, 0.467):
            seam_specs.add((round(angle, 7), round(center_x + route.width * fraction, 5)))
    for opening in openings:
        if opening.polygon.area <= 0:
            continue
        center = opening.polygon.centroid
        seam_specs.update(((0.0, round(center.y, 5)), (round(math.pi / 2, 7), -round(center.x, 5))))
    seams, transformed = [], {}
    for angle, cut_at in sorted(seam_specs):
        params = (-math.sin(angle), math.cos(angle), math.cos(angle), math.sin(angle), 0, 0)
        if angle not in transformed:
            transformed[angle] = affine_transform(surface, params)
        surface_in_frame = transformed[angle]
        _, ymin, _, ymax = surface_in_frame.bounds
        section = LineString([(cut_at, ymin - 1), (cut_at, ymax + 1)])
        segments = seam_coordinates(surface_in_frame.intersection(section), SAMPLE_STEP_MM)
        positions = np.sort(np.concatenate(segments)) if segments else np.empty(0)
        points = shapely.points(np.full(len(positions), cut_at), positions)
        seams.append((cut_at, params, positions, points))
    return seams


def box_near(a, b, radius):
    left, bottom, right, top = b.bounds
    return a.intersection(box(left - radius, bottom - radius, right + radius, top + radius))


def seam_lower_bound(a, b, radius, seams, planar):
    left, right = box_near(a, b, radius), box_near(b, a, radius)
    if left.is_empty or right.is_empty:
        return planar
    bound, transformed = planar, {}
    for cut, params, positions, samples in seams:
        if params not in transformed:
            transformed[params] = affine_transform(left, params), affine_transform(right, params)
        first, second = transformed[params]
        al, ab, ar, at = first.bounds
        bl, bb, br, bt = second.bounds
        if not (ar < cut < bl or br < cut < al):
            continue
        low, high = max(ab, bb) - radius, min(at, bt) + radius
        i, j = np.searchsorted(positions, [low - SAMPLE_STEP_MM, high + SAMPLE_STEP_MM])
        if i == j:
            return radius
        points = samples[i:j]
        sample_bound = float(np.min(shapely.distance(first, points) + shapely.distance(second, points)))
        bound = max(bound, min(radius, sample_bound - SAMPLE_STEP_MM))
    return bound


def evidence(a, b, surface, openings):
    start, end = nearest_points(a.polygon, b.polygon)
    segment = Point(start.x, start.y) if start.equals(end) else LineString([start, end])
    return {
        "mm": start.distance(end),
        "a": a.anchor(start),
        "b": b.anchor(end),
        "crosses_voids": [opening.id for opening in openings if opening.polygon.intersection(segment).length > GEOMETRY_TOLERANCE_MM],
        "continuous_surface": bool(surface.covers(segment)),
    }


def measure(a, b, surface, openings, seams, radius, thickness=None):
    if not a or not b:
        return None
    ap, bp = [item.polygon for item in a], [item.polygon for item in b]
    tree = STRtree(bp)
    pairs, distances = tree.query_nearest(ap, return_distance=True, all_matches=False)
    minimum_index = int(np.argmin(distances))
    near = evidence(a[pairs[0, minimum_index]], b[pairs[1, minimum_index]], surface, openings)
    if thickness is None and near["continuous_surface"]:
        return {"planar_clearance": near, "creepage": {"lower_mm": near["mm"], "upper_mm": near["mm"],
                "exact": True, "path": near, "examined_pairs": 1}}
    radius = max(radius, near["mm"] + 0.1)
    matched = tree.query(ap, predicate="dwithin", distance=radius)
    bounds = []
    witness = None
    for i, j in zip(*matched):
        current = evidence(a[i], b[j], surface, openings)
        xy_bound = current["mm"]
        if current["continuous_surface"]:
            if witness is None or current["mm"] < witness["mm"]:
                witness = current
        else:
            xy_bound = seam_lower_bound(ap[i], bp[j], radius, seams, xy_bound)
        bounds.append((xy_bound, current))
    bounds.sort(key=lambda item: item[0])
    lower, limiting = bounds[0] if bounds else (radius, near)
    lower = min(lower, radius)
    if thickness is not None:
        return {"lower_mm": math.hypot(lower, thickness), "thickness_mm": thickness,
                "a": limiting["a"], "b": limiting["b"], "examined_pairs": len(bounds)}
    upper = witness["mm"] if witness else None
    return {"planar_clearance": near, "creepage": {
        "lower_mm": lower, "upper_mm": upper, "exact": upper is not None and abs(upper - lower) <= GEOMETRY_TOLERANCE_MM,
        "path": witness, "examined_pairs": len(bounds),
    }}


def select(copper, selector):
    if not isinstance(selector, dict) or not selector or set(selector) - {"nets", "refs", "pads"}:
        raise ValueError("A scope selector needs one or more of nets, refs or pads")
    filters = {}
    for key, values in selector.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"Selector {key} must be a nonempty list")
        filters[key] = {(item["ref"], str(item["pad"])) for item in values} if key == "pads" else set(values)
    rows = [
        item for item in copper
        if ("nets" not in filters or item.net in filters["nets"])
        and ("refs" not in filters or item.ref in filters["refs"])
        and ("pads" not in filters or (item.ref, item.pad) in filters["pads"])
    ]
    for key, values in filters.items():
        getter = {
            "nets": lambda item: item.net, "refs": lambda item: item.ref,
            "pads": lambda item: (item.ref, item.pad),
        }[key]
        missing = values - {getter(item) for item in rows}
        if missing:
            raise ValueError(f"Selector {key} does not match outer copper: {sorted(missing, key=str)}")
    return {layer: [item for item in rows if item.layer == layer] for layer in OUTER.values()}


def positive_number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return float(value)


def source(path):
    with path.open("rb") as stream:
        return {"filename": path.name, "sha256": sha256(stream.read()).hexdigest()}


def reconcile_openings(native, routes, route_options):
    """Require a choice when nominal fabrication geometry disagrees on one opening."""
    routed = shapely.union_all([item.polygon for item in routes])
    tolerant = routed.buffer(ALIGNMENT_TOLERANCE_MM)
    conflicts = [
        opening.id for opening in native if opening.stream == "Regions6"
        and opening.polygon.intersection(routed).area > 0.000001
        and opening.polygon.difference(tolerant).area > 0.000001
    ]
    if conflicts and route_options.get("confirmed_routes_override_cutouts") is not True:
        raise ValueError("Registered route conflicts with overlapping native Board Cutout "
                         + ", ".join(conflicts)
                         + "; confirm fabrication geometry before choosing the route override")
    return [opening for opening in native if opening.id not in conflicts] + routes, conflicts


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--pcb", type=Path, required=True)
    parser.add_argument("--gm1", type=Path)
    parser.add_argument("--scopes", type=Path, help="Scope selections, route apertures, and optional minimum board thickness")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error, shapely.GEOSException) as error:
        parser.exit(2, f"PCB geometry: {error}\n")


def run(args):
    options = json.loads(args.scopes.read_text(encoding="utf-8-sig")) if args.scopes else {}
    if not isinstance(options, dict):
        raise ValueError("The scope file must contain a JSON object")
    route_options = options.get("mechanical_route", {})
    if not isinstance(route_options, dict):
        raise ValueError("mechanical_route must be a JSON object")
    if not args.gm1 and {"apertures", "datum_matches"} & route_options.keys():
        raise ValueError("mechanical_route.apertures and datum_matches require --gm1")
    if args.gm1 and "widths_mm" in route_options:
        raise ValueError("Use mechanical_route.apertures with --gm1; widths_mm is for native-only routes")
    if args.out.resolve() in {path.resolve() for path in (args.pcb, args.gm1, args.scopes) if path}:
        raise ValueError("The output path must differ from all source input paths")
    if "minimum_board_thickness_mm" in options:
        thickness = positive_number(options["minimum_board_thickness_mm"], "minimum_board_thickness_mm")
    else:
        thickness = None
    copper, outline, native_openings, mechanical, plated_slots, stack = read_native(args.pcb)
    registration = None
    if args.gm1:
        plotted = gerber_routes(args.gm1, route_options.get("apertures"))
        if "datum_matches" in route_options:
            routes, registration = explicit_registration(plotted, mechanical, route_options["datum_matches"])
        else:
            routes, registration = register_routes(plotted, mechanical)
    else:
        widths = [positive_number(value, "mechanical route width") for value in route_options.get("widths_mm", [])]
        routes = [route for route in mechanical if any(abs(route.width - width) <= ALIGNMENT_TOLERANCE_MM for width in widths)]
        if widths and not all(any(abs(route.width - width) <= ALIGNMENT_TOLERANCE_MM for route in routes) for width in widths):
            raise ValueError("A confirmed native mechanical route width matched no Mechanical1 primitive")
    routes_openings = [
        Opening(f"gm1-{item.gm1_object}" if item.gm1_object is not None else f"mechanical1-{item.stream}-{item.index}",
                item.void, item.stream, item.index, item.offset)
        for item in routes
    ]
    openings, overridden_cutouts = reconcile_openings(native_openings, routes_openings, route_options)
    nominal_board = outline.difference(shapely.union_all([item.polygon for item in openings]))
    board = nominal_board.buffer(GEOMETRY_TOLERANCE_MM)
    by_layer = {layer: [item for item in copper if item.layer == layer] for layer in OUTER.values()}
    outlines = shapely.union_all([polygon.exterior for polygon in polygon_parts(outline)])
    board_edge = {layer: min_shape_distance(rows, outlines) for layer, rows in by_layer.items()}
    board_edge_tracks = {
        layer: min_shape_distance([row for row in rows if row.stream in ("Tracks6", "Arcs6")], outlines)
        for layer, rows in by_layer.items()
    }
    details, groups = route_report(routes, native_openings, by_layer, outline)
    seams = build_seams(board, routes, [item for item in native_openings if item.id not in overridden_cutouts])
    measurements = []
    ids = set()
    for scope in options.get("scopes", []):
        if not isinstance(scope["id"], str) or not scope["id"].strip() or scope["id"] in ids:
            raise ValueError("Scope ids must be nonempty and unique")
        ids.add(scope["id"])
        first, second = select(copper, scope["a"]), select(copper, scope["b"])
        for layer in OUTER.values():
            if {id(row) for row in first[layer]} & {id(row) for row in second[layer]}:
                raise ValueError(f"Scope {scope['id']} selects the same copper on both sides")
        threshold = positive_number(scope.get("threshold_mm", 8.3), "threshold_mm")
        radius = positive_number(scope.get("search_mm", threshold + 2), "search_mm")
        if radius < threshold:
            raise ValueError("search_mm must be at least threshold_mm")
        layers = {layer: measure(first[layer], second[layer], board, openings, seams, radius) for layer in OUTER.values()}
        cross = {"verified": thickness is not None, "top_to_bottom": None, "bottom_to_top": None}
        if thickness is not None:
            cross["top_to_bottom"] = measure(first["top"], second["bottom"], board, openings, seams, radius, thickness)
            cross["bottom_to_top"] = measure(first["bottom"], second["top"], board, openings, seams, radius, thickness)
        measurements.append({"id": scope["id"], "a": scope["a"], "b": scope["b"], "threshold_mm": threshold,
                             "layers": layers, "cross_face": cross})
    result = {
        "schema_version": 1,
        "pcb": source(args.pcb),
        "gm1": {**source(args.gm1), "registration": registration} if args.gm1 else None,
        "scopes": source(args.scopes) if args.scopes else None,
        "geometry": {
            "outline_bounds_mm": list(outline.bounds),
            "copper_layers": stack,
            "cutout_count": sum(item.stream == "Regions6" for item in native_openings),
            "unplated_drill_count": sum(item.stream == "Pads6" for item in native_openings),
            "plated_slot_count": plated_slots,
            "route_group_count": len(groups),
            "route_segment_count": len(routes),
            "route_model": "registered_gm1" if args.gm1 else "selected_native" if routes else "native_cutouts_only",
            "mechanical_route_selection_requires_review": bool(mechanical and not routes),
            "overridden_native_cutout_ids": overridden_cutouts,
            "route_groups": groups,
            "mechanical_routes": details,
            "native_mechanical1_width_candidates_mm": dict(Counter(round(item.width, 4) for item in mechanical)),
            "nets": sorted({item.net for item in copper if item.net}),
            "copper_primitive_counts": {layer: dict(Counter(item.stream for item in rows)) for layer, rows in by_layer.items()},
        },
        "board_edge": board_edge,
        "board_edge_tracks": board_edge_tracks,
        "measurements": measurements,
        "limits": ["Nominal outer-layer PCB geometry and polygonal curve approximations.",
                   "Component bodies, solder geometry, coating, and enclosure are outside this model.",
                   "Opposite-face results are projected board-surface lower bounds, not measured 3D paths."],
    }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
