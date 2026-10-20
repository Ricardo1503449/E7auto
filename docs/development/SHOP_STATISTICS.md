# 刷商店统计后台开发与首次部署

源码在`scripts/cloudflare/shop_stats/`，属于shop的配套部署工具。客户端不导入scripts，不增加第三方运行依赖。工具需要Node.js 22及以上；Wrangler及其配套Miniflare精确版本见package.json和package-lock.json，只用于开发/部署，不打包进E7auto。

## 本地工具与测试

设置页统计计数读取失败时，控制器返回None而非零条。页面保留最近一次可信计数并明确标为上次记录；尚无可信结果时显示暂不可读取。计数区域的异常不覆盖上传反馈或其显示计时。开启统计且不忙时仍可点击重试，但必须由原retry流程实时读库确认，不能以旧显示计数授权上传；后续成功读取恢复正常显示。

客户端ReportQueue的每次操作仍在调用线程创建并关闭独立SQLite连接。counts/next_due只执行读取，不申请BEGIN IMMEDIATE；新增、保存、确认删除、退避及取消等写操作继续使用BEGIN IMMEDIATE和synchronous=FULL。首次使用只读检查已有表字段与索引，完整结构不申请写锁；新库或旧结构才在独立写事务中创建/迁移，成功后记录本实例已准备的数据库文件身份，失败回滚并允许下次重试，文件被重建/替换时重新准备。容量检查与插入保持同一写事务，不跨线程共享连接，也不改变统计内容、上传协议或业务异常隔离。

先分配shop任务，记录返回目录，以下`$statsTask`使用该实际目录。Node/npm使用tools/state/tool-paths.json中的外部路径，或显式传入--node/--npm-cli；不复制运行时、不修改系统PATH。私有配置schema_version为1，node_executable及npm_cli分别保存node.exe与npm/bin/npm-cli.js的实际路径；配置无效时修正配置，不隐式切换到其他版本。

```powershell
.\.venv\Scripts\python.exe -B -m scripts.project.artifacts --kind tasks --feature shop --subject shop-statistics-tools
.\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.tools install --run-dir $statsTask
.\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.tools stage --run-dir $statsTask
$statsPaths = .\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.tools locate | ConvertFrom-Json
```

Wrangler/Miniflare安装在tools/cloudflare/shop-stats，缓存固定在tools/cache/npm。安装必须具备一致的package.json和package-lock.json，使用npm ci，没有自动npm install回退。stage、测试库和日志仍进入任务scratch，不在源码目录生成node_modules或.wrangler。工具安装不会清理私有授权状态，客户端依赖锁不变。测试环境变量和命令：

```powershell
$env:SHOP_STATS_TOOLCHAIN = $statsPaths.toolchain
$env:SHOP_STATS_TEST_OUTPUT = Join-Path $statsTask 'scratch/worker-test'
$env:SHOP_STATS_NODE = $statsPaths.node
& $statsPaths.node --test tests/scripts/cloudflare/test_shop_stats.mjs
.\.venv\Scripts\python.exe -B -m pytest tests/core/test_shop_report.py tests/logging/test_report_queue.py tests/automation/test_shop_reporting.py tests/ui/test_shop_reporting.py tests/scripts/cloudflare/test_snapshot.py
.\.venv\Scripts\python.exe -B -m scripts.project.check_layout
```

本地D1使用真实workerd/SQLite验证，测试调用创建新测试库标识保留之前证据，不连接生产D1、不发送游戏输入、不截图。Miniflare 5通过其官方导出的选项转换器使用明确的模块列表；SQL迁移按完整语句及触发器加载，不能把多行迁移直接交给逐行解释的exec。

## 首次部署

这些步骤需要维护者注册并登录自己的Cloudflare账号，并按项目授权规则批准实际部署；源码和本地测试可以先完成。仅使用Workers/D1免费方案，不购买域名，不开通付费资源。不要把账号密码、验证码或API令牌提交到仓库。

1. 使用登记工具目录中的Wrangler完成用户自己的登录授权，统一run入口将XDG_CONFIG_HOME指向tools/state/cloudflare，日志进入任务scratch。账号选择信息保存在同一私有状态目录的account.json，不提交、不打包。创建名为e7auto-shop-stats的D1数据库，记录平台返回的真实数据库UUID。
2. 用配置工具回填该UUID，再重新stage：

```powershell
.\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.configure --database-id $statsDatabaseId
.\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.tools stage --run-dir $statsTask
```

3. 仅在授权首次部署后，使用统一入口执行`tools run --run-dir <实际任务> -- d1 migrations apply e7auto-shop-stats --remote`，再执行`tools run --run-dir <实际任务> -- deploy`；完整模块前缀为`python -B -m scripts.cloudflare.shop_stats.tools`。入口使用固定工具/授权目录和任务stage，关闭Wrangler遥测，日志进入任务scratch。日常展示更新不重复执行数据库迁移。只做预检查时传入`deploy --dry-run`。不要在数据库ID尚为空时部署，也不使用临时假UUID替代生产ID。
4. 取得平台实际返回的HTTPS workers.dev根地址，读取/v1/shop-stats和/stats.svg检查服务。再回填客户端配置及README实时图片：

```powershell
.\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.configure --endpoint $statsEndpoint
.\.venv\Scripts\python.exe -B -m scripts.project.check_layout
```

配置工具只改统计地址和README标记块，不重写游戏几何/YAML；初始地址为空时只保存本地记录。默认域名不保证中国大陆直连，需分别记录无代理、系统代理及雷神实际模式下的连接结果。加速游戏成功不作为统计连接成功的证据；不向生产池写人工测试计数。

## GitHub每日快照

实现由[工作流](../../.github/workflows/shop-statistics.yml)、[快照生成器](../../scripts/cloudflare/shop_stats/snapshot.py)和[分支发布器](../../scripts/cloudflare/shop_stats/publish.py)组成。工作流在GitHub托管的Ubuntu运行器上使用Python 3.12和Node.js 24执行，不需要保持开发电脑开机，不运行游戏或E7auto客户端，也不需要安装Wrangler或连接D1管理接口。

每次只读请求一次服务的`/v1/shop-stats`，校验两个策略池的字段、累计值和比率，再由同一份JSON通过render.mjs及views.mjs生成SVG。保存的是执行时的累计汇总，日期按北京时间计算；不是当天新增量，也不自动补算漏跑日期。同一天重跑会更新该日期文件，之前提交仍留在Git历史中。

| stats分支文件 | 内容 |
| --- | --- |
| latest.json、latest.svg | 最近一次成功快照的汇总及图表 |
| history/YYYY-MM-DD.json、history/YYYY-MM-DD.svg | 对应北京时间日期的汇总及图表 |
| README.md | 图表入口、快照日期和统计口径 |

本地生成的snapshot-files.json仅作这五个文件的发布白名单，不上传。首次运行创建没有源码历史父提交的独立stats分支；之后基于该分支原tree保留旧日期文件，以非强制方式更新引用并核对返回提交。源码默认分支不会被发布器修改。

### 启用顺序

以下步骤会配置远端并产生统计提交，须先获得对应授权；文档核对或本地测试不代表已经启用。

1. 将工作流及它依赖的scripts模块提交、推送到目标仓库的默认分支。不能只上传YAML：snapshot.py、publish.py、render.mjs、views.mjs、scripts.common.paths及包入口都必须可导入。定时任务使用默认分支，手动触发也要求工作流已存在于默认分支。[GitHub触发规则](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
2. 在仓库`Settings → Secrets and variables → Actions → Variables`新增**仓库变量**`SHOP_STATS_API_URL`，值为真实HTTPS服务根地址，例如`https://e7auto-shop-stats.e7auto-stats.workers.dev`，不附加`/v1/shop-stats`。只在本机config中填写地址不会配置GitHub变量；变量缺失或为空时整个snapshot作业跳过。[变量配置](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-variables)
3. 确认仓库允许GitHub Actions及checkout/setup-python/setup-node，保留工作流声明的`contents: write`。发布步骤使用GitHub为本次作业提供的`GITHUB_TOKEN`，不需要个人长期令牌或Cloudflare管理密钥。组织策略、分支保护或ruleset若拒绝创建/更新stats，应先由维护者审查权限，不改为强制推送。[令牌权限](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)
4. 在`Actions → Shop community statistics snapshot → Run workflow`选择默认分支，手动运行一次。[手动运行说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
5. 核对任务成功、stats分支实际提交与发布器输出一致，且五个文件齐全；latest与当天history的JSON/SVG内容一致、保留旧日期、没有新增源码或逐次上报明细。比较必须使用本次快照的数据，不能要求它与之后又收到上报的实时接口完全相等。首次成功后，README中的每日快照链接才有对应分支可访问。

工作流安排每日UTC 18:17，即北京时间次日02:17；日期由实际执行时的北京时间决定。同一仓库的快照作业串行排队，不取消正在运行的任务，单个作业上限5分钟。GitHub定时触发可能延迟或漏跑，公共仓库60天无活动时还可能自动禁用，不能承诺每天准点；维护者应查看Actions执行记录。[定时限制](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

### 失败与复核

- 请求失败、重定向、超过32 KiB的响应、非法汇总或渲染失败都会使生成步骤失败，后续发布步骤不执行，保留已发布快照，不用零值替代失败结果。
- 发布器只接受白名单文件。更新stats引用失败时不强制覆盖；若最后的引用核对失败，先只读查看实际分支，因为分支可能已经更新，不能仅凭任务失败认定远端未改变。
- 没有新的上报时，快照仍可成功保存相同累计值；updated_at是服务汇总更新时间，不是任务运行时间。每日快照和README实时图表是两条展示路径，图片缓存不影响stats分支里的文件记录。
- 首次启用和后续真实执行结论记入统计验证文档，原始请求、Actions结果及引用核对留在shop管理任务的results；不要在README或通用发布检查单中逐次追加运行记录。

手动生成本地快照默认进入新shop任务results，也可以明确指定项目外导出目录。`publish`不带--publish只检查文件清单，不写GitHub：

```powershell
.\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.snapshot --endpoint $statsEndpoint --node $statsPaths.node
.\.venv\Scripts\python.exe -B -m scripts.cloudflare.shop_stats.publish --snapshot-dir $statsSnapshot
```

这两个命令只生成本地快照并检查发布清单；真实远端执行仍以获授权后的工作流运行和分支核对为准。

## 安装版交付

服务地址定稿后再构建发布输入，沿用项目构建捕获/封存流程。新增模块将使SQLite扩展及运行库进入安装包，需核对实际payload并验证编译版写入、恢复队列及HTTPS上传；按现有工具判断生命周期验收范围，不能用源码测试代替安装验收。上报成功后删除记录，不删除数据库文件；升级保留、卸载清理沿用state规则。

安装版`E7auto.exe --self-check`在独立临时目录验证SQLite写入、队列恢复与确认删除，并检查Qt TLS后端，不接触用户待传记录。`--self-check-statistics-https`另通过配置地址只读GET汇总接口验证真实HTTPS，跟随系统代理，超时10秒；不上传模拟记录、不发送游戏输入。网络失败须单独记录，不将源码联网通过视为安装版验证。
