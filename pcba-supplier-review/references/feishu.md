# 飞书电子表格交付

工程师在审核请求中选择飞书电子表格时，按 [生成及回读 Excel](workbook.md) 在本机任务目录生成并核对审核工作簿。已有符合模板的审核工作簿可以直接使用。发布时通过飞书 Sheets API 写入报告单元格和版式。SCH、PCB、BOM、Checklist 源文件和工作副本留在已有的取证环境中。报告里的证据定位优先使用板号、图纸页、位号、工作表行号或提交包内相对路径。

## 接入和运行

用 `npm install -g @larksuite/cli` 安装飞书 CLI，Windows 也可安装官方发布的可执行文件。已有公司批准的应用配置时，直接用 `lark-cli api GET /open-apis/bot/v3/info --as bot` 核对。首次配置可用 `lark-cli config init --new`。应用发布版本需要 `sheets:spreadsheet:create`、`sheets:spreadsheet:write_only` 和 `sheets:spreadsheet:read`。应用需具备 `sheets:spreadsheet:readonly` 或相应的完整表格权限，以便按原类型回读单元格。脚本需要安装了 `openpyxl` 的 Python。可以用 `--cli` 指定 CLI 可执行文件。[飞书 CLI 安装指南](https://open.feishu.cn/document/no_class/mcp-archive/feishu-cli-installation-guide.md) · [读取单个范围](https://open.feishu.cn/document/ukTMukTMukTM/ugTMzUjL4EzM14COxMTN)

```powershell
python pcba-supplier-review/scripts/publish-feishu.py 'D:\PCBA\review-task\audit.xlsx' --dry-run
python pcba-supplier-review/scripts/publish-feishu.py 'D:\PCBA\review-task\audit.xlsx' --title '控制板 V1.2 供应商资料初筛' --state 'D:\PCBA\review-task\audit.feishu-state.json'
```

`--dry-run` 在本机核对工作表范围和可能存在的转换差异。正式发布可用 `--folder-token` 指定应用和复核人员均已获授权的文件夹。省略时报告建在应用的云空间。以应用身份创建文档、写入和回读，CLI 显式指定 `--as bot`。若应用权限不足，报告相关操作和权限缺口。通过个人账号执行某项操作须取得用户对该操作和身份的明确许可。

## 回读和交付

1. 核对工作表名称及顺序、单元格的值和公式、冻结、合并、筛选、条件格式和 Checklist 下拉。标准审核工作簿保留模板的主要格式及数字类型。确认判定和备注分列、工程师列留空、安规底线以下为 NG，以及满足底线但低于公司推荐值的项目为黄色 OK。
2. 交付 `status=verified` 回执中的飞书链接。列出实际页签和回执报告的格式差异。内嵌图片在飞书版保留文字说明和原表定位，图像本身可在本地审核工作簿查看。
3. 发布状态默认保存在工作簿旁的 `<文件名>.feishu-state.json`。中途失败时，用同一份工作簿、标题、文件夹和状态路径重跑，继续已记录的表格。若创建无回执，先在应用云空间核对是否已生成同名表格。已创建的工作表名称、顺序或合并范围发生意外变更时，先核对已有表格和状态。
4. 分享权限和机器人消息仅在用户明确请求对应操作时办理。分别确认表格、目标协作者和权限等级。给团队复核时，核对目标人员能否打开或编辑链接，按请求记录授权及发送结果。
