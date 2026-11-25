# VeSync PCBA 供应商资料初筛

供 VeSync 电子工程师审核供应商提交的原理图、PCB 和 BOM。工程师在 Windows 本机用 Codex 发起审核并生成 Excel。完整审核交付四张表，PCB 距离检查也可单独交付。需要远端设计取证时，可使用公司已授权的 RDP 会话和远端 Altium Designer（AD）。

> **验证状态**　已从解压后的安装包用双面 Altium PCB 及经制造资料确认的铣槽 Gerber 复算，验证开槽、距离判定、黄色 OK 和本地 Excel 回读。**RDP 端到端流程待真实会话验收。**

## 安装 Skill

1. [下载安装包](downloads/pcba-supplier-review.zip?raw=true)，解压后将完整的 [`pcba-supplier-review/`](pcba-supplier-review/) 目录复制到 Codex 的用户级技能目录，保留 `SKILL.md`、`references/` 和 `scripts/`。也可直接从仓库复制该目录。
2. 按当前 Codex 的用户级目录配置安装。Windows 上可使用 `%USERPROFILE%\.agents\skills\`，安装后应有 `%USERPROFILE%\.agents\skills\pcba-supplier-review\SKILL.md`。若公司环境已配置其他用户级技能目录，使用已配置的位置。[Codex 技能目录说明](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)
3. 在本机工作区打开 Codex，输入 `$pcba-supplier-review` 调用。列表未出现时重启 Codex 后再查。

## 开始一次审核

准备四类本机资料，可以一次提供提交包目录，也可以逐项提供路径。

| 输入 | 提供内容 |
| --- | --- |
| 原理图 | 全套 `.SchDoc`。有 `.PrjPcb` 时一并提供，保留工程目录结构。 |
| PCB | 对应板卡的 `.PcbDoc`。可一并提供同版制造说明、Gerber、钻孔表和板厚。 |
| BOM | 对应板卡和装配变体的物料表。如果工作簿包含多张表，指出本次正式使用的页签。 |
| Checklist | 提供适用于本次板卡的原理图 Checklist。PCB 距离检查默认用包内的公司 Sheet2 规则。 |

在公司已授权的 RDP 会话中按需试运行。登录和身份验证在远程桌面完成。远端需要可打开设计文件的 AD，以及公司允许的工作副本传输和取证文件回传通道。受公司保护的表格通过获准的客户端读取，供应商原件保持只读。[RDP 取证流程](pcba-supplier-review/references/rdp.md)

在 Codex 对话中可直接提交下面的格式，替换示例路径和板卡信息。

```text
$pcba-supplier-review

请对控制板 V1.2 的标准装配变体做供应商资料初筛。
原理图      D:\PCBA\ControlBoard\Design\ControlBoard.SchDoc
PCB         D:\PCBA\ControlBoard\Design\ControlBoard.PcbDoc
BOM         D:\PCBA\ControlBoard\Materials\ControlBoard_BOM.xlsx
Checklist   D:\PCBA\Standards\Schematic_Checklist.xlsx

请复用我已登录的公司 RDP 会话，尝试在远程 AD 取证并记录验收结果，
将四表 Excel 报告保存到本机 D:\PCBA\ControlBoard\review-output\。
```

板号、图纸修订、BOM 版次和装配变体以发布清单或可追溯的对应关系为准。文件名或日期仅用于协助定位。若版本或变体的对应关系尚未确认，报告会保留各份资料的原始标识、影响范围及需要补充的依据。[证据和版本核对](pcba-supplier-review/references/evidence.md)

## 单独检查 PCB 距离

输入 `$pcba-supplier-review`，提供原理图或同版网表及 `.PcbDoc` 路径，说明只生成「PCB爬电距离检查」。已知市场、污染等级、工作电压或加工条件时一并提供。基础量测采用原生板切口和无镀铜孔。附加铣槽依据同版制造资料逐个确认。Skill 使用包内《05 电子电路Check模板工具-》第 2 张表的 **209 条规则**。脚本使用 Python 和 `olefile`、`shapely`，解析附加加工 Gerber 时还需 `gerbonara`。[PCB 距离取证和使用说明](pcba-supplier-review/references/pcb-distance.md)

## RDP 端到端验收

在真实会话中用远程 AD 打开本批次指定的 `.SchDoc` 和 `.PcbDoc`。从两份图档定位同一代表位号并读取原理图参数及 PCB 封装，将证据回传本机并核对该位号的 BOM 原行。将证据写入本地 Excel，重新打开并核对报告中的代表位号和来源。以上步骤全部完成后才记录 RDP 端到端通过。登录和文件往返分别记录为会话及通道状态。[完整验收条件](pcba-supplier-review/references/rdp.md#真实会话端到端验收)

## 阅读报告并复核

完整审核交付一份本地 `.xlsx`，顺序如下。[报告格式](pcba-supplier-review/references/report.md)

| 工作表 | 查看内容 |
| --- | --- |
| 原理图评审 | 按功能模块逐器件列出用途、参数、封装、工况、计算式或核对依据。AI 选型结论与原因／备注分列。常规器件按实际用途判断，只有会影响选型的缺口才留待确认。 |
| 原理图Checklist | 保留条款原文、级别和历史记录。此次审核结果填写在「AI结论」「AI备注」两列，历史结果供回查。 |
| 原理图与BOM | 按位号比对原图参数、图示封装、BOM 原描述、原厂型号和原表位置，保留主料、替代料、预留件、单侧位号及无位号物料。 |
| PCB爬电距离检查 | 分别记录电气间隙、爬电、槽宽和板边距离，保留网络、层、端点、条款、安规限值和公司推荐值。 |

## 样例展示

以下是虚构的控制板 V1.2 审核记录。各表仅摘录关键字段，工程师复核列由工程师填写。

### 1. 原理图评审

用原理图和器件规格核对选型。示例计算采用图纸标称电压和阻值。

| 功能模块 | 位号与参数 | 计算或复核 | AI选型结论 | 原图定位 |
| --- | --- | --- | --- | --- |
| 3.3 V 控制接口 | `R12`，10 kΩ，0603 | 低电平时上拉电流约 `3.3 V ÷ 10 kΩ = 0.33 mA`，在示例器件可灌电流 2 mA 范围内。 | 合适 | `ControlBoard.SchDoc` 第 1 页 B2 |
| 电源滤波 | `C17`，10 µF / 6.3 V，0805 | 该电容接在 12 V 电源上，标称工作电压已超过 6.3 V 额定值。 | 不合适 | `ControlBoard.SchDoc` 第 1 页 C4 |

### 2. 原理图 Checklist

按选定 Checklist 逐条核对。报告保留原表条款及定位，并将本次判断和依据分别写入两列。

| 原表位置 | 示例条款 | AI结论 | AI备注 |
| --- | --- | --- | --- |
| `接口!A8` | 检查 I²C 上拉 | OK | 第 1 页 B2 显示 `R12`、`R15` 分别将 SCL、SDA 上拉到 3.3 V。 |
| `接口!A9` | 核对外部接口 ESD 保护 | NG | `J2` 为外部接口，现有图纸未给出保护器件及连接依据。 |
| `电源!A12` | 核对电池反接保护 | NC | 本装配变体只有市电输入，整套图纸无电池接口。 |

### 3. 原理图与 BOM

逐位号对照图纸参数、封装和 BOM 原始料描，差异可定位到原行。

| 位号 | 原理图参数与封装 | BOM 原始料描 | 原图和 BOM 定位 | AI审核结论 |
| --- | --- | --- | --- | --- |
| `R12` | 10 kΩ，0603 | `RES 10K 1% 0603` | 原图第 1 页 B2，`主板BOM!27` | 可比字段相符。阻值与封装一致。 |
| `R13` | 1 kΩ，0603 | `RES 10K 1% 0603` | 原图第 1 页 B3，`主板BOM!34` | 图表差异。原图 1 kΩ，BOM 10 kΩ。 |

### 4. PCB 爬电距离检查

本例按欧规隔离方案、污染等级 III、250 Vrms 工况计算。示例板的隔离槽以同版制造资料为准，由制板图和加工 Gerber 对应确认。`GM1` 等层文件名只用于定位图档。加工用途未确认时，按 PCB 原生板切口和无镀铜孔量测，并在备注中标出缺少的制造依据。

| 量测项目 | 网络、端点与位置 | 底线 J (mm) | 推荐值 K (mm) | 量测值 (mm) | AI结论 | AI备注与规则定位 |
| --- | --- | ---: | ---: | ---: | --- | --- |
| 初次级爬电 | 顶层 `U2-3 → U2-2`，连续板面 | 8.000 | 8.300 | 8.100 | 🟨 **OK** | 达到 J，距 K 差 0.200 mm。`J17:K17` |
| 初次级电气间隙 | 顶层 `PRI_RETURN → SEC_GND`，铜边直线跨已确认的隔离槽 | 3.500 | 3.800 | 4.200 | OK | 同层铜边间隙达到推荐值。`J16:K16` |
| 走线至板边 | 底层 `NTC1` 铜边 `X=68.480 mm`，同一 Y 处板边 `X=69.100 mm` | 0.800 | 0.800 | 0.620 | **NG** | 低于底线 0.180 mm。`J46:K46` |

🟨 对应 Excel 中的黄色结论格。只运行此项时，可在 Codex 输入下面的请求，并替换文件路径。

```text
$pcba-supplier-review

请只做控制板 V1.2 的 PCB 距离审核，输出「PCB爬电距离检查」。
原理图   D:\PCBA\ControlBoard\Design\ControlBoard.SchDoc
PCB      D:\PCBA\ControlBoard\Design\ControlBoard.PcbDoc
制板资料 D:\PCBA\ControlBoard\Fabrication\

用包内公司 Sheet2 规则，按欧规隔离、污染等级 III 和 250 Vrms 示例工况核对。
依据同版制造资料确认加工槽，说明已确认槽对最短路径的影响。报告保存到本机 review-output。
```

### RDP 取证状态样例

登录成功但文件回传受阻时，可按下表记录已完成步骤及继续审核的范围。

| RDP 会话 | 文件通道 | 远端 AD 证据 | 本地报告 |
| --- | --- | --- | --- |
| 已登录 | 回传目录不可访问 | `R12` 的远端图档证据待回传 | 已读取本地 BOM 和同版 PDF。报告注明已查范围，RDP 端到端待验收。 |

## 判定说明

Checklist 的 AI 结论只有三种。[Checklist 判定规则](pcba-supplier-review/references/checklist.md)

- **NC** 表示条款对本次板卡、变体或审核范围明确不适用。
- **OK** 表示适用条款已有可追溯证据支持通过。
- **NG** 表示适用条款存在差异或通过所需证据不足。AI备注写明是已证实偏差、未达到建议，还是缺少关键资料或测量。

PCB 距离达到安规底线 J 但低于公司推荐值 K，标为**黄色 OK**。低于 J 或底线证据不足，记 NG 并在备注说明。工程师可按图页、位号、坐标、BOM 原行和条款复核，记录实装和加工证据。

## RDP 或资料不可用

若无法登录、传输工作副本、打开远程 AD 项目或回传证据，报告会记录受阻的步骤和受影响的检查。本地可读的 BOM、Checklist 和已确认对应同版的导出件仍可用于其覆盖范围内的初筛。可以补充同版原理图 PDF、网表、PCB 导出或 DRC 报告以支持对应检查。缺少适用的公司 Checklist 时，报告标为资料预检，正式条款覆盖率记为不可计算。
