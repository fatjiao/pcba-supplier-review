"""Read an approved XLSX and prepare native Feishu spreadsheet payloads."""

from colorsys import hls_to_rgb, rgb_to_hls
import csv
from datetime import date, datetime, time, timedelta
import math
from pathlib import Path
from xml.etree import ElementTree

from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles.colors import COLOR_INDEX
from openpyxl.utils.cell import column_index_from_string, get_column_letter
from openpyxl.utils.datetime import WINDOWS_EPOCH, to_excel


THEME_KEYS = ('lt1', 'dk1', 'lt2', 'dk2', 'accent1', 'accent2', 'accent3',
              'accent4', 'accent5', 'accent6', 'hlink', 'folHlink')
THEME_NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
BORDER_STYLES = {
    'hair': ('solid', 'thin'), 'thin': ('solid', 'thin'), 'medium': ('solid', 'medium'),
    'thick': ('solid', 'thick'), 'double': ('double', 'medium'), 'dotted': ('dotted', 'thin'),
    'dashed': ('dashed', 'thin'), 'mediumDashed': ('dashed', 'medium'),
    'dashDot': ('dashed', 'thin'), 'mediumDashDot': ('dashed', 'medium'),
    'dashDotDot': ('dashed', 'thin'), 'mediumDashDotDot': ('dashed', 'medium'),
    'slantDashDot': ('dashed', 'medium'),
}
VERIFY_ROWS = 40


def _range(first_row, first_col, last_row, last_col):
    return f'{get_column_letter(first_col)}{first_row}:{get_column_letter(last_col)}{last_row}'


def _area(first_row, first_col, last_row, last_col):
    if (first_row, first_col) == (last_row, last_col):
        return f'{get_column_letter(first_col)}{first_row}'
    return _range(first_row, first_col, last_row, last_col)


def _theme_colors(theme):
    if not theme:
        return {}
    scheme = ElementTree.fromstring(theme).find('.//a:clrScheme', THEME_NS)
    if scheme is None:
        return {}
    colors = {}
    for index, key in enumerate(THEME_KEYS):
        entry = scheme.find(f'a:{key}', THEME_NS)
        if entry is not None and len(entry):
            colors[index] = entry[0].get('lastClr') or entry[0].get('val')
    return colors


def _color(color, theme):
    if color is None:
        return None
    if color.type == 'rgb':
        value = color.rgb[-6:]
    elif color.type == 'theme':
        value = theme.get(color.theme)
    elif color.type == 'indexed' and 0 <= color.indexed < len(COLOR_INDEX):
        value = COLOR_INDEX[color.indexed][-6:]
    else:
        return None
    if value is None:
        return None
    if color.tint:
        channels = tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
        hue, lightness, saturation = rgb_to_hls(*channels)
        tint = color.tint
        lightness = lightness * (1 + tint) if tint < 0 else lightness + (1 - lightness) * tint
        value = ''.join(f'{round(channel * 255):02X}' for channel in hls_to_rgb(hue, lightness, saturation))
    return '#' + value.upper()


def _solid_fill(fill, theme, *, differential=False):
    if fill is None or getattr(fill, 'patternType', None) != 'solid':
        return None
    # Excel conditional rules commonly store their solid color as bgColor and
    # openpyxl supplies a default black fgColor when the field was omitted.
    if differential and fill.bgColor.value != '00000000':
        return _color(fill.bgColor, theme) or _color(fill.fgColor, theme)
    return _color(fill.fgColor, theme)


def _cell_styles(cell, theme):
    style = {}
    font = cell.font
    for field, value in (
        ('font_family', font.name), ('font_size', font.sz), ('font_color', _color(font.color, theme)),
        ('font_weight', 'bold' if font.bold else None), ('font_style', 'italic' if font.italic else None),
        ('background_color', _solid_fill(cell.fill, theme)),
        ('number_format', cell.number_format if cell.number_format != 'General' else None),
    ):
        if value is not None:
            style[field] = value
    if font.strike:
        style['font_line'] = 'line-through'
    elif font.underline and font.underline != 'none':
        style['font_line'] = 'underline'
    alignment = cell.alignment
    if alignment.horizontal in ('left', 'center', 'right'):
        style['horizontal_alignment'] = alignment.horizontal
    if alignment.vertical in ('top', 'center', 'bottom'):
        style['vertical_alignment'] = 'middle' if alignment.vertical == 'center' else alignment.vertical
    if alignment.wrap_text:
        style['word_wrap'] = 'auto-wrap'
    borders = {}
    for direction in ('top', 'bottom', 'left', 'right'):
        side = getattr(cell.border, direction)
        if side and side.style in BORDER_STYLES:
            line, weight = BORDER_STYLES[side.style]
            borders[direction] = {'style': line, 'weight': weight,
                                  'color': _color(side.color, theme) or '#000000'}
    if borders:
        style['border_styles'] = borders
    return style


def _rectangles(cells):
    """Group consecutive cells of the same kind with a rectangular extent."""
    remaining = set(cells)
    for row, col in sorted(cells):
        if (row, col) not in remaining:
            continue
        marker = cells[row, col]
        end_col = col
        while (row, end_col + 1) in remaining and cells[row, end_col + 1] == marker:
            end_col += 1
        end_row = row
        column_span = range(col, end_col + 1)
        while all((end_row + 1, c) in remaining and cells[end_row + 1, c] == marker for c in column_span):
            end_row += 1
        remaining.difference_update((r, c) for r in range(row, end_row + 1) for c in column_span)
        yield row, col, end_row, end_col, marker


def _sizes(dimensions, label):
    spans = []
    for index, pixels in sorted(dimensions.items()):
        if spans and spans[-1][1] == index - 1 and spans[-1][2] == pixels:
            spans[-1][1] = index
        else:
            spans.append([index, index, pixels])
    return [{'range': f'{label(first)}:{label(last)}', 'size': pixels} for first, last, pixels in spans]


def _cell_payload(cell):
    value = cell.value
    if cell.data_type == 'f':
        if not isinstance(value, str):
            raise ValueError(f'{cell.parent.title}!{cell.coordinate} 使用数组或数据表公式，不能按普通公式迁移。')
        return {'formula': value}
    if isinstance(value, (datetime, date, time, timedelta)):
        # Feishu/Excel use the Windows date serial. This also preserves dates
        # decoded from books that use the Mac 1904 epoch.
        return {'value': to_excel(value, WINDOWS_EPOCH)}
    if cell.data_type == 'e':
        raise ValueError(f'{cell.parent.title}!{cell.coordinate} 含 Excel 错误值 {value}，请在源工作簿修复。')
    if type(value) not in (str, bool, int, float) or (type(value) is float and not math.isfinite(value)):
        raise ValueError(f'{cell.parent.title}!{cell.coordinate} 含无法迁移的值类型 {type(value).__name__}。')
    # Retain every stored character, including a literal apostrophe. A text
    # cell beginning with '=' is passed as a value, never as a formula.
    return {'value': value}


def _styles(sheet, cells, theme, warnings):
    merges = sorted(sheet.merged_cells.ranges, key=lambda m: (m.min_row, m.min_col))
    merged_anchors = {(m.min_row, m.min_col): m for m in merges}
    cell_styles = {}
    style_ids = {}
    for cell in cells:
        if not cell.has_style or isinstance(cell, MergedCell):
            continue
        marker = cell.style_id
        if marker not in style_ids:
            style_ids[marker] = _cell_styles(cell, theme)
        if not style_ids[marker]:
            continue
        area = merged_anchors.get((cell.row, cell.column))
        if area is None:
            cell_styles[cell.row, cell.column] = marker
        else:
            cell_styles.update({(r, c): marker for r in range(area.min_row, area.max_row + 1)
                               for c in range(area.min_col, area.max_col + 1)})

    widths = {}
    for dimension in sheet.column_dimensions.values():
        if dimension.width is None:
            continue
        first = dimension.min or column_index_from_string(dimension.index)
        last = dimension.max or first
        for col in range(first, last + 1):
            widths[col] = max(1, round(dimension.width * 7 + 5))
    heights = {row: max(1, round(dimension.height * 4 / 3)) for row, dimension in sheet.row_dimensions.items()
               if dimension.height is not None}
    if any(dim.hidden for dim in (*sheet.row_dimensions.values(), *sheet.column_dimensions.values())):
        warnings.append('源工作表有隐藏的行或列。样式载荷保留尺寸，隐藏状态需单独设置。')

    style = {'name': sheet.title}
    parts = {
        'cell_styles': [{'range': _area(r, c, er, ec), **style_ids[marker]}
                        for r, c, er, ec, marker in _rectangles(cell_styles)],
        'cell_merges': [{'range': str(area)} for area in merges],
        'row_sizes': _sizes(heights, str), 'col_sizes': _sizes(widths, get_column_letter),
    }
    style.update({key: value for key, value in parts.items() if value})
    pane = sheet.sheet_view.pane
    if pane and pane.state in ('frozen', 'frozenSplit'):
        style['freeze'] = {'rows': int(pane.ySplit or 0), 'cols': int(pane.xSplit or 0)}
    elif pane:
        warnings.append('源工作表使用拆分视图，需要在飞书中单独配置。')
    return style


def _filter_range(sheet, warnings):
    filters = [sheet.auto_filter, *(table.autoFilter for table in sheet.tables.values())]
    active = [item for item in filters if item is not None and item.ref]
    ranges = list(dict.fromkeys(item.ref for item in active))
    if len(ranges) > 1:
        warnings.append(f'飞书每张工作表使用一个普通筛选。保留 {ranges[0]}，另外的筛选范围为 {", ".join(ranges[1:])}。')
    if any(item.filterColumn for item in active):
        warnings.append('源表已有筛选条件，载荷保留筛选范围，条件需用飞书筛选重新设定。')
    return ranges[0] if ranges else None


def _conditional_style(differential, theme):
    style = {}
    background = _solid_fill(differential.fill, theme, differential=True)
    if background:
        style['back_color'] = background
    font = differential.font
    if font is not None:
        color = _color(font.color, theme)
        if color:
            style['fore_color'] = color
        variants = [value for value, enabled in (('bold', font.bold), ('italic', font.italic)) if enabled]
        if variants:
            style['font'] = ' '.join(variants)
        decorations = [value for value, enabled in (('underline', font.underline), ('strikethrough', font.strike)) if enabled]
        if decorations:
            style['text_decoration'] = '_'.join(decorations)
    return style


def _conditional_rules(sheet, theme, warnings):
    rules = []
    for definition in sheet.conditional_formatting:
        ranges = [str(area) for area in sorted(definition.sqref.ranges, key=lambda m: (m.min_row, m.min_col))]
        for rule in sheet.conditional_formatting[definition]:
            label = f'条件格式 {", ".join(ranges)}（规则 {rule.priority}）'
            if rule.type != 'expression' or len(rule.formula or []) != 1 or not rule.formula[0]:
                warnings.append(f'{label}的 {rule.type} 类型不能转换为已支持的公式高亮。')
                continue
            differential = rule.dxf
            if differential is None and rule.dxfId is not None:
                differential = sheet.parent._differential_styles.styles[rule.dxfId]
            style = _conditional_style(differential, theme) if differential is not None else {}
            if not style:
                warnings.append(f'{label}没有能转换的背景或字体格式。')
                continue
            if rule.stopIfTrue:
                warnings.append(f'{label}要求命中即停止，飞书条件格式载荷不表达此设置。')
            if any(getattr(differential, key) is not None for key in ('alignment', 'border', 'numFmt', 'protection')):
                warnings.append(f'{label}的边框、数字格式、对齐或保护需单独设置。')
            if differential.fill is not None and 'back_color' not in style:
                warnings.append(f'{label}的填色类型需单独配置。')
            formula = rule.formula[0]
            properties = {'attrs': [{'formula': [formula if formula.startswith('=') else '=' + formula]}],
                          'style': style}
            rules.append((rule.priority, {'ranges': ranges, 'properties': properties}))
    return [item for _, item in sorted(rules, key=lambda pair: pair[0])]


def _dropdowns(sheet, warnings):
    dropdowns = []
    for validation in sheet.data_validations.dataValidation:
        areas = sorted(validation.sqref.ranges, key=lambda m: (m.min_row, m.min_col))
        formula = validation.formula1 or ''
        if validation.type != 'list' or len(formula) < 2 or not (formula.startswith('"') and formula.endswith('"')):
            warnings.append(f'数据验证 {validation.sqref} 使用非内联列表，需单独迁移。')
            continue
        try:
            options = next(csv.reader([formula[1:-1]], strict=True))
        except csv.Error:
            options = []
        if not options or any(not option for option in options):
            warnings.append(f'数据验证 {validation.sqref} 的选项为空或无法解析。')
            continue
        dropdowns.extend({'range': str(area), 'options': options} for area in areas)
    return dropdowns


def read_workbook(path: Path) -> list[dict]:
    """Return typed cell data and a native Feishu presentation spec for each sheet."""
    workbook = load_workbook(path, data_only=False, keep_links=False)
    try:
        theme = _theme_colors(workbook.loaded_theme)
        sheets = []
        for sheet in workbook.worksheets:
            warnings = []
            if sheet.sheet_state != 'visible':
                raise ValueError(f'{sheet.title} 为隐藏工作表。请在本机准备仅包含可交付页签的审核工作簿。')
            # Inspect the sparse cell map so formatting-only rows and columns do
            # not expand the value rectangle or materialize an oversized grid.
            cells = list(sheet._cells.values())
            values = {(cell.row, cell.column): _cell_payload(cell) for cell in cells if cell.value is not None}
            height = max((row for row, _ in values), default=1)
            width = max((col for _, col in values), default=1)
            matrix = [[values.get((row, col), {}) for col in range(1, width + 1)] for row in range(1, height + 1)]
            formulas = {coord: True for coord, data in values.items() if 'formula' in data}
            formula_ranges = [_area(start, col, min(start + VERIFY_ROWS - 1, last_row), last_col)
                              for row, col, last_row, last_col, _ in _rectangles(formulas)
                              for start in range(row, last_row + 1, VERIFY_ROWS)]
            sheets.append({
                'name': sheet.title, 'range': _range(1, 1, height, width), 'cells': matrix,
                'styles': _styles(sheet, cells, theme, warnings), 'filter_range': _filter_range(sheet, warnings),
                'conditionals': _conditional_rules(sheet, theme, warnings), 'dropdowns': _dropdowns(sheet, warnings),
                'formula_ranges': formula_ranges, 'image_count': len(sheet._images), 'warnings': warnings,
            })
        return sheets
    finally:
        workbook.close()
