# v1.5.1 工作区审查、构建与验证

## 2026-11-06 版本范围

从 HEAD `dade104debd1b3b0acf4a28e9c36b98b8c99e389` 核对当前全部工作区改动：
23个已跟踪变更、12个未跟踪文件，其中本地`pip.ini`保留，不提交、不作为打包输入。
其余34个仓库文件由已完成的更新分发实现和社区统计入口迁移组成；本轮没有改变
游戏窗口准备、功能流程、识别算法、模板或输入能力，也没有修改依赖锁定版本。

| 改动 | 范围与对应记录 |
| --- | --- |
| 更新查询与下载 | 独立updates分支签名清单；加速与官方来源、来源等待、旧缓存迁移、失败发布页入口及诊断记录；见[更新分发说明](../../development/UPDATE_DELIVERY.md)和[专项验证](../../validation/common/UPDATE_DELIVERY_VALIDATION.md) |
| 发布身份与工具 | Windows原生验签、固定公开信任身份、DPAPI本地私有签名状态及显式清单发布工具；Release仍只交付标准安装EXE |
| 社区统计入口 | 配置和README切换至`https://stats.e7helper.com`；Wrangler同步自定义域名及旧入口关闭状态；已有待传队列保留与补传验证见[统计验证](../../validation/shop/SHOP_STATISTICS.md#2026-11-06-自定义域名切换与旧生产入口关闭) |
| 版本与用户说明 | 统一项目、程序、README和随包说明为1.5.1；新增本版CHANGELOG、旧统计地址用户覆盖升级提示及本记录 |

源码、测试、工具、配置和文档已作差异审查，没有发现阻断本次构建的问题。历史版本
CHANGELOG、v1.5.0发布内容及原始验证记录保持原样；本轮没有重新部署统计服务、
修改D1、仓库变量或CI，也没有向统计生产池写入模拟数据。

审查及本轮定向检查的原始资料位于本地
`artifacts/maintenance/20261106-125558-release-v151-preparation-3a45e67d/`。
`scratch/workspace-before.json`保存开始时的全部文件摘要和来源划分；私有配置仅记录
节名、键名和文件摘要，没有输出配置值。

## 复用与实际执行的验证

开始时的28个更新分发文件与2026-11-05专项记录逐字节一致，已核对各自SHA-256。
因此复用原新增62个参数用例及选跑56个既有用例，共118个不同用例的通过结果；
具体范围、原生CMS签名集成、双源换源、缓存、取消、迟到回调、弹窗和发布边界见
专项记录及原任务的`results/validation-scope.json`。原专项的一次多余补跑偏差
保留在原记录，本轮不重复执行这些未变的用例。

社区统计复用先前实际通过的
`test_retired_endpoint_404_preserves_record_and_resumes_after_restart`：本地真实404
保留记录及退避，重新打开SQLite后记录和UUID不变，改用新主机名并到达重试时间后
成功补传，只在有效确认后删除。测试源码与原通过记录相符，本轮没有重跑；此前的
线上新入口200、旧GET/POST 404证据按原范围引用，不将其解释为本轮重新联网或
新的大陆直连验证。

版本同步后，仅离线更新`.venv`中的项目可编辑安装元数据为1.5.1，没有下载或升级
第三方依赖。本轮没有新增测试，只运行`tests/scripts/release/test_version_sync.py`
中的四个既有函数，4项通过（7.64秒）：

- `test_active_source_document_and_environment_versions_are_synchronized`：项目、源码、环境及活动文档版本一致。
- `test_source_self_check_reports_the_project_version`：源码自检报告当前项目版本，保留源码安装身份边界。
- `test_environment_verifier_requires_exact_versions`：锁定依赖及项目元数据与当前环境一致。
- `test_future_build_embeds_and_verifies_windows_version_metadata`：本次版本的Windows数字映射和不一致拒绝。

本轮20个变更Python文件通过AST语法检查。构建前工作区目录、源码依赖边界、
Markdown本地链接及`git diff --check`通过。未运行全套测试，也没有因提交准备
重复运行已通过且未变的用例。

## 本机构建

使用现有Nuitka 4.1.3、登记的GCC 15.2.0和Inno Setup 6.7.3构建，构建ID为
`20261106-125947-installer-40b1e6f9`。编译376个C文件，366个缓存命中、10个
重新编译；原生运行库归一化、完整payload冻结和安装EXE封存通过，构建退出码0。
构建入口再次核对项目、源码和本地环境元数据均为1.5.1，锁定依赖缺失及不符列表为空。

- 安装包：`E7auto_v1.5.1_Setup_x64.exe`，87,379,001字节。
- SHA-256：`577007bacbe1fdad5e10a2b6da5db632afc19b8961f653f9d07e032ce7bb6bb3`。
- 输入快照摘要：`c4f9ebaf57189f8470ed119729328379598f098a707a1479460a5d6f7d645dbd`。
- Windows版本API实际读取安装EXE的文件和产品数字版本，均为1.5.1.0。
- 封存payload包含`win32crypt.pyd`；签名私钥不在payload中。

构建及安装验收资料统一归属本地
`artifacts/releases/v1.5.1/20261106-125947-installer-40b1e6f9/`。
Nuitka中间目录已按构建工具规则清理。新包先保留在本次staging/setup，安装验收完成
后才提升到dist；没有提前替换已验收的旧包。

## 本机完整安装验收

只读范围计划选择`full`，原因为`lifecycle_scope_changed`：版本、受保护输入、
payload文件名和受保护payload发生变化；不复用原安装包的生命周期结论。
安装前只读确认本机没有现有产品登记、安装通知身份或同名开始菜单/桌面快捷方式。
首次Windows管理员确认被取消，验收进程尚未启动；原始取消结果保留。用户要求
重新发起后，实际管理员进程绑定同一run-dir在隔离测试目录完成验收，退出码0，
14项检查全部通过，`problems=[]`，没有复用旧包的安装生命周期结果。

实际覆盖中文空格路径、不选桌面图标、开始菜单入口、逐文件安装内容核对、运行中
安装/卸载拒绝、同版本覆盖保留logs/state、清理停用文件、选择桌面图标、卸载清理
程序数据和登记、保留无关用户文件、删除空测试根目录及环境变量前后不变。

首次安装与同版本覆盖后均完成真实编译版自检：`compiled=true`、`version=1.5.1`、
`wgc_importable=true`、`update_signature_ready=true`、`update_installation_valid=true`、
`statistics_runtime_ready=true`，签名和更新安装预检错误为空。配置、模板、UI资源、
随包说明、原生运行库和封存payload逐文件核对通过；签名验签与Qt/WGC位于同一
编译程序进程，没有分进程绕过原生库共存问题。

测试副本已卸载，安装登记、通知身份及测试快捷方式已清理。验收全部通过后，将
新包提升至`dist/E7auto_v1.5.1_Setup_x64.exe`，按规则删除dist直属旧标准包
`E7auto_v1.5.0_Setup_x64.exe`；没有创建备份或清理其他文件、历史发布证据和缓存。
本次staging已清理，实际安装与卸载日志、各次编译版自检和原始报告保留在本次
release记录目录。

## 修改文件与建议提交

本轮版本整理修改7个仓库文件：`pyproject.toml`、`src/e7auto/__init__.py`、
`README.md`、`CHANGELOG.md`、`docs/user/使用说明.md`、`docs/README.md`及本记录。
加上开始时的更新分发与统计迁移，建议交付范围共38个不同仓库文件，排除`pip.ini`。

拟拆分为三个提交：更新分发、统计地址迁移、1.5.1版本与说明。README、文档索引和
随包使用说明按实际内容拆分暂存；不为拆分改写最终文件或触碰未获批准内容。
精确文件分组、共享文件段落及完整commit消息草稿保存在本次maintenance任务的
`scratch/proposed-commits.json`。本轮未暂存、提交或推送。

## 覆盖限制与后续发布

本机完整安装验收通过不等于真实公网更新闭环通过。当前尚未发布新的GitHub Release或updates清单；
最终提交一致性检查须在获准提交后执行，线上签名查询和本版安装包完整下载须在
正式清单发布后验证。原专项87,301,153字节的v1.5.0加速下载结果仅作为原样本的
下载实现证据，不能替代新包的公网下载或编译版升级闭环。

未执行完整游戏流程、游戏输入或截图、真实公网更新退出/重启/清理全链路、
干净Windows虚拟机或其他系统环境验证；v1.5.0记录的游戏适配与历史用例限制继续
沿用，不将本轮版本检查解释为这些范围已通过。
