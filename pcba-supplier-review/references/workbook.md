# 生成及回读 Excel

先按 [报告规则](report.md) 审核，再准备 UTF-8 的 `review.json`。中间文件放在本次本地工作目录。前三张表包含选型、条款及 BOM 证据。`pcbSafety` 提供第 4 张表的数据，按 [PCB 距离取证和判定](pcb-distance.md) 生成。

## 中间数据

以下是可生成的最小示例。实际使用时写入全部已枚举的位号、条款和 BOM 原行。来源的 `coverage` 填 `complete`、`partial` 或 `unavailable`，对应完整读取、部分取得或不可读。填实际文件、页签、位置和缺口。

```json
{
  "project": "示例控制板",
  "scope": "示例板 V1.0，四份文件属于同一发布批次。",
  "sources": {
    "schematic": {"name": "Control.SchDoc", "location": "Control.SchDoc", "revision": "V1.0", "coverage": "complete", "evidence": "远程 AD 第 1 页，共 1 页。"},
    "pcb": {"name": "Control.PcbDoc", "location": "Control.PcbDoc", "revision": "V1.0", "coverage": "complete", "evidence": "远程 AD 已读取有效元器件和封装属性。"},
    "bom": {"name": "BOM.xlsx", "location": "BOM.xlsx / 控制板", "revision": "V1.0", "coverage": "complete", "evidence": "本机只读核对控制板第 6 行。"},
    "checklist": {"name": "Checklist.xlsx", "location": "Checklist.xlsx / 电源", "revision": "V1.0", "coverage": "complete", "evidence": "本机只读核对电源页第 8 行。"}
  },
  "inventory": {
    "schematicRefs": ["C1"],
    "checklistRows": ["电源!A8"],
    "bomLines": [{"line": "控制板!6", "refs": ["C1"]}]
  },
  "moduleNotes": {"5 V 电源": "去耦和耐压"},
  "schematic": [
    {
      "module": "5 V 电源", "ref": "C1", "state": "计算相符",
      "value": "100 nF, 16 V", "package": "0603", "role": "5 V 旁路",
      "calculation": "5 V ÷ 16 V = 31.25%。",
      "decision": "合适", "note": "标称工况下耐压有余量。",
      "evidence": "Control.SchDoc 第 1 页 B2 C1。PCB C1 实际封装 0603。"
    }
  ],
  "checklist": [
    {
      "location": "电源!A8", "section": "电源", "id": "2-1", "level": "强制",
      "requirement": "5 V 旁路电容耐压至少为工作电压的两倍。",
      "originalHw": null, "originalReview": null, "originalRemarks": null,
      "decision": "OK", "reason": "verified", "note": "C1 额定 16 V，工作 5 V。",
      "evidence": "Control.SchDoc 第 1 页 B2 C1。"
    }
  ],
  "comparison": [
    {
      "ref": "C1", "bomLine": "控制板!6", "bomDescription": "CAP Cer 100nF 16V 0603",
      "bomPackage": "0603", "mpn": null, "bomEvidence": "控制板!I6、L6:N6",
      "role": "主料，原行用量 1 PCS", "status": "可比字段相符",
      "note": "容值、耐压、封装代号均相符。"
    }
  ]
}
```

- `project` 和 `scope` 写项目及比对依据，尤其说明发布和变体是否对齐。`sources` 的四个键固定。`location` 可写本机路径或提交包内可回查路径，`revision` 未明时留空。可选 `sha256` 对应实际使用的文件视图。
- `inventory` 从来源独立点数，按板实例和位号消歧。`checklistRows` 使用唯一源行键，例如 `电源!A8`。BOM 原行的位号组展开到 `refs`，替代料原行分别登记。无位号料件的 `refs` 为 `[]`。部分可读时登记已观察的子集。来源完整时，清单应包含其全部可审核对象。
- `schematic` 和 `checklist` 完整覆盖各自清单。`moduleNotes` 可选，按模块名记录关注点。`value`、`package` 未知时用 `null`，并说明原因。`state` 可选。原理图 `decision` 为 `合适`、`不合适`、`待确认` 或 `不适用`。`calculation` 写可复算工况、算式和单位，或写不适用数值计算时的核对依据。待确认、不合适和不适用须填具体 `note`。
- `checklist` 保留历史字段。源中没有的字段用 `null`。`reason` 与 `decision` 配对。`verified` 对应 `OK`。`mismatch`（已见差异）、`recommendation`（建议项偏差）、`missing`（通过证据不足）对应 `NG`。`not-applicable` 仅对应 `NC`。`evidence` 写定位或明确的资料缺口。
- 原表有需要随报告保留的例图时，可选填写 `checklistFigures`。每项包含 `file`（可读 PNG／JPEG，相对于 JSON 或绝对路径）、`caption`、`evidence`（原表位置）、`widthPx` 和 `heightPx`（原图像素尺寸）。例图随第二张表的表末嵌入。只可定位时在相应条款备注写原表位置。
- `comparison` 对每个 BOM 原行 × 位号建一条，无位号件填 `ref: null` 和可读的 `item` 名，单独保留一行。每个已枚举的原理图位号也至少出现一次。单侧无 BOM 时填 `bomLine: null`，`bomEvidence` 写 BOM 搜索范围或读取缺口。有对应原理图位号时，参数和封装从 `schematic` 复用。无对应时填 `schematicEvidence`，区分已查范围和未读取的范围。关联 PCB 定位也可填入该字段。`bomDescription`、`bomPackage`、`mpn` 保留原文。`role` 标明主料、替代、预留、不贴及原行用量。`status` 和 `note` 按 [报告规则](report.md) 列明可比项、具体差异或需补的依据。

## 运行生成器

使用 Codex 工作区提供的 Node.js 与 `@oai/artifact-tool`。工作区依赖工具可用时从工具取得实际路径，否则查当前主机已配置的 Codex 捆绑运行时。Windows 上常见位置为当前用户目录下 `.cache/codex-runtimes/codex-primary-runtime/dependencies`，以本机实际安装为准。

把本 Skill 的 [工作簿生成器](../scripts/build-review.mjs) 及其同目录 Excel 模块复制到本次临时目录，将该目录的 `node_modules` 链接到运行时的 `node_modules`。用同一运行时的 Node.js 执行，路径含空格时加引号。

```text
node build-review.mjs review.json 项目-原理图PCB及BOM评审.xlsx
node build-review.mjs review.json 项目-原理图PCB及BOM评审.xlsx 临时预览目录
```

最后的目录参数可省略。提供时保存各表的头部预览 PNG，供版面校验。已有的三表 JSON 仍可生成对应三张表。完整审核使用 `pcb_distance_review.py --base-review` 将 `pcbSafety` 加入同一 JSON。独立 PCB 距离审核使用 `--pcb-only` 和 `{ "project": "项目名", "pcbSafety": {...} }` 数据。中间证据和预览留在临时目录。

## 回读验收

1. 完整审核的表序为「原理图评审」「原理图Checklist」「原理图与BOM」「PCB爬电距离检查」。核对位号、Checklist 原行、BOM 原行、替代料和无位号物料与独立清单相符。独立 PCB 输出回读其单张表。
2. 抽核一个可比相符项、一个差异项以及一个预留或证据缺口。确认原值、页码、原行和结论吻合，图示封装、PCB 实际封装与 BOM 封装仍可区分。像 0603 的封装代号按文本保留。
3. 核对第二张表仅有 NC、NG、OK，下拉限制可用。确认 NC 有实际不适用理由，NG 备注说明性质。检查工程师列留空、冻结、筛选、换行和公式缓存。
4. 核对第四表的量测口径、J/K、原表行号、端点、黄色 OK 和工程师复核栏。来源不可读时在受影响的表中说明范围和补件。交付说明实际完成的远程取证、制造前提及本次局限。
