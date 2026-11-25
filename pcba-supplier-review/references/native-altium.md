# Altium 原生图档直读

本流程在有无 Altium Designer（AD）的电脑上使用同一组只读脚本。图档直读需要 Python 3.10 及以上和 `olefile`。按 [证据要求](evidence.md) 先配对板号、修订和装配变体，原件通过公司允许的本机读取方式获取。

```powershell
python -m pip install olefile
python scripts/read-altium.py --sch 'D:\PCBA\Control.SchDoc' --pcb 'D:\PCBA\Control.PcbDoc' --out 'D:\PCBA\review-output\design-evidence.json'
```

示例从 Skill 目录运行。多页原理图对每个文件重复 `--sch`。仅取证一类文件时，可只传该类参数，或使用 `scripts/read-altium-schematic.py`、`scripts/read-altium-pcb.py` 的同名参数。每块板独立配对和读取。输出文件使用新路径，供本次报告整理和回查。

## 用证据填报告

- SCH 从二进制 `FileHeader` 读取器件、位号、参数、当前及候选 PCB 模型、引脚、导线、连接点、网络标签和端口。保留文件、记录序号、字节偏移和图面坐标。按引脚端点和导线逐项核对局部图面连接。相同位号跨文件或存在多个部件时，先核对图页、部件及工程实例。
- PCB 从 `Components6`、`Texts6`、`Pads6` 和 `Nets6` 读取元件模型、物理位号、源位号、焊盘、网络和坐标。以元件记录编号联接文本及焊盘。物理位号重复、来源号与物理位号有差异、对象无位号时，保留全部原始记录，必要时按焊盘、封装和坐标复核。
- 报告的位号清单由已枚举的图面对象和 BOM 原行独立生成。PCB 网络名是图档保存的属性，涉及同名铜岛、换页、总线、重复通道、编译遮罩、变体或正式 ERC／DRC 的判断，补充适用的同版网表、规则报告或制造资料。按实际复核证据填写结论。

直读支持已验证的 OLE 二进制格式，也按文件内容识别采用该格式的 `.sch`。其他存储格式、厂商自定义对象或缺失图元按错误和诊断记录受影响范围，再找同版 PDF、网表、属性表或已授权 AD 补证。图档证据 JSON 用于审核取证，工程师可按原有方式输出 [Excel](workbook.md) 或 [飞书电子表格](feishu.md)。

## PCB 距离和开槽

几何量测还需 `shapely`。双层板可从 `.PcbDoc` 还原名义铜面，按选定绝缘边界和网络核查间隙、爬电、板边及开槽。附加加工槽以同版制造资料确认，机械层名用于定位图档。几何脚本按文件中的源位号、焊盘或网络选择铜对象。源位号与物理位号不同或重复时，先核实选择器命中的对象。[PCB 距离取证和判定](pcb-distance.md)

可识别的刚性单叠构多层板支持顶层或底层指定铜对象之间的同层二维边到边间隙。在 Skill 目录运行以下命令，并在 `scopes.json` 指定两侧待量测对象。

```powershell
python scripts/pcb_geometry.py --outer-clearance-only --pcb 'board.PcbDoc' --scopes 'scopes.json' --out 'geometry.json'
```

结果用于指定的外层间隙项。多层板的爬电、内层间距、跨面路径、实际板边和槽需另有对应证据。公开样件 Arribada Argos SMD v1.0 的顶层、底层铜边可分别与同版 `GTL`、`GBL` 对照。[多层板外层铜边间隙](pcb-distance.md#多层板外层铜边间隙)
