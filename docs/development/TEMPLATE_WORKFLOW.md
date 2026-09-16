# 模板候选与正式登记

模板提取不再直接覆盖正式资源。此流程适用于商店、企鹅、共享模板和后续新增功能，也适用于已有模板的替换、裁剪、透明通道、遮罩和像素调整。以原图为基础修改仍必须生成候选并登记，不能通过手工更新正式图片和清单哈希绕过流程。执行约束见项目根目录 `AGENTS.md`。

## 1. 导出候选

从项目根目录运行已有提取命令并提供原始素材。例如：

```powershell
.\.venv\Scripts\python.exe -m scripts.calibration.extract_main_shop_icon_template --source "完整主界面截图.png"
```

默认输出到 `artifacts/template-candidates/<时间>-<唯一编号>/`，终端会打印实际路径。目录内保留功能层级（如 `shop/main_shop_icon.png`），每个PNG旁新增同名 `.candidate.json`，记录PNG的SHA-256和尺寸。原有来源/裁剪/遮罩记录也输出到候选目录。

`--output-dir`、适用工具的 `--manifest-dir` 仍可指定自定义候选位置，但不允许指向正式模板、正式校准记录或运行配置目录。网络裁剪新增 `.source.json`；网络文字转换通过 `--template-dir` 读取输入、`--output-dir` 导出新候选，不再原地修改输入。

导出工具明确指定所属功能，按运行清单解析已登记模板路径。未知键直接报错，不默认归商店。新增模板由对应工具明确生成候选，再在登记时提供功能、模板键及正式文件名。

全窗口/悬浮窗校准和实机证据整理是独立记录工具，不属于PNG候选提取；它们原有的文档输出参数继续保留。

## 2. 验证候选并生成登记计划

先检查候选内容，并运行有关真实正反例、缩放或功能回归。登记工具验证文件、元数据和一致性，不替代识别质量验证，也不运行游戏。

将示例中的候选目录替换为实际输出路径：

```powershell
.\.venv\Scripts\python.exe -m scripts.templates.register prepare --feature shop --key main_shop_icon --candidate "artifacts/template-candidates/本次目录/shop/main_shop_icon.png" --source-record "artifacts/template-candidates/本次目录/main_shop_icon_manifest.yaml" --plan-out "artifacts/template-candidates/本次目录/registration_plan.json"
```

`prepare` 不修改正式资源。它验证8位彩色PNG、非空有效遮罩、基准尺寸、候选校验文件和JSON/YAML来源记录，并生成可审阅计划。计划列出目标文件、候选哈希、来源哈希、完整清单结果，以及当前正式文件的预期哈希。

新模板必须加上 `--filename`，该路径相对于功能目录，例如 `--feature new_feature --key entry --filename entry.png`。不能接管其他模板的文件、覆盖未登记文件或越出功能目录。新功能还需要按 `TEMPLATES.md` 接入运行配置、功能入口及发布验证；登记图片不会自动启用功能。

## 3. 显式应用已核对的计划

```powershell
.\.venv\Scripts\python.exe -m scripts.templates.register apply --plan "artifacts/template-candidates/本次目录/registration_plan.json"
```

- 重新核对候选、来源、校验文件、清单及旧PNG；预览后任一输入或正式状态改变，都会拒绝应用，需要重新生成计划。
- 候选PNG按原始字节复制，不重新编码。只更新指定模板条目，保留其他清单内容；不修改搜索范围、阈值和运行配置。
- 来源记录复制到 `docs/calibration/registrations/`，运行清单的 `source.record` 和 `record_sha256` 指向该记录；来源文件本身不成为运行时依赖。
- 按来源记录、PNG、运行清单的顺序替换文件，并调用现有运行清单加载器验证；发布校验以运行清单为当前文件列表，并从源码校准目录验证新增来源记录哈希。
- 现有正式图片被意外改动时，登记工具会拒绝，不能通过自动刷新哈希把未知改动变成有效模板。

一次登记处理一个模板。多个模板分别生成、检查和应用计划；多个写操作不能并发。

## 4. 失败与中断恢复

每次应用在 `artifacts/template-registration/transactions/<ID>/` 保存旧文件、待写文件及校验日志。该目录保留以便检查。

普通执行错误会尝试恢复旧PNG、旧清单和旧来源状态。进程被终止等中断可能留下未完成事务，后续应用会拒绝继续并提示事务ID；也可在上述目录查看ID。

```powershell
.\.venv\Scripts\python.exe -m scripts.templates.register recover --transaction "事务ID"
```

恢复会先校验所有备份和当前文件。若发现应用后又被其他操作改动的文件，会保留该外部修改并报冲突，不强行覆盖。处理冲突前保留事务目录。已完成或已回滚的事务再次恢复仅返回状态，不改变文件。

多个文件的替换不是一次文件系统原子操作。每个文件单独使用临时文件替换；中途启动程序可能遇到哈希不一致而被拒绝，事务日志负责恢复到一致状态。不要把该工具描述为无中间状态的多文件原子发布。

本工具不提交Git、不推送、不打包或发布程序。

## 5. 模板变更检查

完成更新后检查工作区（包含未跟踪的模板和来源记录）：

```powershell
.\.venv\Scripts\python.exe -m scripts.templates.check_changes
```

准备提交时检查实际暂存内容；正式PNG、运行清单和登记来源记录必须一起暂存：

```powershell
.\.venv\Scripts\python.exe -m scripts.templates.check_changes --staged
```

检查已提交变更时使用明确的基准提交，例如：

```powershell
.\.venv\Scripts\python.exe -m scripts.templates.check_changes --base HEAD~1 --revision HEAD
```

命令只读，不修改资源或Git状态。新增/修改模板必须有对应登记来源，图片与清单、来源记录与来源哈希必须一致；复用旧登记来源而只更换PNG和清单哈希也会失败。已登记来源被修改或遗漏、暂存时漏加来源记录、未登记的新PNG均会报错。无变化的历史模板不要求追溯登记。

本项目使用本地检查命令，不配置模板检查CI，也不自动安装hook。首次提交没有基准时使用 `--base EMPTY`。`.gitattributes` 禁止Git转换登记来源记录的换行，保留哈希对应的原始字节；该文件应随检查工具一起提交。此检查验证可追溯状态，不能证明调用历史，也不是文件系统写保护；禁止用伪造登记记录绕过工具。Git提交授权与相关识别测试要求不变。

本次实现的测试范围和正式资源保护检查见 [验证记录](../validation/templates/TEMPLATE_WORKFLOW_VALIDATION.md)。
