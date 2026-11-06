# 更新分发验证

## 2026-11-05 实现范围

采用用户选择的第三方加速源及独立updates分支方案。客户端并行读取签名文件源，
选择较高有效版本，迁移旧API等待，安装包在完整性保护下顺序换源；失败弹窗提供
发布页，等待时禁止无效即时重试。发布工具绑定既有构建/安装验收及官方Latest附件，
非强制更新独立分支。未改变游戏功能、识别配置、模板或输入能力。

签名身份已在登记的本地私有工具状态初始化并DPAPI保护，客户端固定证书摘要及
公开验签样本。没有新增Python依赖、系统证书或付费资源。目录登记、源码、测试及
当前开发/用户说明同步；原v1.5.0安装包、发布内容和历史验收记录保持原样。

原始输出位于本地
`artifacts/tasks/ui/20261105-203016-signed-update-feed-a7b7add9/`。

## 验证记录

本轮新增清单协议、原生验签、双源及下载切换、发布工具的定向用例，并选择既有
更新请求生命周期、缓存、流式下载、HTTPS跳转、取消及弹窗用例。未运行完整测试套件。
首轮80项通过；一个超长参数名称超过Windows环境变量长度，在setup和teardown分别
报错一次。已改用短参数名称，原始失败输出及缓存写入权限警告保留；后续测试使用
受管任务内缓存位置，没有修改既有缓存权限。

第二轮38项通过，覆盖失败参数及随后修改的重定向、等待持久化、迟到回调和对应
网络路径，另核对签名原生扩展缺失拒绝及源程序自检。此轮曾将同一用例的两个阶段
错误误判为两个用例，因此多补跑了一个此前已通过的重复字段参数，超出最小补跑
范围；这是本轮执行偏差，不能以测试通过替代范围合规。该事实明确保留，不扩大
为其他无关测试的补跑理由。

第三轮27项通过，范围为此前未覆盖的缓存损坏/退避/写入失败、错误弹窗的容器初始
焦点和键盘关闭、同版本说明优先级及幂等发布时的远端引用复核。之后两轮各2项，
覆盖深层JSON、默认CLI不远端写入、实际读取响应上限及新增回调的窗口释放；
最后只扩展并补跑1项原生签名集成用例，将真实临时CMS签名直接交给客户端清单
解码器，验证发布端与客户端的字节协议兼容。

各轮有交集，不相加为新总数。按最终参数和选择清单去重，本轮新增62个用例、
选择56个既有用例，共118个不同用例通过。具体函数和参数清单在任务results的
validation-scope.json；仅为核对范围额外执行过collect-only，没有再次执行用例。

原生验签实际使用Windows CryptoAPI；正例、内容/签名篡改及错误签名者拒绝均通过。
测试使用独立临时签名身份验证DPAPI初始化、签名、验签和禁止覆盖，不使用生产私钥
签署虚构的高版本清单。Qt/WGC与验签在同一进程导入验证，没有分进程规避DLL问题。
发布分支测试使用离线边界，覆盖首次/后续窄范围更新、版本回退、同版本冲突、
并发引用变化和Latest变化拒绝；未向远端写入测试数据。

## 真实加速下载

使用正式Qt流式下载及大小/摘要判断路径，完整读取已发布的
E7auto_v1.5.0_Setup_x64.exe。首轮工具会话中断且没有最终报告，日志和部分文件保留，
不计入通过；确认对应进程退出后，第二轮用隐藏的独立验证进程重测。

第二轮直接通过gh-proxy.org完成87,301,153字节下载，耗时537.344秒。SHA-256为
7baabe25e9fa82b00ef99708fec5b73cac0be72141b0c3740890519f0d3e7907，
与既有封存安装包完全一致，Windows文件版本为1.5.0.0。结果位于
results/download-probe.json；下载文件以.bin保留在本任务scratch中，没有作为新分发包。
不以这次耗时宣称比官方来源更快，也不保证其他网络可用性。

验证进程仅为下载旧版样本，替换了安装目录预检及当前版本比较边界，并拦截安装交接；
其余下载、换源、写盘、摘要和资源释放使用生产代码。没有运行安装器、注册安装、
管理员确认、游戏输入或截图，因此这不是编译版安装升级验收。首轮部分文件及
所有原始结果保留，没有重写v1.5.0历史构建证据。

## 变更文件

本轮28个仓库文件，完整摘要清单在任务results/changed-files.json：

- 运行代码：src/e7auto/app.py、core/update_feed.py、platform/update_signing.py，
  以及ui/main_window.py、ui/pages/application.py、ui/styles.py、ui/update_install.py、ui/updates.py。
- 发布及目录：scripts/project/layout.json、scripts/release/update-signing.ps1、
  scripts/release/update_feed.py、scripts/release/verify_release.py。
- 测试：tests/core/test_update_feed.py、tests/helpers/updates.py、tests/platform/test_update_signing.py、
  tests/scripts/release/test_update_feed.py，及tests/ui下的test_navigation.py、test_update_install.py、
  test_update_sources.py、test_updates.py。
- 文档：docs/README.md、design/ARCHITECTURE.md、development/DEVELOPMENT.md、INSTALLER.md、
  RELEASE_CHECKLIST.md、UPDATE_DELIVERY.md、user/使用说明.md及本记录。

## 交付边界

源码实现不代表已经部署：本轮没有创建或更新远端updates分支，没有新增CI、Git提交、
推送、构建或Release。首次联网签名清单验证及新安装包真实升级全链路留待后续发布
授权与验收。项目版本保持1.5.0；没有将本次改动写入已发布版本的历史CHANGELOG。
签名私有状态及任务产物不纳入Git，本地pip.ini保留。

最终28个文件中的18个Python文件通过AST检查；Git差异空白、工作区布局、源码
依赖及Markdown本地链接检查通过，暂存区为空。收尾Git远端读取遇到TLS握手失败，
随后通过官方GitHub API只读确认updates分支不存在；没有远端写入。
