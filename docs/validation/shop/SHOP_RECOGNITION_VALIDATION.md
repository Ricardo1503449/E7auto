# 商店识别改动与验证记录

## 2026-10-23 移除临时分项计时和离线汇总

按用户要求删除仅用于优化诊断的滚动分项计时与离线汇总工具，版本保持1.4.27。删除scroll_timing.py、summarize_shop_scroll.py及其接入，移除截图/场景/邻帧/首尾/位移/等待计时包围、候选评分耗时与诊断用匹配调用计数、额外截图计数差值和性能批次/对齐采样统计字段。普通scroll_settle_trace仍记录成功或超时的识别证据及总有效耗时；中断保留原停止原因和通用阶段日志，trace写入失败不改变滚动结果。

业务仍使用active_monotonic维护参考帧年龄、采样间隔和总期限。已实现的WGC客户区复制、场景准备复用、第三帧排程、购买后局部扫描及原图多行候选评分保留；模板、ROI、阈值和配置未修改。对修改前快照移除纯诊断语法后作AST对照，9个视觉函数及滚动主流程主体一致，未改变两种尺寸路径的运算或控制表达式。

实际运行5个直接相关测试模块：滚动集成、确认/超时守卫、库存就绪、原图商品行及重叠位移。首轮94项通过、4项失败；使用修改前的contracts/timing/vision/scrolling/flow快照复核，四项以相同原因失败。随后仅修正这些测试的旧假设：两个等待序列改用1纳秒容差；视口替身按生产路径将滚动稳定检查委托到底部视口，允许后续扫描复用证据；真实首张底部样本按当前20%变化上限纳入三帧窗口并断言保留该原帧。四项复测全部通过，合计98个独立用例获得通过结果，未重跑已通过的94项。新增覆盖三/四帧排程中的实际取图与比较耗时，以及trace输出失败时成功/超时结果保持，共4项，已计入98项。

证据位于本地`artifacts/tasks/shop/20261023-233734-remove-scroll-timing-af559c62/results/`：scroll-tests、before-failures及corrected-tests分别保存首轮、修改前复现与修正后的输出；algorithm-preservation.json保存AST对照结论。修改前源码和被删除工具保存在该任务scratch/before，原有日志、历史结果及其他未提交修改保留。

工作区目录/本地链接检查通过，problems为空；版本/文档预检返回1.4.27。运行源码、脚本、测试及当前设计文档已无退役计时接口或工具命令引用；本轮差异格式检查通过。版本声明与元数据未变，复用同日已通过的6项版本同步测试，未重复执行。

本轮未运行全套、游戏输入、WGC实机捕获或安装包构建，不据此宣称已有优化完成全部实测。下方分项计时、离线统计命令和批次分析属于历史实施记录，所述工具现已退役，历史输出不改写；其他优化的实测缺口仍按各批次原记录保留。

## 2026-10-22 全位置购买后扫描优化（实施，待实测）

按用户批准方案：第1～3行只有购买前确认同屏还有后续目标时，购买成功后使用新帧局部核验剩余行；顶部最后一个目标直接完整扫描，局部扫空也必须恢复完整扫描，只有完整干净窗口能够生成滚动参考。第5行后只核验第6行；最后底部槽位成功后不重扫商品，稳定确认店铺控件后交回原预算/刷新/策略/退出流程。第1行购买结果确认、第4行后的滚动参考规则及所有门槛不变。

实现为默认None的slot_ids识别接口和调用内_PostPurchaseScope。范围必须是已校准同屏剩余后缀、完整可见且不包含顶部第一行；商品模板搜索、纹理、邻帧/首尾和原像素检查使用同一组剩余行，局部相位ROI从配置商品框计算。reward_overlay_roi不是完整提示遮罩；只将其作为配置范围不得侵入的额外边界，局部策略仍限定于既定第2～6行场景。局部帧不消费滚动缓存、不产生滚动参考；投影缓存纳入行集合，避免范围切换复用错误坐标。

处理进度不伪记购买，且绑定库存证据代次、恢复代次、窗口/变换/客户区、槽位布局和启用目标。购买前稳定匹配必须与配置坐标、顺序一致，购买后只允许purchase本身的一次证据失效；网络恢复或其他变化使快捷范围撤销。局部扫描前、取图后及计算后复核，失败恢复完整窗口且不重置该扫描期限。快速收尾期间失效则退回完整扫描；停止和识别超时继续按原规则结束。

日志新增post_purchase_scan_selected、post_purchase_scope_fallback、post_purchase_full_reference_required、bottom_purchase_fast_finish；inventory_scan阶段增加请求/实际范围及回退原因，post_purchase_finish记录控件收尾耗时，批次标记scoped_followup_v1。不增加逐环节计时或额外诊断识别。

修改前依据：13:07:14实测第5行购买后20次扫描平均2030.652ms、第6行5次平均2029.258ms，合计约50.8秒；这是全部后续扫描时间，不等于可节省时间。第1行24次购买结果平均1862.417ms且均为success_button，保持原确认规则。本轮没有同屏多目标样本，不能据此宣称顶部连续购买已经验证。

遵照用户要求不运行测试、图像回放或游戏操作；仅适配现有识别测试替身的可选范围参数，并执行语法、配置、差异及目录静态检查。购买结果确认与核心商品匹配函数通过源码AST核对，不把静态检查当作运行验证。待用户覆盖顶部同屏多目标/单目标、第4行滚动参考、第5行后有/无第6行目标、第6行刷新/正常结束，以及恢复、金币不足、手动停止。非基准尺寸和其他未出现情况仍未验证。

本批文件：src/e7auto/features/shop/contracts.py、inventory.py、vision.py；tests/helpers/vision.py、tests/automation/test_inventory_readiness.py；CHANGELOG.md、docs/design/ARCHITECTURE.md、BEHAVIOR.md、NATIVE_MATCHING.md及本记录。修改前副本在既有商店任务scratch/post-purchase-before。出现新漏购、误购或提前刷新，按本批差异回退，保留此前三项优化。未提交、推送或构建。

## 2026-10-22 截图、场景准备与采样排程三项优化

按用户批准的三项方案同批实施，不再以新增细分计时或原占比门槛作为前置。配置保持1000ms、3帧/200ms、20%变化率、0.75商品分数、0.05领先差值，全部候选、原始像素核验和输入保护保留。

- WGC在锁定的位图缓冲区内校验步长/容量、裁去ContentSize外填充并按原客户区定位复制，输出有独立内存的连续BGRA数组。省去整窗中间数组；完整位图工具接口保留，停止及复制后帧龄核验保持。内部capture_copy_ms现在包含客户区裁剪。
- ShopVision缓存顶部/底部固定布局和当前几何的行投影；配置或客户区变换、尺寸、原点变化时重建。奖励浮层ROI每次调用只准备一次连续BGR，保留原父帧变换及全局原点，全部目标依次调用原匹配入口。商品行灰度、纹理判定及逐行裁剪越界规则不变，缓存不保存帧或识别结论。
- 三帧窗口且要求两次稳定观察时，连续两帧通过后的第三次采集对齐max(首帧完成+稳定跨度,第二帧开始+采样间隔)；其他情况保持原排程。已有计算计入等待，仍以原有效时钟和截止时间复核全部条件。

新trace的performance_profile为capture_scene_sampling_v1，排程为stable_window_target或capture_interval，window_aligned_samples记录实际完成的对齐采样数。成功、超时及中断仍只汇总一次。统计工具按批次、场景、客户区尺寸、采样策略、三帧/补采分组，关联成功滚动后首次底部扫描并记录行号。旧日志缺标记时归为unmarked，不推断版本；新增缺失数值不补零。

对比基线为10月22日10:43:52日志：101次滚动全部通过，确认平均529.743ms、P95 547ms、最大765ms，正常三帧100次、五帧1次。该结果来自修改前实测，不作为本批运行验证。用户后续完成至少一轮/50次滚动，比较正常三帧耗时、补采比例、失败原因及底部扫描，并核对首次进入、购买后、刷新后和重进场景。新误判或截图异常回退本批相关改动，不能归因时回退本批三项；保留之前修复。

遵照用户要求，未运行自动化测试、图像回放、统计脚本实跑或游戏操作。仅适配现有WGC测试替身的借用缓冲区/直接客户区复制入口，以及既有滚动日志断言；本批执行语法、配置、差异及目录静态检查。实际提速、其他功能、非基准尺寸、手动停止和网络/截图异常运行行为待实测，不复用旧结果冒充验证。未提交、推送或构建。

本批文件：src/e7auto/platform/wgc_capture.py；src/e7auto/features/shop/vision.py、scrolling.py；scripts/validation/summarize_shop_scroll.py；tests/platform/test_wgc_capture.py、tests/automation/test_scrolling.py；CHANGELOG.md、docs/design/ARCHITECTURE.md、BEHAVIOR.md、NATIVE_MATCHING.md及本记录。修改前副本保存在既有任务scratch/performance-three-before，可按本批差异恢复，不能用Git整文件回退覆盖此前修改。

## 2026-10-21 滚动分项计时与离线统计（首批实施）

按用户批准的完整计划，本批只实现分项计时和日志统计。保持1000ms上限、3帧/200ms、20%变化率、0.75商品门槛、0.05领先差值及最多四个相位提案加零位移；不实施预处理复用、评分早停或候选数量削减。

生产ShopFlow为一次滚动注入可选ScrollTimingRecorder及原始截图/视觉累计计数器。计时上下文仅在该次调用内有效，所有出口复位；只保存计时数值，不保存截图。ScrollMovementObservation.timings默认为空；ScrollContentCandidate.score_ms、scored_rows默认未记录，后者是实际执行matchTemplate的行数，不是达标行数。原评分函数只增加计时包围和返回元数据。

每次滚动只汇总一次scroll_settle_trace，正常结果保留原字段，中断补记已完成和已开始环节的耗时及停止原因，未开始确认的记录显式标注。timing使用schema=1；capture、scene、neighbor、span、movement、wait_initial、wait_poll为互斥顶层耗时，capture.raw/capture.network_vision及vision_*均为嵌套子项，不重复求和。wheel_*在确认期限外；operation_wall_ms、settle_wall_ms与原有效期限各自保留。部分无法归属的控制/调度/计时开销保留为unattributed_settle_ms，不伪称全部为睡眠。

新增scripts.validation.summarize_shop_scroll，只读显式指定的日志，输出平均值、中位数、最近秩P95、最大值、调用/评分行数、成功/失败原因、已记录稳定窗口和配置参数。按首次进入、购买后、刷新后及重进分组，并分开客户区尺寸；购买后优先于重进，重进优先于刷新。旧日志缺项记为未记录，不补零；报告保留输入行号、完整/未完成运行和解析问题，不把日志成功等同于没有漏购或覆盖全部反例。默认输出由任务分配器创建；显式输出须符合既有任务results约定，不覆盖已存在文件。

实测后可从项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -B -m scripts.validation.summarize_shop_scroll logs\run-实测日志名.log
```

沿用用户要求，本批不运行测试、图像回放、统计工具实跑或游戏实测，只做源码/配置/差异及目录静态检查。新增计时数据、工具运行效果与运行开销待新实测确认；此前76次成功仅为原行为背景，不作为本批通过结果。首轮应收集至少一次完整运行且不少于50次滚动，再按计划判断后续优化；不提前实施尚缺证据的阶段。

本批修改：src/e7auto/features/shop/contracts.py、flow.py、scrolling.py、scroll_vision.py；新增src/e7auto/features/shop/scroll_timing.py与scripts/validation/summarize_shop_scroll.py；同步CHANGELOG.md、docs/design/ARCHITECTURE.md、BEHAVIOR.md、NATIVE_MATCHING.md及本记录。修改前副本保存在既有任务artifacts/tasks/shop/20261021-154829-row-content-scroll-verification-8faa608e/scratch/timing-before。未提交、推送或构建。

## 2026-10-21 滚动确认上限提高至1000ms

22:32:35长测完成75次刷新、53次购买，76次滚动全部成功，最后以refresh_strategy_exhausted正常结束。滚动确认平均539.684ms、中位532ms、95%分位594ms、最大625ms，全部使用3帧及1次最终内容核验。这些为调整上限前的实测数据；此前22:31一轮内容已匹配，但确认总耗时844ms，超过原800ms上限。

按用户要求，仅将config/internal.yaml的scroll.settle_ms从800改为1000。仍通过即结束，3帧/200ms、20%变化率、0.05领先差值、所有识别区域及各项检查保持。滚动确认内部进一步优化本轮只提供方案，未修改算法或新增计时实现。

按此前明确要求不运行自动化测试、图像回放或游戏实测，仅核对配置文本与差异；1000ms配置实测待用户完成，不把先前76次成功记录标为新上限的验证。未构建、提交或推送。

本轮修改文件：config/internal.yaml、CHANGELOG.md、docs/design/ARCHITECTURE.md、BEHAVIOR.md、NATIVE_MATCHING.md及本验证记录。

## 2026-10-21 复用确认进度、采样调度及20%变化阈值

用户确认候选排序修改后可正常运行。修改前的21:02:05实测共69次滚动成功、68次刷新，最终手动停止；其中55次刷新未直接复用顶部结果，51次结束时已有1～2帧画面证据。底部69次缓存扫描平均376.5ms，购买后顶部/底部重扫分别平均2012.5ms/2299.3ms。以上为本轮优化前日志统计，不是优化后的性能结论。

本轮按批准的三项方案实现：余额确认后的顶部SceneWindow、商品身份键及连续计数可一次性续接；成功滚动的FrameSample携带SceneSampleEvidence，底部仍执行原SceneWindow状态判断及逐帧目标识别，但复用同一帧已经检查过的场景、邻帧和首尾测量；扫描、刷新后余额确认及滚动改为按采集时间安排下一次采样，计算耗时计入100ms间隔，只睡剩余部分。顶部续接和底部证据受输入代次、恢复代次及最后一帧500ms年龄限制，购买、刷新、进店、恢复和收尾清空；底部证据处理中若失效，丢弃剩余缓存并重新取帧，缺失对应比较证据时使用原测量。

inventory_readiness.maximum_row_changed_fraction按用户要求从0.15改为0.20。3帧、minimum_stable_ms=200、既有识别区域、0.05候选领先差值和800ms滚动总期限保留。新增日志top_continuation_frames、scene_frames_reused、scene_comparisons_reused、sample_wait_ms及poll_schedule=capture_interval，供用户实测核对进度复用、实际计算和等待；不承诺未测量的提速幅度。

沿用用户“不需要测试，我直接实测”的要求，本轮不运行自动化测试、离线图像回放或游戏实测，仅做源码语法、配置文本、差异和目录检查；旧验证不作为本轮新逻辑通过的证据。未构建、提交或推送。

本轮修改文件：config/internal.yaml；src/e7auto/features/shop/readiness.py、scrolling.py、inventory.py、refresh.py、flow.py；CHANGELOG.md；docs/design/ARCHITECTURE.md、BEHAVIOR.md、NATIVE_MATCHING.md；本验证记录。

## 2026-10-21 位移候选按多行质量和领先差值选择

19:07:30开始的实测前23次滚动通过，最后一次在19:11:06停止。最大行变化12.3480%已低于15%，进入内容核验后却返回ambiguous_content。此前用用户顶部截图与停止帧复算：正确约-394像素候选两行分数约0.990，错开一行的约-131像素候选约0.764/0.772；旧逻辑因两者各有两行超过0.75而拒绝。该复算发生在本轮实现之前，不作为新代码验证。

按用户批准的方案，保留A/B/C区域、15%停稳阈值、3帧/200ms、原生像素评分及800ms总期限；不缩框、不接入名称辅助。完整评价所有相位提案及零位移后，候选仍须至少N条完整不重叠商品行达到0.75（N默认2），取其最强N行中的最低分作为质量。最佳候选须领先最强不同位移至少scroll.minimum_content_margin=0.05；按原两倍对齐容差视为同一位移的提案不重复竞争。分数接近继续判歧义；零位移、向下及上移不足仍保留，选中后再执行原位移和变化量门槛。无合格候选时保留首提案得分用于诊断。

ScrollContentCandidate记录编号、基准单位位移、相位响应、每行得分、达标行数及质量。scroll_settle_trace新增minimum_content_margin及content_candidate_checks；后者汇总每次核验的样本号、全部候选、选中/竞争候选编号、领先差值及判断原因，不额外增加逐帧日志。

按用户要求，本轮不运行自动化测试、图像回放或游戏实测；0.05为本轮启用的初值，效果待用户实测。既有通过记录不作为新候选逻辑的验证。仅做源码语法、配置文本和差异检查，未构建、提交或推送。

本轮修改文件：config/internal.yaml；src/e7auto/configuration/models.py、loader.py；src/e7auto/features/shop/contracts.py、scroll_vision.py、scrolling.py；tests/core/test_domain_config_overlay.py（仅同步合成配置的新增字段，未执行）；CHANGELOG.md；docs/design/ARCHITECTURE.md、BEHAVIOR.md、NATIVE_MATCHING.md；本验证记录。

## 2026-10-21 商品行像素变化阈值调整为15%

18:45:50实测的底部第3～5行变化率均为0，第6行依次为7.2509%、3.0490%、0.1718%；原2%门槛使稳定计数重置，800ms内未进入最终位移核验。用户观察到该区域背景有人物翅膀运动，并明确要求将inventory_readiness.maximum_row_changed_fraction由0.02改为0.15。该共享配置同时作用于顶部参考帧就绪、刷新复用及底部停稳；其余判定和等待期限未改。

按用户要求，本轮不运行自动化测试，由用户进入游戏实测。此前2%配置下的测试结果不作为15%阈值的验证结论；15%配置的实测结果待补充。

## 2026-10-21 停稳测量按当前视口对齐

17:46:48实测中，购买后顶部就绪检查通过，参考帧以172ms年龄交接；滚动后场景均为ready，但最大像素变化2.6757%、7.4557%超过2%，稳定计数清零，828ms超时且未进入商品位移核验。本轮只有停止图，无当时连续检测帧；不能确认两个最大值分别来自哪一行，也不能声称离线重放了原失败序列。

确认并修复的代码问题是：停稳像素检查混用了顶部/底部全部六个固定框。当前视口现在显式传递，顶部检查完整第1～4行；底部由已校准行距与底部锚点推导完整第3～6行，排除局部露出的第2行。基准图标主体框分别为顶部y=169/432/695/958、底部y=301/564/827/1090，均x=1002、176×172；这些结果由配置计算，未在算法中硬编码滚动位移或新增ROI数值。整行就绪检查使用同一组完整可见行，避免漏查底部上方两行。配置加载拒绝缺失视口锚点、断裂库存顺序、重复次序及不一致行距/几何。基准、非基准均使用完整客户区变换及原图像素。

RowPixelChange保留行名、实际ClientRect及变化比例。滚动日志按样本记录pair_row_changes、span_row_changes，并附row_regions；扫描和刷新阶段记录末次邻帧/首尾比较明细，失败清空连续窗口时仍保留触发明细。2%门槛、图标内缩、奖励浮层检查、三帧/200ms、已验证参考帧交接及800ms预算保持。

实际执行235个不同相关用例，全部通过；首轮80项、后续179项有重叠，按唯一用例计数。覆盖当前视口、底部第3/4行、配置漂移、局部可见排除、原图及尺寸边界、子帧全局原点、逐行日志与首尾区分，以及库存、刷新、滚动、参考失效和超时回归。新增扰动反例基于最新停止图构造：旧框落入的空隙变化不再否决当前图标；旧框遗漏的底部上方图标变化现在被拒绝。扰动是合成验证，不是原实测时间序列。未运行全套，前阶段未变的验证仅作背景，不自动累计到235项。

三种尺寸各3次实际时钟离线回放共9次，滚动后378～523ms，均低于800ms。回放使用此前保留的同库存原始图像区域，复制模拟新帧，识别和等待实算；无真实WGC或游戏输入，小尺寸为派生样本。最新停止图的静态场景及原图坐标已验证，仍需实测确认该轮动态超限是否消失。

证据沿用artifacts/tasks/shop/20261021-154829-row-content-scroll-verification-8faa608e/，本阶段文件以viewport-命名：viewport-initial.xml、viewport-regression.xml、viewport-replay.json、viewport-completion.json。scratch/viewport-before保存本阶段修改前副本，之前阶段的证据保持。最新停止图只保存必要无损区域到inventory_viewport_latest.npz，同名JSON绑定来源、区域与像素摘要。无正式模板变更、实机操作、构建、提交或推送。

本阶段文件范围：

- `CHANGELOG.md`
- `docs/design/ARCHITECTURE.md`
- `docs/design/BEHAVIOR.md`
- `docs/design/NATIVE_MATCHING.md`
- `docs/validation/shop/SHOP_RECOGNITION_VALIDATION.md`
- `src/e7auto/configuration/loader.py`
- `src/e7auto/configuration/shop_layout.py`
- `src/e7auto/features/shop/contracts.py`
- `src/e7auto/features/shop/inventory.py`
- `src/e7auto/features/shop/readiness.py`
- `src/e7auto/features/shop/refresh.py`
- `src/e7auto/features/shop/scroll_vision.py`
- `src/e7auto/features/shop/scrolling.py`
- `src/e7auto/features/shop/vision.py`
- `tests/automation/test_inventory_readiness.py`
- `tests/automation/test_scrolling.py`
- `tests/core/test_domain_config_overlay.py`
- `tests/core/test_shop_viewport_layout.py`
- `tests/fixtures/shop/inventory_viewport_latest.json`
- `tests/fixtures/shop/inventory_viewport_latest.npz`
- `tests/helpers/shop_scenes.py`
- `tests/helpers/vision.py`
- `tests/vision/test_inventory_readiness.py`
- `tests/vision/test_shop_viewport_stability.py`

## 2026-10-21 商品区就绪与滚动参考帧

16:07:42运行确认上一阶段按行核验已经生效：前8次滚动通过，第9次在购买友情点数后仅一行通过（0.708199、0.986419）。停止图保留奖励图标、文字及蓝色光效；随后补充的顶部图与停止图局部对齐，友情点数整行约0.702，下部未受影响区域约0.995。补图存在约1像素布局差异，并非本轮历史before帧，不能把该对照数值当作日志中未保存的原始逐帧评分。

用户确认同时修复基准、非基准下的刷新/购买过场及滚动参考帧。新增feature内的SceneWindow，将商品身份三帧一致与画面就绪分开：商店控件可见、奖励浮层缺席、配置槽位三段纹理充分，才积累至少3张新帧及200ms跨度。邻帧与首尾帧均检查既有位移/响应，以及每个图标主体的原始BGR变化比例。首尾检查用于缓慢漂移；静止奖励浮层由专用ROI内的现有三种商店商品模板单独拒绝，不以不动作为消失证据。新增检测不加载其他功能资源、不登记新PNG。

inventory_readiness配置：minimum_frames=3、minimum_stable_ms=200、difference_threshold=8、maximum_row_changed_fraction=0.02、pixel_inset=[32,24]、minimum_band_stddev=5、reward_overlay_roi=[960,90,250,215]、reward_overlay_confidence=0.90、reference_max_age_ms=500。已购买灰态友情点数上段纹理标准差约7.236，因此新分段门槛采用5，整行仍须达到原8。两张相隔约29秒的同库存停止图在搜索框边缘有6.826%的变化；像素检查内缩后最大为0.899%，保持2%门槛。该对照只用于区分图标主体与背景，不充当短时连续帧证据。完整行检查、最终位移核验仍使用原ROI；内缩仅作用于停稳像素测量，按父帧真实比例一次投影。

顶部扫描只交出最后一张已验证原帧，滚动不再另取before。刷新后余额确认仅在画面和商品身份共同通过时复用顶部结果及同帧引用；余额已成功而画面未就绪时交由原顶部扫描继续有界等待。购买、刷新、重入、网络恢复、参考过期及收尾失效旧证据。取帧期间发生恢复的旧帧直接丢弃；滚动后恢复使原参考失效时停止，禁止把滚动后画面重新设为before。滚动前复用原扫描3000ms有效期限；仅本功能参考帧交接检查采集调用返回后的年龄（500ms），滚动后800ms仍包含识别计算，失败不重开预算。停止、窗口和输入守卫保留，购买计数及两行内容核验门槛不变。

诊断增加scroll_reference_accepted（来源、年龄、恢复代次），原scroll_settle_trace增加scene_states及各次邻帧/首尾比较的图标主体最大变化。inventory_scan和refresh_balance_wait记录就绪帧数及原因。无自动截图或额外游戏输入，窗口和界面不作本轮修改。

本阶段实际执行263个不同用例，各用例最终结果全部通过，重复运行不重复计数。覆盖：真实清晰/奖励浮层图及派生尺寸、基准边界、静态浮层、空白/半展开/强淡出构造反例、局部颜色变化与2%边界、慢漂移、重复时间、恢复、停止、超时、参考精确交接与过期、刷新结果复用、购买后失效、原滚动/库存/刷新/会话和配置边界。首次回归的3项旧断言按新增日志、首尾比较和真实首张图仍有变化的行为更新；另一个真实背景变化反例促使像素测量区域收紧。后续只重验受影响模块，未变用例复用本阶段通过结果；上一阶段159项未直接并入263项，未跑全套。

最终版本在2322×1306、1920×1080、1536×864各进行3次实际时钟离线回放，共9次：滚动前就绪及商品扫描935～1794ms，交接参考年龄255～428ms，滚动后391～568ms，均满足各自期限。回放使用固定原始区域复制成模拟新帧，识别与等待实算；商品区外未参与检测的像素填零，小尺寸为离线派生。没有真实WGC、新游戏输入或刷新/购买动画逐帧序列，不能替代实机全流程验收。真实奖励浮层拒绝与正常静态图接受已经离线验证，实际动画全过程仍需后续实测。

证据沿用任务artifacts/tasks/shop/20261021-154829-row-content-scroll-verification-8faa608e/，本阶段结果均用readiness-前缀；readiness-replay-final.json绑定最终参数的9次回放，早期回放不作为最终结果。tests/fixtures/shop/inventory_readiness.json记录来源哈希、原图裁剪及实际存储区域像素哈希；NPZ只保留商品区域和商店按钮，helper在空客户区画布中放回原位。readiness-before保存已备份文件的阶段前状态，scroll_vision.py的上一阶段摘要保留在原completion.json。原阶段证据未覆盖或删除。

工作区目录检查通过；本次不构建、不安装、不提交、不推送，不操作游戏，既有无关修改保留。

本阶段文件范围：

- `CHANGELOG.md`
- `config/internal.yaml`
- `docs/design/ARCHITECTURE.md`
- `docs/design/BEHAVIOR.md`
- `docs/design/NATIVE_MATCHING.md`
- `docs/validation/shop/SHOP_RECOGNITION_VALIDATION.md`
- `src/e7auto/configuration/loader.py`
- `src/e7auto/configuration/models.py`
- `src/e7auto/features/shop/contracts.py`
- `src/e7auto/features/shop/flow.py`
- `src/e7auto/features/shop/inventory.py`
- `src/e7auto/features/shop/readiness.py`
- `src/e7auto/features/shop/refresh.py`
- `src/e7auto/features/shop/scroll_vision.py`
- `src/e7auto/features/shop/scrolling.py`
- `src/e7auto/features/shop/vision.py`
- `tests/automation/test_inventory_readiness.py`
- `tests/automation/test_scroll_verification.py`
- `tests/automation/test_scrolling.py`
- `tests/core/test_domain_config_overlay.py`
- `tests/core/test_inventory_readiness_config.py`
- `tests/fixtures/shop/inventory_readiness.json`
- `tests/fixtures/shop/inventory_readiness.npz`
- `tests/helpers/config.py`
- `tests/helpers/shop_scenes.py`
- `tests/helpers/vision.py`
- `tests/vision/test_inventory_readiness.py`
- `tests/vision/test_scroll_overlap.py`

## 2026-10-21 按完整商品行核验滚动

用户确认同时修改商店基准和非基准最终内容核验。15:26:37、15:27:07两轮日志均在稳定后返回insufficient_matching_content；补充的同一库存滚动前截图与两次停止图分别配对，原算法均给出约-393.722像素，但四分区得分为0.803、0.640、0.940、无效（滚动前纹理标准差7.617<8）。右上包含不随商品滚动的固定背景，右下主要是短名称尾部及背景，导致正常位移被拒绝。

附件原图2396×1462，客户区原点(54,103)、大小2322×1306，由标题、金币和刷新按钮三个独立静态区域定位确认。新增scroll_rows_recent.npz保存无缩放的商品内容ROI裁片；同名JSON记录原始文件哈希、裁剪框、完整客户区几何和各BGR像素哈希。滚动前图是用户随后补充的同库存画面，并非两次运行当时的before帧；本次属于该场景离线复现，不伪称原运行逐帧回放。

最终核验使用现有top槽位item_roi，至少scroll.minimum_content_rows（默认2）条完整且纵向不重叠的商品行达到原0.75相关门槛。低纹理、残缺行及超出内容ROI的完整对齐窗口不提供证据；单行或重复声明同一行不能通过。配置加载拒绝少于两行、行数不足、越界及纵向重叠。槽位经完整客户区变换投影，ClientRect保留全局坐标，数组访问减去FrameView原点，不缩放最终验证像素。

四个位移候选、静止竞争解释、歧义拒绝、名称参与候选/稳定性/变化量、向上300、水平4、重叠50%、变化20%及纹理8门槛保持；滚轮次数、100ms首检/采样、三张新帧确认和800ms有效预算保持。失败时content_scores保留首候选的逐槽位分数，按传入top槽位顺序；na表示该行没有有效评分。背景验证工具同步传入配置、槽位及实际客户区变换，并将内容通过和水平位移纳入验收条件，本次未执行该工具的实机输入。

实际相关测试累计159个不同用例全部通过：首轮115项；流程、时间、配置加载及验证工具40项；明确保持全局ClientRect语义并增加基准尺寸±1像素边界后，重验受影响图像用例74项（含4项新增），其余45项配置和40项流程结果复用本轮通过记录。不运行全套。覆盖新旧真实图像、派生1920/1536与尺寸边界、单行/残缺/重叠/低纹理、重复商品歧义、静止/反向/无关内容、水平与重叠边界、原图不resize、调用重复一致性，以及停止、恢复、缓存和统一截止时间。

两组新配对的完整行得分均为0.971189和0.970225，旧1536真实问题图为0.979895和0.971555。8组样本（两组新配对各三尺寸、旧基准、旧原生1536）各三次实际时钟离线回放，共24次，滚轮结束后校验337～410ms，全部低于800ms；单次内容核验中位数31.63～89.02ms。回放用固定after像素模拟新帧，仅识别和等待实算，未计真实WGC、游戏输入或网络耗时；派生尺寸不作为独立实机样本。

工作区目录检查通过。未改正式模板，未构建、安装、提交、推送或操作游戏。已有入口模板、UI及其他无关改动保留。任务证据位于artifacts/tasks/shop/20261021-154829-row-content-scroll-verification-8faa608e/：results/rows-regression.xml、flow-regression.xml、geometry-regression.xml、initial-replay.json和replay.json；scratch/replay.py保留回放方法，scratch/before保存本次涉及文件的修改前状态。

本次文件范围：

- `CHANGELOG.md`
- `config/internal.yaml`
- `docs/design/ARCHITECTURE.md`
- `docs/design/BEHAVIOR.md`
- `docs/design/NATIVE_MATCHING.md`
- `docs/validation/shop/SHOP_RECOGNITION_VALIDATION.md`
- `scripts/validation/validate_background_mode.py`
- `src/e7auto/configuration/loader.py`
- `src/e7auto/configuration/models.py`
- `src/e7auto/features/shop/scroll_vision.py`
- `src/e7auto/features/shop/vision.py`
- `tests/core/test_domain_config_overlay.py`
- `tests/core/test_scroll_configuration.py`
- `tests/fixtures/shop/scroll_rows_recent.json`
- `tests/fixtures/shop/scroll_rows_recent.npz`
- `tests/scripts/validation/test_background_validation.py`
- `tests/vision/test_scroll_optimization.py`
- `tests/vision/test_scroll_overlap.py`
- `tests/vision/test_scroll_rows.py`
- `tests/vision/test_vision.py`

## 2026-10-16 商品内容核验性能优化

本节是同日主判定修复之后的独立优化阶段。基准、非基准共同执行，ROI、四个候选与静止解释、三分区通过条件、评分/纹理/位移/重叠/变化量门槛、采样时序及800ms有效预算均不变。一个候选已有两块不通过时结束其余图块；首个通过候选与后来通过候选已超过现行歧义容差时立即返回歧义。通过候选仍返回完整四块分数，不因第一次成功而省略其他竞争解释。

纹理统计用C++矩量先获得保守区间，按float32运算次数上界覆盖均值、方差归约、平方与开方舍入；范围仅针对原始uint8灰度转成的float32输入，区间本身使用Python双精度计算。阈值落在不确定区间时，直接使用原NumPy float32标准差判定。没有用样本误差指定经验epsilon，也没有移动阈值8。原图相关打分保留原float32及相同输入视图，未采用uint8直接打分原型，避免尚未证明的分数/峰值边界差异；未增加跨帧缓存、线程池、依赖或运行诊断导出。

93项直接相关测试通过：46项新增数值/提前结束用例及47项既有图像、流程、时间、停止、恢复和缓存回归。将误差区间计算显式固定为Python双精度后，仅重验受影响的65项图像/数值用例，全部通过；其余28项复用本轮已通过结果。既有205项记录中的未变配置、日志、停止截图等不重复运行。新增覆盖标准差8附近的float32舍入、非连续视图、实际标准差上下相邻浮点数、低纹理、两块失败的全部位置组合、通过分数0.75、保留float32输入、静止竞争及确定歧义结束。

额外64组生成场景与优化前观察结果逐项一致。另64种纯色场景全部保持拒绝；其中一组FFT位移出现约6.5e-8像素的数值差别，在仅重复运行优化前函数40次时也复现了相同两个结果，相位算法未修改且不构成通过证据，未以改阈值隐藏该既有数值波动。真实正例的匹配分数、位移及判定在交错测量中一致。

同机、相同OpenCV线程设置、预热后20次交错测量，单次位移核验中位数：正常基准160.83→101.62ms（减少36.81%）；旧基准图派生1536×864为91.44→63.06ms（减少31.03%）；本轮真实失败图99.34→67.09ms（减少32.47%）。P95分别176.39→114.42、120.38→94.69、148.12→146.81ms。当前样本的原图匹配从20次减至12次；纹理统计从40次NumPy方差变为24次C++矩量，临界情形仍可执行原方差，不承诺所有场景固定12次。

每组另外做三次交错实际时钟离线回放，滚轮结束后校验中位数：基准533.28→474.09ms、派生1536为496.02→392.29ms、真实问题图668.84→532.91ms，均通过且在800ms内。该批次实际等待受本机调度影响，与前一阶段单独测量不直接相减；没有真实WGC取帧、游戏输入、购买/刷新或新包验收，不能换算为所有设备的整轮刷店速度。

本轮仅修改scroll_vision.py、新增test_scroll_optimization.py，并补充本记录和CHANGELOG；既有修复及无关工作区改动保留。任务仍为 `artifacts/tasks/shop/20261016-191736-content-scroll-verification-63638787/`。`results/optimization-before-files.json`及`scratch/optimization-before`保存本阶段开始状态；实际证据为`optimization-regression.xml`、`optimization-numeric-final.xml`、`optimization-performance.json`、`optimization-call-counts.json`及纯色数值复现记录。未运行全套、未实机操作、未构建、未提交或推送。

## 2026-10-16 商品内容滚动主判定

用户提供本轮完整日志、滚动前图、首张检测图及最后三帧，文件和BGR像素SHA-256均与JSON一致。原客户区1536×864，OpenCV 5.0.0。旧整块位移复现+395.222基准像素，而商品内容约-394；右侧重复价格/购买按钮形成错误相关峰。首张检测图已到底，原累计兜底只从首张滚动后帧开始，累计纵向仅+0.025，未执行重叠核验。旧粗网格同图比较另有+0.5网格像素的水平偏差。

用户明确授权基准和非基准共同直接替换旧算法，不保留旧算法或失败后切换的兜底。当前ROI为基准坐标 `[950,110,900,1180]`，仅商品图标和名称；价格和购买按钮不参与候选、稳定性、差异或原图内容评分。同一相关面至多四个候选及静止解释一起核验，四个原图分区至少三个达0.75，局部对齐容差4基准像素；多个不一致位移同时成立时拒绝歧义。最终验证不resize，不把非基准截图缩回基准。稳定粗特征只用于相邻帧停稳检查，偶数FFT补边修正网格原点。

真实新样本、旧基准样本显示，内容区变窄后原30%变化比例会拒绝正确滚动：最终ROI中新样本首张为26.24%，旧基准为27.59%，因此内容变化门槛改为20%；最小向上位移300、水平4和最小重叠50%继续约束。ROI校准时排除了右侧无用背景，未降低0.75原图内容门槛。真假方向、静止、无关内容、重复商品歧义及价格/按钮扰动反例均纳入回归；本轮只有一个真实失败场景，不能据此宣称覆盖所有商品组合。

保持六次-120、10ms输入间隔、100ms首检、100ms采样、两次连续稳定比较和800ms预算。截图与识别耗时计入统一截止时间，计算完成时超时不得通过；失败不重开预算。内容冲突、稳定中断、重复采集时间及网络恢复清空连续窗口，保留停止/窗口/WGC/输入守卫。首张已经到底可由滚动前图验证，成功仍要求三张新帧停稳。只返回本次连续窗口，底部扫描保留稳定帧复用。

直接相关测试累计205个不同用例最终通过（按各用例最后一次结果合并，不累计重复运行），涵盖新原图判定、基准/派生尺度、时间边界、异常方向、稳定/缓存、配置拒绝、手动停止、网络恢复及日志/停止截图。首次测试暴露旧基准ROI含多余背景、测试停止API调用错误及两项旧日志字段断言，修正对应内容后仅重验受影响范围。新鲜时间测试的浮点构造曾未产生真正相等时间，改为精确相同时间后通过。读取整个test_vision模块时另有三项通用匹配断言失败；使用修改前保存的测试副本重现相同失败，相关通用匹配代码和这些断言未修改，不计入205项通过。未运行全套测试。

实际时钟离线回放（识别实算，截图来自固定原图，未操作游戏）：本轮真实失败场景386ms；旧基准2322×1306为477ms，旧图派生1920×1080为420ms、1536×864为452ms，均在800ms内完成。派生尺度不是独立实机样本，当前机器耗时不代表所有用户设备。本轮未进行实机输入、新包构建、安装验收、提交或推送。

临时功能/日志取证模块及导出接入、分组接入、两份专用测试已移除；旧累计兜底及其测试已由单一主判定回归替换。既有日志、诊断原图及任务历史保留。本轮无正式模板变更、候选登记或PNG清单更新。固定测试素材仅新增三张无损原图裁片及来源摘要，见 `tests/fixtures/shop/scroll_content_manifest.json`。

任务证据：`artifacts/tasks/shop/20261016-191736-content-scroll-verification-63638787/`。`results/final-offline-validation.json`保存五张原图摘要、四组前后测量和四次实际时钟回放；`regression.xml`、`logging-repair.xml`、`content-final.xml`、`fresh-time.xml`及`native-final.xml`保留实际测试阶段，最终不同用例汇总及文件清单见 `results/completion.json` 与 `results/changes.md`。原有改动的本轮开始前副本在该任务scratch/before，不使用Git整文件回退。

## 2026-10-08 临时滚动失败取证（历史记录，本次已移除）

用户提供的七轮1.4.0日志均为桌面2560×1440、客户区1536×864、DPI144，在滚动位移验证处停止。最终列表稳定，整块相关返回约+395个基准像素；正常记录约-394。重复行错配是待验证假设，尚未取得失败当轮滚动前截图，不能宣称已确认或修复根因。

经用户批准，仅增加临时取证：成功滚动不编码或保存图片；同一轮只持有原有滚动前、首张检测及末尾三帧的引用。只有SCROLL_VERIFICATION_FAILED才保留该轮证据，并在会话已停止、功能清理时导出。手动停止、窗口/截图异常不导出此包；不增加截图、滚轮、等待或比较次数，不改变现有基准/非基准判定、兜底、ROI、阈值、模板及截图实现。

产物与原运行日志同目录、同编号：`<run>-scroll-before.png`、`<run>-scroll-first.png`、`<run>-scroll-last-1.png`至`last-3.png`，以及`<run>-scroll.json`。PNG为算法实际接收的原始BGR客户区像素；JSON绑定帧序号、源帧/接收时间、网络恢复代次、坐标变换、实际ROI、输入顺序、各次比较结果及应用/OpenCV版本。客户区等效位移和相位网格位移由原返回结果换算，明确标为派生值，未在运行中重复计算。原始像素和PNG摘要在失败导出时生成。诊断文件纳入原日志组的期限、数量和容量管理；文件冲突不覆盖，部分导出明确记录，写盘或诊断异常不替换原停止原因或跳过清理。

127项直接相关测试最终通过，包括25项新取证/导出检查，以及原滚动、兜底、停止截图和日志回归102项。两项释放断言曾被测试替代服务和异常回溯持有的图片引用影响，已清除测试自身引用后仅重跑失败用例；另修正一项原日志回归子进程中的两处旧模块导入，再单独验证。最终补查元数据缺失时帧序号不移位，并仅重验受影响的取帧/角色绑定。基准及缩小窗口下核对输入、截图次数、等待、成功无PNG、失败单次导出、元数据/原像素、保留策略和诊断失败保护。未重复未变图像匹配验证，未执行全套、实机、构建、提交或推送。当前补丁不属于已完成的滚动修复。

任务证据：`artifacts/tasks/shop/20261008-233150-scroll-failure-diagnostics-53de9d6c/`。`results/rollback-scope.json`及`changes.txt`保存临时接入范围、实施前字节备份和差异；根因及正式修复验证完成后，移除两个临时诊断模块、Flow接入、日志导出/分组接入和两份专用测试，保留正式修复、有效回归证据及已修正的旧测试导入。不使用整文件Git回退覆盖原有修改；本节后续更新实际诊断结论及移除状态。

## 2026-09-30 金币不足提示文字遮罩修复

本节为后续模板修复，不改变下文v1.3.1历史构建记录。`insufficient_funds`下方“购买金币。要前往商店吗？”的较暗笔画被旧遮罩遗漏；从PNG保留的原始截图RGB重新提取说明区文字并保留1像素原始边缘。RGB、249×236画布、顶部标题区域及当前0.93门槛、purchase_result范围、金币不足终止行为均不变。用户审核通过后，经prepare、目标/哈希/来源核对、apply正式登记；PNG、商店运行清单和新来源记录一致。

沿用候选阶段9项检查：保留的真实局部RGB放入历史坐标所组成的合成客户区，在2322×1306、1920×1080、1536×864下匹配分数为1.0、0.986681、0.977830；4项移除标题/说明的派生反例及2张真实商店列表局部反例均未命中。原始完整终止弹窗截图已缺失，不能把这些正例称为独立完整截图或实机验收。初次误将两个73×30数字局部图按完整客户区使用的结果已剔除，最终9项不包含这些无效检查。

审核后未重跑图片扫描。`tests/resources/test_template_assets.py::test_insufficient_funds_template_uses_only_terminal_prompt_evidence`改为通过当前运行清单读取新登记来源的mask_foreground_pixels；历史insufficient_funds_manifest.yaml及其component_areas/foreground_pixels记录保持原样。该直接相关测试1项通过（0.45秒）。模板登记完整性、目录和差异检查结果见同一任务completion.json；未运行全套测试、实机、构建、提交或推送。

本地任务：`artifacts/tasks/common/20260930-155455-template-mask-repair-60757600/`；候选及计划：`artifacts/template-candidates/20260930-155605-175ab0be/`。registrations.json记录全部49张（商店1张、战利品48张）的候选/计划路径、登记来源及事务ID；本次审批没有重新启用历史资源或调整此前9张招募完成模板的ROI/锚点。

## v1.3.1 原始记录

日期：2026-09-14。源码与开发环境版本为 `1.3.1`。本记录分别记录离线验证、用户实测反馈和本机构建状态，不将源码运行结果等同于新发布包的实机验证。

## 改动范围

1. 三种商品的未购买、购买确认、已购买状态共九张模板，去掉外侧灰框和多余背景，保留内框、框内图案、数字、RGB 和尺寸。二值透明遮罩参数和逐图摘要见 `docs/calibration/shop/manifest.yaml`。
2. 新增三种商品共用的 `purchased_button.png`，采用用户确认的第四版：331×98，顶部不连续黑边已去除，圆角透明边缘已平滑。正式配置将其列为必需模板。
3. 购买后先判断金币不足，再检查原商品行的已购买图标；图标未通过时，同一行按钮达到 0.93 且连续 3 帧通过也可计数。按钮判定不识别商品种类，不用于初次扫描计数，不跨行确认，不触发购买重试。结果等待上限仍为 5 秒。
4. 主界面 `main_shop_icon.png` 改用用户提供的深色背景完整截图，只保留问号、文字及相邻描边。恢复误删的 43 个浅蓝灰笔画像素，保留自然开口；仅向内平滑透明边缘，RGB 和最终 128×106 尺寸不变。
5. 主界面提取脚本支持 `--source` 完整截图，并保留历史 `--source-dir` 模式。来源和处理参数见 `main_shop_icon_manifest.yaml`。运行日志新增 `slot`、`button_stable` 和 `purchase_counted.evidence`。

## 截图与结果

原始文件由用户提供。临时路径只是来源证据，不保证在其他机器存在；可移植的按钮回归素材只保留九个 367×130 的按钮小区域，按行拼接在 `tests/fixtures/shop/purchased_button_samples.png`，来源及排列见 `purchased_button_manifest.json`。

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 最初异常截图，原已购买书签图标 | 0.8228，未通过 0.90 | 日志终止原因为 `purchase_result_ambiguous` |
| 同图，九张模板去边后的书签图标 | 0.8371，仍未通过 | 加透明通道会切换现有匹配算法，新旧分数不是同一指标；去外框不足以单独解决异常 |
| 本机素材，友情点数的已购买按钮 | 约 1.0000，通过 0.93 | 模板来源图的正样本 |
| 最初异常截图，书签的已购买按钮 | 0.9693，通过 0.93 | 独立截图；正式 `purchase_outcome` 返回 `success_button` |
| 两张商店截图中的其余七个未购买按钮 | 0.1199～0.1471，均未通过 | 当前样本未出现把 `1/1` 识别为已购买的情况 |
| 新深色主界面，2322×1306 | 1.0000 | 最终修复及平滑后的模板 |
| 新深色主界面，派生 1536×864 | 0.9762 | 将源客户区缩小后走生产 ROI 归一化路径 |
| 前一张浅色主界面，2322×1306 | 0.9894 | 跨背景样本 |
| 前一张浅色主界面，派生 1536×864 | 0.9629 | 高于现有入口阈值 0.93 |

单张截图不能证明连续三帧稳定；连续帧、帧间中断、计数和超时行为由模拟流程回归覆盖。其余两种商品和各购买弹窗尚无完整的独立现场截图覆盖，不将九张模板本身作为独立正样本。离线开发阶段未操作游戏；随后用户完成源码实测，证据见下节。

## 用户源码实测

用户于 2026-09-14 明确反馈“经过实测此次修改没有问题”。本机源码日志 `run-20260914-181917-002191-e191299ad996.log` 记录版本 1.3.1，运行时间 18:19:17～18:23:04，客户区 2322×1306，友情点数未启用。

- 本轮共确认 4 次圣约书签购买：2 次 `evidence=success`，2 次 `evidence=success_button`。
- 18:21:24，`top_2` 连续记录 `button_stable=1/2/3`，随后书签计数增至 3；当时 `refresh_spent=84`。
- 18:21:46，`top_1` 再次连续记录 `button_stable=1/2/3`，随后书签计数增至 4；当时 `refresh_spent=99`。
- 最终 `reason=budget_complete`、`refresh_spent=150`、`refresh_limit=150`，正常结束，没有购买结果不明停止。

该轮直接证明按钮备用方案参与了实际成功计数。日志未记录另外两种商品的按钮备用成功，也未覆盖所有显示环境；用户总体反馈不扩写为这些场景均已验证。构建验证只运行编译版 self-check，不自动执行游戏购买。

## 已完成的相关测试

- 按钮接入阶段：54 项通过，范围为 `tests/vision/test_purchased_button.py`、`tests/vision/test_vision.py`、`tests/automation/test_inventory.py`、`tests/core/test_domain_config_overlay.py` 及商品模板完整性检查。覆盖两类按钮截图、同一行限制、未知槽位、三种已知目标共用按钮、连续帧重置、只计数一次、超时不重试及金币不足优先。
- 最终主界面模板阶段：5 项通过，范围为主界面模板可重建性、问号实心笔画／自然开口、入口阈值、透明遮罩和 ROI 边界检查。恢复及平滑后 RGB 不变，透明背景范围不扩大，匹配分数均通过。
- 版本及记录同步阶段：`tests/scripts/release/test_version_sync.py` 与两项模板完整性／重建检查共 8 项通过；开发环境可编辑安装元数据同步为 1.3.1，未下载依赖。`git diff --check` 通过。
- 本机构建检查阶段：补齐发布验证器的已购买按钮／JSON 清单要求；5 项直接相关发布资源测试通过（另 3 项未选入），包含当前资产通过、缺失按钮／清单、清单损坏、模板不可解码和网络模板缺失。
- 上述阶段存在重叠测试，不累计为独立测试总数。遵循仅运行直接相关测试的约定，未运行全量测试。

## 本机构建与验证状态

用户确认实测通过后，已运行 `scripts/release/build-standalone.ps1`，Nuitka 退出 0。本机产物为 `dist/launcher.dist/E7auto.exe` 和 `dist/E7auto_v1.3.1_x64.zip`，旧 v1.3.0 ZIP 已按构建脚本规则替换，不额外备份旧 dist。

- ZIP 大小 83,370,324 字节；165 个文件均完成解压 CRC 校验，并逐文件 SHA-256 与 dist 对比一致。
- EXE 静态检查：AMD64，文件／产品版本 1.3.1.0，包含 requireAdministrator 清单。
- 59 个模板目录文件均与源码逐字节一致；31 张配置模板均可解码；配置及包内使用说明与源码一致。发布目录未混入日志、测试或虚拟环境。
- 发布验证器此前将新增按钮 PNG 误报为意外资源，已补齐按钮及 JSON 清单白名单、缺失／损坏检查；当前静态验证 problems=[]。该工具仅用于开发侧验证，不是 EXE 打包模块。
- **管理员发布验证和编译版 self-check 已通过**：最初管理员启动失败／UAC 被取消；随后用户明确要求重发 UAC，本次启动成功，验证退出 0、problems=[]。自检报告 compiled=true、version=1.3.1、machine=AMD64、WGC 可导入，配置／模板／UI 资源存在，未捆绑 venv。此结果为本机编译版自检，不代表纯净 Windows 或编译版游戏购买的全部检查已完成。
- 既有源码测试已通过，本轮没有重跑全量或无关测试；本轮没有执行游戏购买。

本机构建证据保存于 `artifacts/release-v1.3.1-20260914-182935/`（不纳入提交）。当前未暂存、提交、推送、创建标签或发布 GitHub Release；dist 和 ZIP 继续保持为本地产物。
