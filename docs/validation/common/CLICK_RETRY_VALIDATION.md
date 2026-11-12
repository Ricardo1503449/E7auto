# 游戏点击有限重试验证

## 2026-11-12 卡厄思结算大厅稳定确认

10月31日提交`556e97f`在旅程关闭后新增`journey_closed`确认；大厅检测返回`Observation`，而`ChaosFlow._signature`仅提取`Detection`的身份和位置，导致大厅结果按完整对象比较，识别分数波动也会重置三帧计数。通关、战败和启动清理旧探查共用该关闭流程，均受影响。

本次修复限定在卡厄思的稳定签名转换，复用已有`observation_signature`处理`Detection`及`Observation`。原`Detection`签名值、地图和元组处理保持；模板、ROI、颜色及结构门槛、三帧要求、点击次数、页面期限和网络/停止守卫不变。新增流程用例在2322×1306和1536×864下给大厅分数注入微小波动，并检查关闭只发送一次、确认使用三张不同的新帧。

使用`.venv`中的Python以`-B -m pytest -v --tb=short -p no:cacheprovider`按函数和参数选择执行。新增的`test_chaos_confirmation.py::test_journey_return_accepts_varying_lobby_confidence[size0/size1]`在修改生产代码前均复现`journey_closed`识别超时，修复后两例通过。既有用例仅执行以下6例，全部通过：

- `test_chaos.py::test_two_rounds_complete_only_after_final_home_with_all_nodes_and_swap`：通关计数、多轮继续及最终返回主界面。
- `test_chaos_defeat.py::test_defeat_restarts_all_battle_types_without_counting_failed_round[boss-3]`：第三层首领战败后重新开始，失败不计入成功轮数。
- `test_chaos_saved_exploration.py::test_cleanup_precedes_stage_selection_and_new_round[1]`：清理旧探查后才开始新轮，清理不计轮数。
- `test_chaos_defeat.py::test_failed_recovery_requires_each_destination_without_reclick_or_restart[lobby]`：大厅缺失仍超时停止。
- `test_chaos_defeat.py::test_reconnect_during_defeat_recovery_does_not_replay_inputs[journey_close]`：关闭后重连不重放关闭或重复计数。
- `test_chaos_defeat.py::test_manual_stop_stops_without_followup_input_or_completed_round[closed]`：关闭后的手动停止阻止后续输入和完成计数。

合计新增2个参数用例、复用6个既有用例，修复后8例全部通过。未运行整份测试文件或项目全套，未重复执行未变的图像识别矩阵，未做实机、构建或安装验收。

## 2026-11-12 企鹅等待接口约束

企鹅`PenguinFlow._wait`的`signature`改为必填关键字参数，去掉按原对象比较的默认值。现有生产调用继续按`object_id`确认控件；直接等待字符串状态的重连测试显式声明字符串比较规则。识别、导航、金额、帧数、期限和重连行为保持。

实际按函数执行`test_penguin.py::test_recovery_uses_clear_frames_without_restarting_business_stability`及`test_all_penguin_navigation_and_altar_handoffs_use_three_frames`，两例通过。前者是调整后的既有测试，后者复用原导航与三帧交接覆盖；未新增仅检查必填参数语法的测试。

## 2026-11-12 卡厄思等待结果类型约束

卡厄思`ChaosFlow._signature`只接收已定义比较规则的`Detection`、`Observation`、`MapView`、递归元组、`None`、字符串（含`AutoState`）及整数（含布尔值）。未支持的结果类型直接抛出带类型名的`TypeError`，不再退回完整对象比较。新增用例从实际等待入口检查未知对象及嵌套未知对象在第一帧报错，且不发送输入。

新增`test_chaos_confirmation.py::test_unknown_wait_result_fails_on_first_frame[False/True]`在加固前均因未抛出`TypeError`而失败，加固后两例通过。既有`test_chaos_confirmation.py::test_camp_rank_and_close_use_same_fresh_frames[None-size0]`及`test_chaos_auto_motion.py::test_static_three_frames_clicks_once_without_outer_wait_and_reconfirms_rotation`通过，分别覆盖阶数整数元组及`AutoState`的原稳定比较语义。

本轮实际执行新增2例、既有4例，全部通过。上节大厅分数波动修复及三条返回入口的相关分支未变，复用其8例通过结果，不因接口加固重复运行。未运行完整测试文件或项目全套，未做图像矩阵、实机、构建或安装验收。

## 2026-10-31 实施范围

按用户确认的方案，新接入动作至少间隔3000毫秒、含首次最多2次；总期限为原期限与6000毫秒中的较大值，并受外层期限限制。契约仍100毫秒缓冲、三选一选中仍0毫秒、通用缓冲仍300毫秒。共享模块管理重试流程，页面、身份、选中与完成条件仍由所属功能提供。

首次可靠响应永久阻止本次补点，即使后来漏识别或重连也不解锁。仅原目标在原页面重新稳定三帧才补点，不重新运行选人、战利品或节点优先级。高风险提交仍各自单次发送；重连不清次数、不重启有效期限。未修改PNG、遮罩、ROI、识别门槛、匹配后端、事件路线或经验规则。

## 点击入口覆盖清单

| 功能/入口 | 处理与边界 |
| --- | --- |
| 商店商品购买入口、退出 | 新增。购买入口绑定同一视口、商品、槽位及未购买状态；弹窗或主界面出现后不补点 |
| 商店购买确认、刷新扣费确认 | 单次。结果未知不重放 |
| 商店刷新入口、主界面入口、策略唤醒、滚轮 | 保留既有重试/策略，不叠加 |
| 企鹅森林、祭坛、购买弹窗入口 | 新增。绑定当前层级；不完整购买弹窗也阻止补点 |
| 企鹅最多数量 | 新增。仅原金额未改变且完整弹窗仍在时补点；金额变化立即禁止补点，保留低金额观察窗 |
| 企鹅奖励关闭、购买取消、祭坛关闭、两级返回 | 新增。确认祭坛或对应上一级页面；购买确认仍单次、完成后计数一次 |
| 卡厄思探查卡、介绍进入、逐级返回 | 新增。确认介绍、大厅或上一级；大厅日期提示优先 |
| 卡厄思旧探查放弃入口 | 新增。仅打开提示；最终放弃确认单次 |
| 契约、三选一战利品选中 | 新增。绑定原卡片局部区域；选中证据出现后只等确认按钮，最终选择/领取单次 |
| 阶段面板打开、目标阶段选中、阶段确认 | 新增。原目标阶段及联合金框/按钮；阶段总期限不变，不额外拖动 |
| 普通/全职业招募券页面 | 新增。确认对应职业标题；不增加招募次数 |
| 筛选面板打开/关闭、属性与职业切换 | 新增。按目标开关状态判断；关闭须重新看到列表英雄，不能只依赖按钮颜色 |
| 招募、升阶、离队英雄选中 | 新增。绑定同一英雄；其他姓名或选中框也视为已有响应，不继续补点或提交错误对象 |
| 候补教师选中、自动战斗开启 | 新增。前者确认选中及替换箭头；后者仅重新确认STATIC才补点，ROTATING或结果出现立即结束，识别算法和锚点不变 |
| FAILED关闭、旅程结算关闭 | 新增。原失败/结算页，确认旅程/大厅；轮次及战败仍各计一次 |
| 普通继续探查、营地/补给/商店离开 | 新增。原楼层地图或声明提示；普通战晚到升级的专用继续流程保留 |
| 营地/事件升阶入口、营地升阶结果关闭 | 新增。原入口或已验证的阶数结果；不重发升阶确认 |
| 卡厄思商店购买弹窗入口、石板/通道招募页继续 | 新增。绑定原商品或事件页面；仅打开购买/弃领提示，最终确认单次 |
| 开始探查、进入已配置队伍的探查、战斗开始、节点进入按钮、招募、离队、换人、升阶、购买、战斗领奖、补给领取、战利品领取、弃领/离店确认 | 保留原提交规则，不增加事务性补点 |
| 地图节点、事件选项、事件奖励/生命/升阶卡片、首领剧情 | 保留既有重试，独立计数，不套外围重试 |
| 战后/事件经验升级关闭、连续文本推进 | 保留原状态机，本场/本次事件已关闭状态及文本循环不变，不增加跨弹窗重放 |
| 共享启动返回/唤醒、功能入口、网络重连、跨日卡片关闭 | 保留既有处理；签到确认单次 |
| 应用自身UI、拖动、滚轮 | 不在本次范围，未修改 |

## 验证与限制

实际运行直接相关的共享运行/配置及三个功能的流程测试，合并修复复核后共707个不同用例：703项通过，4项失败也在未修改的HEAD代码上复现。未跑项目全套，未执行实机输入或构建安装包。

新增检查覆盖首次漏点、第二次无响应、三秒间隔、六秒/原长期限/外层短期限、瞬时响应后消失、未知页面、目标移动或歧义、商品/槽位变化、已购买状态、停止和日志/识别跨期限、输入异常、重连及开关不反向切换。2322×1306和1536×864的客户区坐标通过实际受控输入适配器验证只转换一次。两套阵容、教师及暗影萝季、选中联合三帧、地图一次性交接、跨日恢复和高风险提交仍由相关流程用例验证。

首轮失败原始输出保留。确认回调中重复识别已改为复用同帧响应标记；筛选关闭判定补入本阵容已加载的后续英雄；可逆动作的旧“永远只点一次”断言调整为最多两次，最终确认仍单次。自动战斗原有汇总保留，未降低阈值或关闭守卫。

4项既有失败为：企鹅`test_manual_stop_interrupts_long_sanctuary_animation_wait`；卡厄思`test_non_home_start_sends_only_one_wake_then_stops`及`test_hero_selection_relocates_identity_and_requires_its_own_gold_frame`的两个参数。前两项涉及原启动等待/唤醒假设，对应共享导航未改。英雄位移旧用例在HEAD也未通过；非错误行分支在新增重试后由识别超时变为输入失败，其模拟移动锚点与假输入映射不一致，不能据此声称位移场景已有成功基线。本次另用新锚点、两尺寸输入适配器及既有联合确认用例补充覆盖，未扩大修改这4项旧失败。

PNG及像素匹配未变，不重跑图像矩阵。证据主要为假游戏、模拟时钟及受控输入适配器，不等同于实机恢复或性能收益。企鹅原金额始终不变时，补点后固定期限可能不足以完成原低金额观察窗，此时保守超时，不假定第二次点击成功。正常路径没有强制三秒等待；部分原先未确认的页面交接增加了完成检查，未声称正常耗时全部不变。

证据任务为`artifacts/tasks/common/20261031-141536-bounded-click-retries-1f71ea14/`。`results/first.xml`、`integration.xml`、`guards.xml`保留首轮结果；`repair.xml`、`special.xml`、`guard-repair.xml`为对应修复；`dropped.xml`、`final-smoke.xml`、`boundary.xml`为新增检查。`test-summary.json`按被验证的代码顺序合并，不按并行运行的完成时间倒退覆盖修复结果；`baseline-chaos.xml`记录三个旧用例在HEAD上的复现。目录检查及差异空白检查通过。未提交、推送或发布。

## 本次文件

- 共享配置与运行：`config/internal.yaml`、`src/e7auto/configuration/models.py`、`src/e7auto/configuration/loader.py`、`src/e7auto/runtime/action_retry.py`。
- 功能接入：`src/e7auto/features/shop/flow.py`、`src/e7auto/features/shop/inventory.py`、`src/e7auto/features/penguin/flow.py`、`src/e7auto/features/chaos/flow.py`、`src/e7auto/features/chaos/recruitment.py`、`src/e7auto/features/chaos/stage_selection.py`。
- 新增测试：`tests/automation/test_action_retry.py`、`tests/automation/test_click_retry_flows.py`、`tests/core/test_click_retry_config.py`。
- 原测试调整：`tests/automation/test_penguin.py`、`tests/automation/test_chaos_auto_motion.py`、`tests/automation/test_chaos_confirmation.py`、`tests/automation/test_chaos_defeat.py`、`tests/automation/test_chaos_loot.py`、`tests/automation/test_chaos_recruit.py`、`tests/automation/test_chaos_saved_exploration.py`。
- 设计与说明：`docs/design/ARCHITECTURE.md`、`docs/design/BEHAVIOR.md`、`docs/development/DEVELOPMENT.md`及本文。

原有未跟踪`pip.ini`和其他开发产物保留；候选模板、正式资源、版本号及发布历史未改动。
