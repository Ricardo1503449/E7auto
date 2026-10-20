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

### 审核通过后沿用既有验证

此规则跨对话生效。先读取对应任务results中的候选/验证记录及候选校验文件，核对批准PNG的SHA-256；不因上下文丢失从头重测。候选PNG包含其透明遮罩，按原字节登记不构成新的像素变化。

| 实际变化 | 验证范围 |
| --- | --- |
| 图片、识别算法、ROI和阈值均未变，仅正式登记或迁移路径 | 沿用已通过的正反例、缩放及识别结果；只核对哈希、来源、目标路径和登记完整性，不再扫描旧截图 |
| 新增事件，复用已有战斗/文本/返回能力，仅增加绑定和分派条目 | 最小检查新增资源绑定、点击方向、事件分支及返回流程；复用已有公共能力的验证，不重跑全部旧事件 |
| 图片、识别参数或公共识别/流程行为变化 | 只重测失效证据对应的样本与受影响分支；新失败或新样本揭示问题时再补相关边界 |
| 仅补审核状态、说明或记录文档 | 核对内容、必要链接和差异，不运行图像或业务测试 |

单纯向事件注册表添加条目，不视为公共流程行为变化。需要扩大回归时，说明具体改动或失败依据；不得仅以“审核后再确认”“保险起见”重复旧检查。前次验证记录确实缺失时，只补无法确认的最小范围，不默认全量重跑。

`prepare → 核对计划 → apply`、`check_changes`及适用的布局/源码边界、提交前检查仍须执行。登记完整性不替代首次识别验证，也不要求重复已有效的识别验证。交付时区分“沿用此前通过的结果”和“本次实际执行的检查”，复用原任务记录，避免重做审核网页或重复过程文档。

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

## 6. 保留原字节的目录迁移与退役

仍逐项执行候选导出→核对prepare计划→apply，不能并发写入。`write_candidate_png(path, decoded_image, png_bytes=original_bytes)`可在解码像素完全一致时原样导出已有PNG，连同候选校验文件一起保留；不重新编码图片。

- `prepare --operation relocate`：候选摘要必须等于现有PNG，`--filename`指定不同的用途子目录；来源记录必须包含 `operation: relocate`、`previous_filename`和一致的`target_filename`。既有目标文件或其他模板占用目标均拒绝。apply写入新路径、更新清单、删除旧路径，旧PNG留有事务备份。
- `prepare --operation retire`：候选仍需保留被移除PNG的原始字节，来源记录包含 `operation: retire`、`previous_filename`及非空`retirement_reason`；不提供新filename。apply从活动templates移除条目、写入retired_templates登记引用并删除正式PNG。退役前必须同步运行绑定，防止启动继续要求已撤掉的文件。
- apply/recover继续校验旧文件、候选、来源、清单和所有输出；迁移/删除失败会按事务恢复原路径和清单，遇到外部改动则保留并报告冲突。源路径及目标路径都必须位于对应功能目录。
- check_changes检查迁移的新登记来源、移除的退役来源以及正式目录不残留退役PNG；普通register行为不变。以后补齐新的正式小卡时，可用普通register重新登记相同键和明确filename，清单自动移除旧退役索引；原历史记录保留。

示例（路径仅示意，实际使用准备好的候选导出目录）：

```powershell
python -m scripts.templates.register prepare --operation relocate --feature chaos --key loot_old_sword --filename loot/loot_old_sword.png --candidate "artifacts/template-candidates/本次目录/chaos/loot/loot_old_sword.png" --source-record "artifacts/template-candidates/本次目录/sources/loot_old_sword.json" --plan-out "artifacts/template-candidates/本次目录/plans/loot_old_sword.json"
python -m scripts.templates.register apply --plan "artifacts/template-candidates/本次目录/plans/loot_old_sword.json"
```
