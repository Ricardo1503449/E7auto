# 目录与产物约定

机器可读规则在 `scripts/project/layout.json`；路径与创建入口在 `scripts/common/paths.py`。涉及新增文件、功能、修复、校准、构建或迁移时，先确定归属。不得通过新增misc/temp顶层目录绕过分类。

## 目录用途

- `artifacts/`：本地任务和实验输出；除README外不提交。任务按tasks/releases/git/maintenance划分，另保留模板工具专用目录及冻结archive。
- `docs/`：user使用说明、design当前设计、development开发规范、validation按主题的验证结论、releases按版本的交付结论、calibration来源证据。
- `tests/`：按被测职责分组；固定样本进入fixtures/<功能>，运行输出不写入测试源码。pytest通过tmp_path或tmp_path_factory分配隔离临时目录，基目录可以是系统临时目录或显式--basetemp；需要纳入任务管理时，使用管理任务中的独立测试子目录。Node/Worker测试使用管理任务中的隔离临时目录，正式验证证据按对应结果/预览分类保存。
- `build/`：nuitka/<构建ID>存编译产物；staging/<构建ID>/setup保留已封存、待验收的安装EXE。封存成功后清理本次nuitka，staging保留至安装验收、测试卸载及dist提升成功；构建或验收失败保留现场及原可用安装包。`dist/`只保留最新成功验收的标准安装EXE，提升时清理其直属旧版标准安装EXE，不生成ZIP或备份，不处理其他文件和目录。
- `.venv/`：Python运行和开发依赖，包括Nuitka。`tools/`：机器规则登记的项目自行管理的非Python工具安装副本、固定缓存及私有工具状态；全部忽略Git，不随E7auto打包。外部Node/npm通过私有配置登记实际路径，不复制进项目；未经明确授权不安装、升级或修改系统PATH。安装清单、锁文件和工具源码仍保存在scripts。

## 创建新任务和功能

从项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.project.artifacts --kind tasks --feature shop --subject fix-purchase-result
.\.venv\Scripts\python.exe -m scripts.project.artifacts --kind maintenance --subject layout-audit
.\.venv\Scripts\python.exe -m scripts.project.artifacts --kind git --subject shop-fix-commit
```

命令打印唯一目录。task.json记录类别、功能、主题、创建时间、来源提交和状态。任务ID采用日期时间、主题、随机短ID，不覆盖同名任务。

普通任务子目录：inputs保留原始输入；previews保留派生展示；results保存验证输出；scratch保存一次性脚本和临时文件。同一任务多轮修复使用已有任务的相应子目录；无关新问题新建任务。完成后将task.json的status标记complete；失败为failed，历史导入为archived。不要求为每一小步创建新目录。

新增功能先在 `scripts/project/layout.json` 的features登记稳定的小写英文名，再创建任务和所属fixtures；不会因此自动启用运行功能。模板仍需遵循[模板组织](TEMPLATES.md)和[登记流程](TEMPLATE_WORKFLOW.md)。未知功能报错，不能默认归入shop。

模板候选及登记计划只能在 `artifacts/template-candidates/<导出ID>/`；任务记录引用候选，不复制候选。现有事务、候选和 `docs/calibration/registrations/` 的位置和字节保留。新增校准记录须在layout.json的calibration_records明确登记主题路径；代码通过calibration_record_path访问，不以调用者目录推断。

## 文件生成规则

- 工具通过create_artifact_run或task_result_path创建默认输出。不得自行拼接artifacts根目录下的新任务名。
- 验证器默认生成任务results，显式指定项目内结果路径时必须指向已有管理任务。明确指定项目外的临时/导出路径仍支持；不自动把用户输入复制到工程。
- 发布构建自动创建releases/<版本>/<构建ID>，编译目录和临时配置使用同一个ID。日志/构建结果写入对应release任务；封存包先留在本次staging/setup，安装验收成功后才提升到dist并清理旧版标准安装EXE。
- 同一构建的build-inputs.json、build-artifact.json、release-verification.json和publish-check.json直接放该release任务根目录；发布后的远端响应和上传结果也放本地产物，不写回README或发布清单。docs/releases/<版本>/只在最终提交前集中整理验证结论及限制。
- 可复用工具写入scripts对应模块；仅本次任务使用的脚本写入scratch。程序实际运行日志和用户状态仍使用logs和state，不属于开发实验结果。
- Cloudflare配套源码归入scripts/cloudflare，Wrangler/Miniflare安装在登记的tools/cloudflare/shop-stats，npm缓存在tools/cache/npm，授权状态在tools/state/cloudflare。Node/npm使用tools/state/tool-paths.json登记的外部安装，显式参数可覆盖；配置无效直接报错，不隐式切换或更改系统PATH。stage、测试数据库和Wrangler运行输出继续使用所属任务scratch；不在源码目录生成node_modules。对应tests/scripts/cloudflare允许.mjs测试源码，其他测试目录的文件类型规则不变。
- Nuitka固定缓存登记在tools/cache/nuitka，GCC安装副本由nuitka_gcc条目登记在该缓存内的版本目录；构建通过公共路径入口设置NUITKA_CACHE_DIR，不在根目录重新创建.nuitka-cache。迁移保留原工具/缓存字节及历史构建证据，不改变Python依赖环境。
- Inno Setup安装及安装器副本在tools/innosetup，版本仍为6.7.3；重建.venv不影响这些工具。工具重装和缓存清理只处理各自登记目录，不递归清空tools或授权状态。迁移日志与结果使用管理任务，历史证据不改写。
- 文档规范更新当前有效行为；验证文档记录结论、范围、限制和证据位置。原始日志与图像不写入docs。新bug先更新所属主题文档，避免final/v2/fix等平铺文档。
- 归档只用于明确授权保留的历史资料。每份归档由_archive.json冻结文件清单和SHA-256，禁止作为工具输出目录。迁移前后以映射和摘要核验，未完成登记事务不得移动或清除。

## 检查与交付

源码按core/runtime/features/vision/resources/configuration/platform/logging/ui分层，组装位于bootstrap.py，具体职责见[开发指南](DEVELOPMENT.md)。source_rules记录允许的源码层、根入口、禁止重新引用的旧模块以及允许延迟导入WGC的工厂。功能名与tasks、fixtures使用同一登记表；测试仍按被测职责分类，不按源码树机械复制目录。

```powershell
.\.venv\Scripts\python.exe -m scripts.project.check_layout
.\.venv\Scripts\python.exe -m scripts.project.check_layout --staged
```

第一条检查工作区，也扫描被Git忽略的artifacts和build；检查归属、文件类型、任务记录、归档完整性、Markdown本地链接以及源码位置/依赖边界。第二条读取Git索引的实际文件和规则，不能用未暂存的修正掩盖待提交问题。它检查整个待提交树，不修改索引。

每次新增/移动文件或更改输出默认值后运行工作区检查。获准提交后，检查暂存范围、`git diff --cached --check`和staged布局；涉及正式模板继续执行专用的check_changes检查。

目录检查会报错，不自动搬动、删除或修复文件；不安装Git hook、不改.git/config、不自动配置CI。它检查工具登记、正式入口及任务中误放的正式工具安装，跳过第三方工具、缓存和私有状态的内部源码；不是文件系统写保护。新增类型明确扩展规则及必要的相关验证，不添加整个scratch的无限豁免；新增依赖不强制专门新增边界测试。

验收包括正确默认输出、错误功能/类别/路径拒绝、被忽略的乱放文件检出、索引与工作区差异验证、已有样本和登记资料完整性。只运行直接相关测试；Qt与WGC允许同进程，但保留兼容运行库选择、导入顺序及退出回归。构建脚本检查通过不代表已经重新构建发行包。

根目录task_plan.md、findings.md、progress.md仍按既有恢复规则保留，不迁到本地任务目录。

模板登记事务文件类型由layout.json的registration_transaction_files明确登记。新增的obsolete_template.old只允许用于journal声明的relocate/retire旧PNG删除角色，且须记录原摘要及空的新摘要；不接受任意备份文件或obsolete_template.new。
