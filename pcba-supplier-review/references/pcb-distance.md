# PCB 电气间隙和爬电距离

## 选定范围和公司规则

提供本次发布的 `.PcbDoc` 和用于判定绝缘边界的 `.SchDoc` 或同版网表。可提供同版 Gerber、钻孔、制造要求和装配条件。核对 CAD 与制造资料的版次，保留文件名、版本、SHA-256、来源以及实际采用的加工情景。审核几何可在本机运行。需要远端导出时，按 [RDP 取证](rdp.md) 取得同版证据。

默认规则是 [公司当前 Sheet2 数值目录](pcb-safety-rules.json)，来源为《05 电子电路Check模板工具-》第 2 个工作表「05-1 爬电距离和电气间隙审查表」。按销售市场、隔离方案、污染等级及绝缘条件选条款。I 列是原表工况。J 为安规限值，K 为推荐设计值。实际工作电压影响限值时，使用可追溯的 Vrms，或把用户指定电压记为有条件的设计情景。插值按原条款计算。适用性和工作电压证据不足时，按受影响条款注明具体缺口。

用原理图及板上连接确认初级、次级、PE、可接触侧和浮置中间节点。按适用条款挑选完整的网络群与局部焊盘，必要时分别检查两个范围。在 G 列原条款定义下确认诸如保险丝前、继电器高低压、Y 电容间距等端点。网络名仅用来定位，归类依据保留电路连接、位号和图页。物理器件外形、线束按压位移、实际焊锡和壳体距离使用实物或同版装配证据。

## 识别实际加工槽

- 本机模型起点是 `.PcbDoc` 明确建模的 `Board Cutout` 和无镀铜孔。附加加工槽由同版制板图、制造说明、钻槽表或制造层映射确认，记录适用的图元和加工用途。GM1 等文件名用于定位图档。板框、注记、无镀铜槽、镀通槽和无镀铜孔分别识别。公司开槽宽度条款应用于已确认且适用的隔离槽。
- 先比对坐标系和同一槽在原生对象、机械层与制造输出的宽度及位置。成套证据相符时按相应工件建模。存在定义差异或供应商报告加工异常时，分别记录 CAD 设计、发布加工图档和成品检验结果。漏铣情景附到相应槽编号和最短路径，判定限定在该情景。
- 平面最近铜边用于同层电气间隙。路径完全位于连续板面时才可直接用于同面爬电。跨槽时检查绕槽路径及可能更短的其他铜边、过孔和铺铜。跨面下界与同面最短值合并时，包含需要覆盖的各方向。只有单个构造路径时可得到最短距离上界。只有下界时按已能证明的阈值判定。实际不符合可用低于限值的有效路径证实。

## 执行本地测距

随包的 [量测脚本](../scripts/pcb_geometry.py) 提供双层板量测和 [多层外层铜边间隙](#多层板外层铜边间隙)。优先使用 Codex 工作区提供的 Python，依赖为 `olefile` 和 `shapely` 2.x。解析附加加工 Gerber 还需 `gerbonara`。缺少依赖时可用 `python -m pip install --target <本次临时依赖目录> olefile "shapely>=2" gerbonara`，再将该目录加入此次 Python 的模块搜索路径。网络、加工对象和最小厚度取自本次板卡证据。

```json
{
  "minimum_board_thickness_mm": 1.4,
  "scopes": [
    {
      "id": "primary-secondary",
      "a": {"nets": ["P1", "P_RETURN"]},
      "b": {"nets": ["S_RETURN", "S1"]},
      "threshold_mm": 8.3
    }
  ]
}
```

选择器可使用精确的 `nets`、`refs`、`pads` 和 `objects`。同一侧同时指定的条件取交集。用 `[{"ref":"U2","pad":"4"}]` 选择焊盘，或用 `[{"stream":"Vias6","index":346}]` 选择零起始的原生图元。`threshold_mm` 用于安排候选搜索，公司的适用限值由规则计算器单独确定。`minimum_board_thickness_mm` 取制造要求的最小板厚，用于双层模式的跨面求证。

```text
python pcb_geometry.py --pcb board.PcbDoc --scopes scopes.json --out geometry.json
```

需要添加制造资料已确认的隔离槽时，传入 `--routing-gerber route.gbr` 并在 `mechanical_route.object_indices` 填对应图元在该 Gerber 中的零起始序号，例如 `[101, 102]`。制造资料确认某 D-code 下**全部选中图元**均为同类隔离槽时，可使用 `mechanical_route.apertures`。同时指定两个条件时取交集。`--gm1` 作为旧命令的等效参数保留。仅提供 Gerber 文件时，量测采用原生板切口和无镀铜孔。

脚本可用原生机械一层图元配准已选的加工路径。其他层或未能唯一匹配时，根据独立配准证据在 `mechanical_route.datum_matches` 提供至少两个对应点，坐标以 mm 填 `gm1_mm` 和 `pcb_mm`。厂商已确认原生机械一层某厚度的线或弧全部为所需槽、且没有附加工 Gerber 时，也可用 `mechanical_route.widths_mm` 指定该批次的确认宽度。当原生切口伸出匹配加工槽边界时，核对发布制造图档。确认制造图档为本情景的加工定义后，才使用 `mechanical_route.confirmed_routes_override_cutouts: true` 并记录依据。

回看输出中的对象数、层别、板外形、原生切口、机械层匹配、铣槽组、镀通槽和结果覆盖。检查最近点的坐标、屏障与相关端点。尚未确认制造开槽时，保留本模型的图档情景和成品证据缺口。筛查多种可能工艺时，为每个情景单独生成量测证据。没有相关匹配、未支持板型或没有覆盖到必要路径时，可用同版 AD 导出的爬电、制造文件或成品测量补充。AD 的常规 Clearance 对象规则与 Creepage Distance 规则分别使用。 [Altium 电气规则](https://www.altium.com/documentation/altium-designer/pcb/design-rule-types/electrical#creepage-distance)

## 多层板外层铜边间隙

对于能识别铜层顺序的刚性单叠构 Altium 板，使用外层模式测量指定铜对象在顶层或底层的二维边缘距离。过孔按起止层确认在哪一面有铜，并读取该面的环形外径。结果记录原生数据流、图元序号、偏移、网络和最近点。遇到未知层跨度或未支持的焊盘结构时，使用同版 Gerber、独立量测或其他合适的证据补查。

以下范围可用来复算公开四层样件的第一对顶层间隙。外层模式的范围文件只提供铜选择，板厚和铣槽使用各自对应的核查流程。

~~~json
{
  "scopes": [
    {
      "id": "argos-top-via-track",
      "a": {"objects": [{"stream": "Vias6", "index": 346}]},
      "b": {"objects": [{"stream": "Tracks6", "index": 488}]}
    }
  ]
}
~~~

~~~powershell
python pcb_geometry.py --outer-clearance-only --pcb board.PcbDoc --scopes scopes.json --out geometry.json
~~~

Sheet4 仅用这份几何结果绑定明确针对同一外层和所选对象的铜边电气间隙。选择 kind 为 `clearance`，layer 为 `top`、`bottom` 或 `same-face`。内层、跨面、爬电、成品板边和加工槽条款使用对应的独立证据。适用条款缺少通过依据时保留 NG 及缺口。网络组的整体结论需选择该条款涉及的全部铜对象。

### 开源四层板复核

使用 Arribada Argos SMD v1.0，固定提交为 `05ba8d2c6408aaeecd346fc41d2a419715d970c3`，项目许可为 GPL-3.0。以 Altium 原件运行外层模式，再按同版 Gerber 的坐标和口径独立计算。两个坐标系用 X、Y 各 25.4 mm 平移配准。两种方法均使用铜边，Gerber 叠加极性后的局部间隙另做抽点核对。[项目及许可](https://github.com/arribada/argos-smd-hw/tree/05ba8d2c6408aaeecd346fc41d2a419715d970c3) · [原生图档](https://raw.githubusercontent.com/arribada/argos-smd-hw/05ba8d2c6408aaeecd346fc41d2a419715d970c3/module-BGA_RG-4layers_v1.0.PcbDoc) · [同版制造 ZIP](https://raw.githubusercontent.com/arribada/argos-smd-hw/05ba8d2c6408aaeecd346fc41d2a419715d970c3/Project%20Outputs%20for%20BGA_RF-ArticGR/stm32wl-dev_SMD_V1.0_202404/stm32wl-dev_SMD_V1.0.zip)

| 层面 | 原生图元（零起始） | 同版 Gerber 图元（零起始） | 原生（mm） | Gerber（mm） |
| --- | --- | --- | ---: | ---: |
| 顶层 | Vias6 #346 ↔ Tracks6 #488 | GTL #751 ↔ #700 | 0.135629 | 0.135713 |
| 顶层 | Pads6 #115 ↔ Vias6 #161 | GTL #248 ↔ #733 | 0.110019 | 0.109911 |
| 顶层 | Vias6 #114 ↔ Vias6 #164 | GTL #714 ↔ #736 | 0.242417 | 0.242351 |
| 底层 | Pads6 #30 ↔ Vias6 #42 | GBL #31 ↔ #112 | 0.279096 | 0.279215 |
| 底层 | Pads6 #22 ↔ Pads6 #23 | GBL #14 ↔ #15 | 0.832000 | 0.832104 |

五组最大绝对差为 0.000119 mm，其中包括使用逐层外径的过孔。结果用于核对该设计的名义几何和图元对应关系。

## 从测距结果生成 Sheet4

准备 `pcb-plan.json`。每行指向公司表的物理行号 `rule_row`，写明 `applicable` 的依据，补充电压、位号、加工情景和证据。示例用于在 ≤250 V 工作条件下比较加强绝缘的同一板卡。

```json
{
  "project": "示例控制板",
  "pcbSafety": {
    "scope": "控制板 V3，按确认的机械层加工图档进行名义尺寸核对",
    "market": "欧洲AC230V",
    "supply": "隔离",
    "pollution_degree": "III",
    "sources": ["同版 board.SchDoc、board.PcbDoc 与已确认的制造资料。工作工况见图页 1。"],
    "rows": [
      {
        "id": "P01",
        "rule_row": 17,
        "applicable": true,
        "working_vrms": 250,
        "boundary": "主隔离带",
        "evidence": "隔离边界和 Vrms 假定见项目审核记录。",
        "geometry": {"scope": "primary-secondary", "kind": "creepage", "layer": "all-surfaces"}
      }
    ]
  }
}
```

`geometry.kind` 可用 `creepage`、`clearance`、`outline`、`route-width`。`creepage` 的 `layer` 可选 `top`、`bottom`、`same-face`、`cross-face`、`all-surfaces`。`clearance` 和 `outline` 仅接受 `top`、`bottom`、`same-face`。`same-face` 表示选中各外层分别计算后的最小值。按走线检查板边时，`outline` 加 `"tracks_only": true`。`route-width` 可附 `gm1_object`，否则按已确认铣槽段取最窄名义宽度。表格备注写清测的是线路、原生铜边、加工宽度还是实物。

实测、AD 结果和独立推导量可直接写 `measurement_mm`，或写 `lower_bound_mm` / `upper_bound_mm`，附来源位置和证据。这样的行省略 `geometry`。只要存在有效路径小于 J，即可判 NG。证据支持 J ≤ 距离 < K 时判黄色 OK。若下界已经达到 J 且对 K 尚无足够证据，备注说明对 K 的覆盖缺口。适用条件明确不成立时用 NC。适用或适用性未知而底线证据不足时用 NG。

```text
python pcb_distance_review.py geometry.json pcb-plan.json pcb-review.json
node build-review.mjs --pcb-only pcb-review.json PCB距离初筛.xlsx
python pcb_distance_review.py geometry.json pcb-plan.json full-review.json --base-review review.json
node build-review.mjs full-review.json PCBA初筛.xlsx
```

`review.json` 是按 [工作簿数据规范](workbook.md) 完成初审的前三表数据。`--rules` 可指定获准的新一版规则 JSON。完全使用人工证据时，也可直接用 `pcb_evaluate.py` 将同结构的量测计划转换为 `pcbSafety`。生成后回读复核规则所在页及物理行、证据端点、公式和黄色 OK。工程师复核列留空。
