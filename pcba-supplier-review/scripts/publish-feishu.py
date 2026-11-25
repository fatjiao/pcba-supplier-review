#!/usr/bin/env python3
"""Publish an audited Excel report as a native Feishu spreadsheet."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import quote
from zipfile import BadZipFile


def json_text(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def find_cli(override):
    candidate = override or shutil.which("lark-cli.exe" if os.name == "nt" else "lark-cli")
    if not candidate and os.name == "nt":
        shim = shutil.which("lark-cli.cmd")
        if shim:
            candidate = Path(shim).parent / "node_modules/@larksuite/cli/bin/lark-cli.exe"
        if not candidate or not Path(candidate).is_file():
            local = os.environ.get("LOCALAPPDATA")
            if local:
                candidate = Path(local) / "Programs/lark-cli/lark-cli.exe"
    if not candidate or not Path(candidate).is_file():
        raise ValueError("找不到飞书 CLI。请按 README 安装，或用 --cli 指定可执行文件。")
    executable = Path(candidate).resolve()
    if os.name == "nt" and executable.suffix.lower() != ".exe":
        raise ValueError("Windows 上请用 --cli 指定 lark-cli.exe。")
    return str(executable)


class CliError(ValueError):
    def __init__(self, operation, result, fallback):
        self.data = result.get("data", {})
        error = result.get("error", {})
        message = error if isinstance(error, str) else (error or {}).get("message") or (error or {}).get("msg")
        code = error.get("code") if isinstance(error, dict) else None
        detail = message or fallback or "CLI 未返回成功结果"
        super().__init__(f"{operation} 失败（{code or 'CLI'}）{detail[:400]}")


class Feishu:
    def __init__(self, cli):
        self.cli = cli

    def call(self, operation, *args, payload=None):
        return self.run("sheets", operation, *args, payload=payload)

    def values(self, spreadsheet_token, sheet_id, area):
        token = quote(spreadsheet_token, safe="")
        region = quote(f"{sheet_id}!{area}", safe="!:")
        response = self.run("api", "GET", f"/open-apis/sheets/v2/spreadsheets/{token}/values/{region}",
                            "--params", json_text({"valueRenderOption": "UnformattedValue"}))
        return response["valueRange"]

    def run(self, domain, operation, *args, payload=None):
        completed = subprocess.run(
            [self.cli, domain, operation, *args, "--as", "bot", "--format", "json"],
            input=json_text(payload) if payload is not None else "",
            capture_output=True, encoding="utf-8", timeout=180, check=False,
        )
        result = {}
        for stream in (completed.stdout, completed.stderr):
            try:
                candidate = json.loads(stream)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "identity" in candidate:
                result = candidate
                break
        if result.get("identity") != "bot":
            raise CliError(operation, {}, "CLI 未确认本次操作使用应用身份。")
        if completed.returncode or result.get("ok") is not True or result.get("dry_run"):
            raise CliError(operation, result, f"退出码 {completed.returncode}")
        return result["data"]


@contextmanager
def lock_state(path):
    with path.with_suffix(path.suffix + ".lock").open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise ValueError("同一状态文件已有发布任务运行。") from error
        else:
            import fcntl

            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("同一状态文件已有发布任务运行。") from error
        yield


class Receipt:
    def __init__(self, path, fingerprint):
        self.path = path
        self.data = {"fingerprint": fingerprint, "identity": "bot", "finished": []}
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("fingerprint") != fingerprint or existing.get("identity") != "bot":
                raise ValueError("状态文件对应其他报告、标题或目标文件夹。请为这次发布指定新的状态文件。")
            self.data = existing

    def save(self):
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json_text(self.data) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def step(self, name, action):
        if name in self.data["finished"]:
            return
        action()
        self.data["finished"].append(name)
        self.save()


def check_batch(result, count=None):
    total = result.get("total") if count is None else count
    if not total or result.get("failed") != 0 or result.get("succeeded") != total:
        raise ValueError(f"飞书批量操作只完成 {result.get('succeeded', 0)}/{total} 项。")


def create_workbook(api, receipt, title, folder, sheets):
    if not receipt.data.get("spreadsheet_token"):
        if receipt.data.get("creation_started"):
            raise ValueError("上次创建未取得表格凭证。请在应用云空间核对同名表格，再处理本地状态。")
        seed = []
        for sheet in sheets:
            first = sheet["cells"][0][0].get("value")
            seed.append({"name": sheet["name"], "header": False, "columns": ["A"],
                         "data": [[first if isinstance(first, str) else ""]]})
        args = ["--title", title, "--sheets", "-"]
        if folder:
            args.extend(["--folder-token", folder])
        receipt.data["creation_started"] = True
        receipt.save()
        try:
            result = api.call("+workbook-create", *args, payload={"sheets": seed})
        except CliError as error:
            # A failed seed write can still return the new document's locator.
            save_locator(receipt, error.data)
            raise
        if not save_locator(receipt, result):
            raise ValueError("飞书创建响应缺少表格 token。")

    if not receipt.data.get("url"):
        params = json_text({"spreadsheet_token": receipt.data["spreadsheet_token"]})
        result = api.call("spreadsheets", "get", "--params", params)
        if not save_locator(receipt, result) or not receipt.data.get("url"):
            raise ValueError("已取得飞书表格 token，暂未取到可交付的链接。")


def save_locator(receipt, result):
    book = (result or {}).get("spreadsheet", result or {})
    token = book.get("spreadsheet_token") or book.get("token")
    if not token:
        return False
    receipt.data["spreadsheet_token"] = token
    if book.get("url"):
        receipt.data["url"] = book["url"]
    receipt.save()
    return True


def styles_batches(styles):
    # Keep merges separate so a retry can check the server's merge count first.
    entries = [(key, item) for key in ("cell_styles", "row_sizes", "col_sizes")
               for item in styles.get(key, [])]
    for index in range(0, len(entries), 80):
        batch = {"name": styles["name"]}
        part = entries[index:index + 80]
        for key, item in part:
            batch.setdefault(key, []).append(item)
        yield batch
    if styles.get("freeze"):
        yield {"name": styles["name"], "freeze": styles["freeze"]}


def value_blocks(sheet):
    last_column = sheet["range"].split(":")[-1].rstrip("0123456789")
    rows = sheet["cells"]
    for offset in range(0, len(rows), 30):
        block = rows[offset:offset + 30]
        yield f"A{offset + 1}:{last_column}{offset + len(block)}", block


def comparison_range(value):
    address = value.rsplit("!", 1)[-1].replace("$", "")
    start, _, end = address.partition(":")
    return f"{start}:{end or start}"


def strings(data):
    if isinstance(data, str):
        yield data
    elif isinstance(data, dict):
        for value in data.values():
            yield from strings(value)
    elif isinstance(data, list):
        for value in data:
            yield from strings(value)


def matches_rule(data, rule):
    source_ranges = {comparison_range(area) for area in rule["ranges"]}
    source = rule["properties"]
    details = (item["details"] for page in data.get("sheets", [])
               for item in page.get("conditional_formats", []))
    for item in details:
        if item["rule_type"] != "expression" or source_ranges != {comparison_range(area) for area in item["ranges"]}:
            continue
        actual_style = item.get("style", {})
        if (item.get("attrs") == source["attrs"]
                and all(str(actual_style.get(key)).lower() == str(value).lower()
                        for key, value in source["style"].items())):
            return True
    return False


def layout(api, target):
    result = api.call("+sheet-info", *target, "--include", "merges,frozen")
    return result.get("sheet", result)


def merged_ranges(items):
    return {comparison_range(item["range"]) for item in items}


def format_sheet(api, receipt, locator, sheet, sheet_id):
    name = sheet["name"]
    target = (*locator, "--sheet-id", sheet_id)
    styles = sheet["styles"]
    for index, batch in enumerate(styles_batches(styles)):
        receipt.step(f"{name}:styles:{index}", lambda: check_batch(api.call(
            "+styles-put", *locator, "--styles", "-", payload={"styles": [batch]},
        )))
    merges = styles.get("cell_merges", [])
    if merges:
        def apply_merges():
            actual = merged_ranges(layout(api, target).get("merged_cells", []))
            if actual - merged_ranges(merges):
                raise ValueError(f"{name} 存在 Excel 中没有的合并区域。请先核对飞书表格和发布状态。")
            missing = [item for item in merges if comparison_range(item["range"]) not in actual]
            for offset in range(0, len(missing), 80):
                part = missing[offset:offset + 80]
                batch = {"name": name, "cell_merges": part}
                check_batch(api.call("+styles-put", *locator, "--styles", "-", payload={"styles": [batch]}), len(part))
        receipt.step(f"{name}:merges", apply_merges)

    if sheet["filter_range"]:
        area = sheet["filter_range"]

        def apply_filter():
            listed = api.call("+filter-list", *target)
            if comparison_range(area) not in {comparison_range(value) for value in strings(listed)}:
                api.call("+filter-create", *target, "--range", area)
        receipt.step(f"{name}:filter", apply_filter)

    if sheet["conditionals"] and f"{name}:conditions" not in receipt.data["finished"]:
        existing = api.call("+cond-format-list", *target)
        for index, rule in enumerate(sheet["conditionals"]):
            if not matches_rule(existing, rule):
                receipt.step(f"{name}:condition:{index}", lambda: api.call(
                    "+cond-format-create", *target, "--rule-type", "expression",
                    "--ranges", json_text(rule["ranges"]), "--properties", "-", payload=rule["properties"],
                ))
        receipt.step(f"{name}:conditions", lambda: None)

    for index, dropdown in enumerate(sheet["dropdowns"]):
        receipt.step(f"{name}:dropdown:{index}", lambda: api.call(
            "+dropdown-set", *target, "--range", dropdown["range"], "--options", "-",
            "--highlight=false", payload=dropdown["options"],
        ))


def equivalent(expected, actual):
    if expected in (None, ""):
        return actual in (None, "")
    if isinstance(expected, bool):
        return expected is actual
    if isinstance(expected, (int, float)):
        return type(actual) in (int, float) and math.isclose(expected, actual, rel_tol=1e-12, abs_tol=1e-12)
    return expected == actual


def verify_sheet(api, locator, sheet, remote):
    name = sheet["name"]
    target = (*locator, "--sheet-id", remote["sheet_id"])
    freeze = sheet["styles"].get("freeze", {})
    expected_merges = merged_ranges(sheet["styles"].get("cell_merges", []))
    if expected_merges or freeze:
        sheet_layout = layout(api, target)
        if merged_ranges(sheet_layout.get("merged_cells", [])) != expected_merges:
            raise ValueError(f"{name} 的合并区域与 Excel 不一致。")
        remote = {**remote, **sheet_layout}
    for axis, aliases in (("rows", ("frozen_rows", "frozen_row_count")),
                          ("cols", ("frozen_cols", "frozen_columns", "frozen_column_count"))):
        actual = next((remote[key] for key in aliases if key in remote), 0)
        if actual != freeze.get(axis, 0):
            raise ValueError(f"{name} 的冻结范围与 Excel 不一致。")
    for area, expected_rows in value_blocks(sheet):
        data = api.values(locator[1], remote["sheet_id"], area)
        actual_rows = data.get("values") or []
        if len(actual_rows) > len(expected_rows) or (data.get("range") and comparison_range(data["range"]) != area):
            raise ValueError(f"{name} {area} 回读范围不符。")
        for row_index, expected_row in enumerate(expected_rows):
            actual_row = actual_rows[row_index] if row_index < len(actual_rows) else []
            actual_row = actual_row or []
            for col_index, expected in enumerate(expected_row):
                actual = actual_row[col_index] if col_index < len(actual_row) else None
                if "formula" not in expected and not equivalent(expected.get("value"), actual):
                    raise ValueError(f"{name} {area} 第 {row_index + 1} 行第 {col_index + 1} 列内容不符。")

    if sheet["formula_ranges"]:
        for area in sheet["formula_ranges"]:
            verify_formulas(api, target, sheet, area)
            status = api.call("+formula-verify", *target, "--range", area, "--exit-on-error")
            if status.get("status") != "success" or status.get("has_more"):
                raise ValueError(f"{name} {area} 的公式未通过回读。")
    if sheet["filter_range"]:
        filters = api.call("+filter-list", *target)
        if comparison_range(sheet["filter_range"]) not in {comparison_range(value) for value in strings(filters)}:
            raise ValueError(f"{name} 缺少预期的筛选范围。")
    if sheet["conditionals"]:
        rules = api.call("+cond-format-list", *target)
        if any(not matches_rule(rules, rule) for rule in sheet["conditionals"]):
            raise ValueError(f"{name} 的条件高亮规则不完整。")
    if sheet["dropdowns"]:
        for item in sheet["dropdowns"]:
            result = api.call("+dropdown-get", *target, "--range", item["range"].split(":")[0])
            if not set(item["options"]).issubset(strings(result)):
                raise ValueError(f"{name} {item['range']} 的下拉选项不完整。")


def verify_formulas(api, target, sheet, area):
    from openpyxl.utils.cell import column_index_from_string, range_boundaries

    result = api.call("+cells-get", *target, "--range", area, "--include", "formula")
    ranges = result.get("ranges", [])
    if result.get("has_more") or len(ranges) != 1 or ranges[0].get("truncated"):
        raise ValueError(f"{sheet['name']} {area} 公式回读不完整。")
    block = ranges[0]
    actual = {(r, column_index_from_string(c)): item.get("formula", "")
              for r, row in zip(block["row_indices"], block["cells"])
              for c, item in zip(block["col_indices"], row)}
    min_col, min_row, max_col, max_row = range_boundaries(area)
    for row in range(min_row, max_row + 1):
        for col in range(min_col, max_col + 1):
            expected = sheet["cells"][row - 1][col - 1].get("formula")
            if expected and expected.lstrip("=") != (actual.get((row, col)) or "").lstrip("="):
                raise ValueError(f"{sheet['name']} 第 {row} 行第 {col} 列的公式不符。")


def publish(api, receipt, title, folder, sheets):
    create_workbook(api, receipt, title, folder, sheets)
    if receipt.data.get("complete"):
        return
    locator = ("--spreadsheet-token", receipt.data["spreadsheet_token"])
    cloud_sheets = api.call("+workbook-info", *locator)["sheets"]
    if [item.get("sheet_name") or item.get("title") for item in cloud_sheets] != [sheet["name"] for sheet in sheets]:
        raise ValueError("在线工作表名称或顺序与 Excel 不一致。请核对已创建的表格。")
    for sheet, cloud in zip(sheets, cloud_sheets):
        name = sheet["name"]
        for index, (area, block) in enumerate(value_blocks(sheet)):
            receipt.step(f"{name}:values:{index}", lambda: check_batch(api.call(
                "+cells-set", *locator, "--writes", "-",
                payload=[{"sheet_id": cloud["sheet_id"], "range": area, "cells": block}],
            ), 1))
        format_sheet(api, receipt, locator, sheet, cloud["sheet_id"])
        print(f"飞书表格已写入 {name}", file=sys.stderr, flush=True)

    cloud_sheets = api.call("+workbook-info", *locator)["sheets"]
    if [item.get("sheet_name") or item.get("title") for item in cloud_sheets] != [sheet["name"] for sheet in sheets]:
        raise ValueError("回读时在线工作表名称或顺序已变化。")
    for sheet, cloud in zip(sheets, cloud_sheets):
        verify_sheet(api, locator, sheet, cloud)
    receipt.data["complete"] = True
    receipt.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path, help="已生成的审核 .xlsx")
    parser.add_argument("--title", help="飞书报告标题，默认使用 Excel 文件名")
    parser.add_argument("--folder-token", help="应用已获授权的飞书文件夹 token")
    parser.add_argument("--state", type=Path, help="本地发布状态文件")
    parser.add_argument("--cli", help="飞书 CLI 可执行文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只在本机解析和校验")
    args = parser.parse_args()
    receipt = None
    try:
        from feishu_workbook import read_workbook

        source = args.workbook.resolve()
        title = args.title or source.stem
        sheets = read_workbook(source)
        # Fingerprint the public report content rather than Excel package timestamps.
        fingerprint = hashlib.sha256(json_text([1, title, args.folder_token, sheets]).encode("utf-8")).hexdigest()
        summary = [{"name": sheet["name"], "range": sheet["range"], "images": sheet["image_count"],
                    "warnings": sheet.get("warnings", [])} for sheet in sheets]
        result = {"identity": "bot", "title": title, "sheets": summary}
        if args.dry_run:
            print(json_text({**result, "status": "dry-run"}))
            return 0

        cli = find_cli(args.cli)
        state = (args.state or source.with_suffix(".feishu-state.json")).resolve()
        if state == source:
            raise ValueError("状态文件路径须与审核工作簿路径不同。")
        with lock_state(state):
            receipt = Receipt(state, fingerprint)
            publish(Feishu(cli), receipt, title, args.folder_token, sheets)
        print(json_text({**result, "status": "verified", "url": receipt.data["url"], "state": str(state)}))
        return 0
    except (ImportError, OSError, ValueError, KeyError, BadZipFile, subprocess.SubprocessError) as error:
        if isinstance(error, ImportError) and error.name == "openpyxl":
            error = "缺少 openpyxl。请用当前 Python 执行 python -m pip install openpyxl。"
        output = {"status": "error", "identity": "bot", "message": str(error)}
        if receipt:
            output.update(state=str(receipt.path), url=receipt.data.get("url"),
                          spreadsheet_token=receipt.data.get("spreadsheet_token"))
        print(json_text(output), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
