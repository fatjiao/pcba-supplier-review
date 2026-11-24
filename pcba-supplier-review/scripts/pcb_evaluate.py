"""Apply the company PCB checklist to exact CAD distances or proven bounds."""

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path


DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / 'references' / 'pcb-safety-rules.json'
BRANCH_KEYS = ('market', 'supply', 'pollution_degree')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def load_rule_catalog(path=DEFAULT_CATALOG):
    catalog = read_json(path)
    if catalog.get('schema_version') != 1 or not isinstance(catalog.get('rules'), dict):
        raise ValueError('PCB 检查表规则文件的格式版本或 rules 字段无效。')
    return catalog


def quantity(value, label):
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError(f'{label} 必须是非负数。')
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or not math.isfinite(float(number)):
        raise ValueError(f'{label} 必须是有限的非负数。')
    return number


def thresholds(rule, voltage):
    limit = rule['limit']
    if isinstance(limit, dict):
        if voltage is None:
            return None, None
        threshold = quantity(limit['threshold_vrms'], '限值基准电压')
        base = quantity(limit['base_mm'], '基准限值')
        rise = quantity(limit['rise_mm'], '限值增量')
        interval = quantity(limit['interval_vrms'], '限值电压间隔')
        if not interval:
            raise ValueError('限值电压间隔必须大于零。')
        limit_mm = base + max(Decimal(0), voltage - threshold) * rise / interval
    else:
        limit_mm = quantity(limit, '安规底线 J')
    target = rule['recommended']
    if isinstance(target, dict):
        target_mm = limit_mm + quantity(target['add_to_limit_mm'], '公司推荐余量')
    else:
        target_mm = quantity(target, '公司推荐值 K')
    if target_mm < limit_mm:
        raise ValueError('公司推荐值 K 低于安规底线 J，请复核规则文件。')
    return limit_mm, target_mm


def interval(measurement, row_id):
    exact = quantity(measurement.get('measurement_mm'), f'{row_id} 实测距离')
    lower = quantity(measurement.get('lower_bound_mm'), f'{row_id} 距离下界')
    upper = quantity(measurement.get('upper_bound_mm'), f'{row_id} 距离上界')
    if exact is not None:
        if lower is not None or upper is not None:
            raise ValueError(f'{row_id} 请提供实测值或区间其中一种。')
        return exact, exact, True
    if lower is not None and upper is not None and lower > upper:
        raise ValueError(f'{row_id} 距离下界大于上界。')
    return lower, upper, False


def number_or_blank(value):
    return float(value) if value is not None else None


def format_mm(value):
    return format(value, '.12g') + ' mm'


def compare_bounds(low, high, limit, target):
    if high is not None and high < limit:
        return 'NG', False, '距离低于安规底线 J。', high, 'upper'
    if low is None or low < limit:
        return 'NG', False, '缺少满足安规底线 J 的距离证据。', low if low is not None else high, 'lower' if low is not None else 'upper'
    if low >= target:
        return 'OK', False, '达到安规底线 J 和公司推荐值 K。', low, 'lower'
    if high is not None and high < target:
        return 'OK', True, '达到安规底线 J，低于公司推荐值 K。', low, 'lower'
    return 'OK', False, '距离下界达到安规底线 J。公司推荐值 K 尚待复核。', low, 'lower'


def operating_voltage_matches(rule, voltage):
    band = rule.get('working_vrms_band')
    if band is None:
        return True
    if voltage is None:
        return False
    lower = band.get('lower_inclusive', 0)
    upper = band.get('upper_exclusive')
    return lower <= voltage and (upper is None or voltage < upper)


def row_disposition(item, rule, branch, evidence, voltage, low, high, limit, target):
    applicable = item.get('applicable')
    if applicable is not None and type(applicable) is not bool:
        raise ValueError(f'{item["id"]} applicable 必须是 true、false 或 null。')
    if not rule:
        return 'NG', False, '未映射到公司检查表条目。', None, ''
    if any(branch.get(key) is not None and branch[key] != rule[key] for key in BRANCH_KEYS):
        return 'NG', False, '所选条目与报告的市场、电源方案或污染等级不符。', None, ''
    if applicable is False and evidence:
        return 'NC', False, '确认不适用。', None, ''
    if applicable is not True:
        return 'NG', False, '缺少条款适用性证据。', None, ''
    if not operating_voltage_matches(rule, voltage):
        return 'NG', False, '工作电压缺失或不在该条款的电压分段内。', None, ''
    if limit is None:
        return 'NG', False, '原表按工作电压计算限值，当前缺少工作电压 Vrms。', None, ''
    if not item.get('location'):
        return 'NG', False, '缺少量测对象及定位。', None, ''
    if rule.get('assembly_evidence') and item.get('measurement_basis') != 'assembly':
        return 'NG', False, '本条款需要可回查的整机或装配量测。', None, ''
    return compare_bounds(low, high, limit, target)


def evaluate_row(item, rules, branch=None):
    row_id = item['id']
    if not isinstance(row_id, str) or not row_id.strip():
        raise ValueError('每个 PCB 检查项必须有非空的文本 id。')
    rule_row = item.get('rule_row')
    rule = rules.get(str(rule_row)) if rule_row is not None else None
    evidence = str(item.get('evidence') or item.get('note') or '').strip()
    voltage = quantity(item.get('working_vrms'), f'{row_id} 工作电压 Vrms')
    low, high, exact = interval(item, row_id)
    limit, target = thresholds(rule, voltage) if rule else (None, None)
    status, amber, reason, measured, bound = row_disposition(
        item, rule, branch or {}, evidence, voltage, low, high, limit, target
    )
    if low is not None and high is not None and not exact:
        reason += f'可复核区间为 {format_mm(low)} 至 {format_mm(high)}。'
    if rule and rule.get('source_note'):
        reason += rule['source_note']
    if evidence:
        reason += f' {evidence}'
    criterion = rule['criterion'] if rule else '待映射公司检查项'
    if rule:
        reference = f"J{rule_row}:K{rule_row}，{rule['enforcement']}，{rule['market']} / {rule['supply']} / 污染等级 {rule['pollution_degree']}"
    else:
        reference = f'未找到公司检查表行 {rule_row}' if rule_row is not None else '未映射公司检查表'
    comparable = measured is not None and limit is not None
    return {
        **item,
        'boundary': item.get('boundary') or criterion,
        'metric': item.get('metric') or (rule['metric'] if rule else '待核对'),
        'location': item.get('location') or '未提供定位',
        'vrms': number_or_blank(voltage),
        'limit': number_or_blank(limit),
        'target': number_or_blank(target),
        'measured': number_or_blank(measured),
        'status': status,
        'amber': amber,
        'note': reason,
        'rule': reference,
        'lowerBound': comparable and not exact and bound == 'lower',
        'upperBound': comparable and not exact and bound == 'upper',
        'comparable': comparable,
        'marginAllowed': comparable and exact,
    }


def evaluate_document(document, rule_catalog):
    """Return workbook-ready pcbSafety rows while keeping other report sections intact."""
    safety = document['pcbSafety']
    branch = {key: safety.get(key) for key in BRANCH_KEYS}
    rows = [evaluate_row(row, rule_catalog['rules'], branch) for row in safety['rows']]
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError('PCB 检查项 id 有重复。')
    source = rule_catalog['source']
    citation = f"{source['workbook']} / 第 {source['tab_index']} 个工作表「{source['sheet']}」 / {source['range']}"
    sources = list(dict.fromkeys([citation, *safety.get('sources', [])]))
    return {**document, 'pcbSafety': {**safety, 'sources': sources, 'rows': rows}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='待判定的 PCB 距离记录 JSON')
    parser.add_argument('output', type=Path, help='供 Excel 生成器使用的报告 JSON')
    parser.add_argument('--rules', type=Path, default=DEFAULT_CATALOG, help='覆盖包内公司规则文件')
    args = parser.parse_args()
    try:
        result = evaluate_document(read_json(args.input), load_rule_catalog(args.rules))
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    except (OSError, TypeError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
