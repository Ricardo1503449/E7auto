# 自动卡厄思候选模板审核记录

## 本次范围

本次只制作离线候选和审核材料，等待用户审核。未修改运行源码、运行配置或正式模板；未执行 prepare/apply，无登记计划或事务 ID。未操作游戏，未进行实机验收。

任务：`artifacts/tasks/chaos/20260920-163417-template-review-c51812d6`。

候选：`artifacts/template-candidates/20260920-170617-81e7982d`。

- 保存162份用户素材：91张完整截图、70张局部截图、1段15秒录屏；另外引用2份现有公共网络模板。
- 91张完整截图分别定位标题栏，记录客户区裁剪位置；客户区均为2322×1306。原文件按字节保全，客户区和候选没有拉伸。
- 导出135个chaos候选PNG及校验、来源文件，另有2个公共模板引用，共137个审核条目。灰态参考与暂不启用的阶段切换明确标记。
- 身份、选中、领取完成分别制作；英雄只保留名称及职业图标；事件组合、战利品保留文字和特征图标。
- 候选RGB与来源裁剪逐像素一致，只修改alpha遮罩。候选元数据哈希核对通过。

## 审核材料

任务目录中的 `previews/index.html` 提供按组、状态、名称筛选，每项展示原裁剪、棋盘格候选、遮罩、客户区搜索范围、匹配位置及点击锚点。

- `previews/sources.html`：原始素材及客户区边界。
- `previews/summary.html`：验证范围、限制和审核事项。
- `previews/cross_validation.html`：11种战利品交叉匹配矩阵。
- `previews/motion.html`：自动战斗逐帧采样及旋转候选参数。
- `results/recognition_draft.yaml`：未批准的识别区域、阈值和策略草案，不属于运行配置。
- `results/approval_manifest.json`：审核材料及候选的版本哈希，不是正式登记证据。

动态节点和英雄列表采用区域搜索，不固化截图中的行列位置；替换按钮另展示相对骑士图标的搜索范围。源图裁剪位置与运行搜索ROI分别记录。

## 离线验证结果

- 模板检查420项通过：包含来源自匹配、其他真实截图正例、真实反例和经现有客户区适配器处理的人工1536×864缩放样本。详细分类数量见任务结果JSON。
- 11种战利品121项局部交叉检查通过，对角线命中、不同物品拒绝；7项同类型灰态节点拒绝检查通过。
- 123项有完整画面用例并通过；14项缺完整对应正例或仅为参考/公共引用，未冒充完整场景验证。
- 自动战斗录屏100ms采样150次，得到3个静止窗口、22个旋转窗口。隐藏、变暗、遮挡归为不可观察，不当作关闭。单段录屏不能覆盖全部动画和背景。
- 离线工具直接回归6项通过：裁剪边界、原始RGB保持、alpha约束、局部搜索范围、平移定位、空遮罩拒绝及低对比文本提取。
- 审核页面JavaScript语法、初始渲染、名称搜索、状态筛选与空搜索通过；本地静态资源链接检查通过。
- 工作区布局/源码边界检查通过；模板变更检查显示正式模板改动0项。

## 尚未覆盖

8项功能模板缺少完整对应画面正例：律荷招募完成、首领紫色领取完成、2→3、3→4、候补雅洛薇、次元核、大祭司戒指、紫色战利品选中边框。另4项只有灰态局部参考、2项复用已有公共网络资源。缺完整对应的预览只画搜索范围，不伪造匹配框。

来源自匹配、局部交叉检查和人工缩放不能替代更多真实场景与实机验证。阈值仅对当前素材集验证；只有一种用户背景和有限动画样本。阶段切换、事件升阶和盗贼招募券特殊分支没有实现。

用户审核后，逐模板执行候选验证、prepare、计划核对、apply，再开展运行功能实现；任何候选或ROI变更均须更新对应预览。

## 复现

```powershell
.\.venv\Scripts\python.exe -B -m scripts.calibration.build_chaos_review --run-dir artifacts/tasks/chaos/20260920-163417-template-review-c51812d6 --candidate-dir artifacts/template-candidates/20260920-170617-81e7982d
.\.venv\Scripts\python.exe -B -m pytest tests/scripts/calibration/test_chaos_review.py -q
.\.venv\Scripts\python.exe -B -m scripts.project.check_layout
.\.venv\Scripts\python.exe -B -m scripts.templates.check_changes
```

素材索引、候选规格及补充分析脚本保存在本地管理任务中；运行工具不自动访问聊天历史、不上传素材、不启动游戏。再次生成会更新未审核的候选和预览，审核哈希清单需同步生成。

## 第一批审核反馈修订（2026-09-20）

按用户列出的35个模板重做，新增保存18份原始素材（1张完整截图、17张局部图），累计180份用户素材。新完整图仍为2322×1306客户区，未拉伸素材。新素材优先，其余取既有原图；四人招募后的按钮按真实界面保留“进入探查”。

- 完整按钮保留按钮本体、边框及两端装饰，外围透明；阶段栏只保留绿色图标和阶段文字，文字保留窄暗边以区分1与3。
- 招募券仅保留职业图标及名称；英雄身份保留完整职业图标与名称；完成卡保留“招募完毕”、名称和职业图标；可变字段不参与匹配。
- 筛选与属性/职业按钮保留完整按钮本体及圆角；金色英雄选中边框保留四周，内部人物和文字透明。
- 精细遮罩保存于任务inputs/revision1_masks；工具读取等尺寸灰度遮罩，验证尺寸、类型、非空及原有透明约束，仅修改alpha。来源记录保存遮罩路径和哈希。

新增`previews/revision1.html`和3张`revision1-candidates-*.png`，全部模板页同步更新。`results/revision1_scope.json`核对候选PNG恰好变更这35项，其他候选PNG字节不变。重做项119个完整画面离线检查通过；全包当前422项检查，其中未改动项复用缓存结果。律荷招募完成卡仍只有局部素材，未声称完整场景验证。战利品与灰态节点未改动，沿用之前128项结果。

本轮直接工具回归7项通过，新增精细遮罩形状/类型拒绝和原始RGB/透明保持检查；候选哈希、RGB、页面资源链接、布局/源码边界与正式模板零改动检查通过。没有正式登记、游戏运行或提交推送。

本轮重现顺序：运行任务`scratch/revise_batch1.py`生成修订规格和遮罩，再运行上述build命令及`scratch/finalize_review.py`更新预览与审核哈希。旧`scratch/make_spec.py`只生成首版规格，不能单独运行覆盖本轮反馈。

## 第二批细修（2026-09-20）

用户批准上一批35项中的其余27项，要求继续修正8项：hero_twins、hero_arowell、hero_teacher及五个recruited完成卡。批准范围仅为上一批，其余原始审核项未据此自动批准。批准记录及对应候选/遮罩哈希、裁剪、搜索区域保存在任务results/user_approval_revision1.json。

取消职业图标孔洞填充，仅保留原图笔画及抗锯齿边缘；三个长名称末端按放大原图排除头像/发饰像素。裴妮完成卡的职业图标扩大到完整原图范围，恢复之前被裁掉的左弧。文字及图标保留原始RGB，不重绘。

8项相关25个离线检查通过，律荷完成卡仍缺完整场景正例。results/revision2_scope.json核对只有这8项候选PNG改变；上一批已批准27项的候选、遮罩、裁剪及ROI均核对不变。新预览在previews/revision2.html及revision2-candidates.png，全部页及上一批页面同步显示批准状态。尚未正式登记或启用运行流程。

重现本轮时，在第一批规格基础上运行scratch/revise_batch2.py，然后运行build及finalize_review；不要仅运行旧批次脚本覆盖新遮罩。

## 第三批名称末端细修（2026-09-20）

用户批准第二批其余五个完成卡，累计已明确批准32项；仍待修正hero_twins、hero_arowell、hero_teacher。批准证据保存在results/user_approval_revision2.json，与上一批记录分别绑定对应候选及遮罩哈希。

三个名称在头像上的末尾笔画与底图亮色混合，仅使用颜色筛选无法准确排除底图。本轮从既有无遮挡同名文字生成字形辅助遮罩，按原始前段文字及逐字位置对齐，再限制原图末尾像素的参与范围，清除字间与笔画间隙的头像色块及孤立残点。只缩放辅助遮罩，候选RGB没有缩放或重绘；职业图标及左侧内容保持不变。辅助来源、位置与尺度写入results/revision3_alignment.json，用户问题截图另存inputs/revision3_feedback.json，不作为游戏校准源。

3项相关12个离线检查通过，其他候选PNG不变；已批准32项的候选/遮罩/裁剪/ROI核对不变。previews/revision3.html提供逐项原图、遮罩、区域及末尾放大前后对照，PNG预览为revision3-candidates.png。页面图片使用验证指纹作为版本参数，避免沿用旧版浏览器缓存。

重现：第三批规格由scratch/revise_batch3.py生成，随后执行build和finalize_review。仍停在模板审核阶段，不登记或启用运行功能。

## 本批35项审核完成

用户明确通过revision3中的三个名称模板，批准记录保存于results/user_approval_revision3.json，绑定候选、遮罩、裁剪、搜索区域及候选阈值。本批35项全部通过；全包其余102项仍待审核。此次仅更新审核记录和页面状态，候选像素、遮罩与识别配置未改动，未执行正式登记或业务实现。

## 第四批节点修订

按用户反馈重做7种点亮节点及7种灰态参考，共14项。点亮节点遮罩保留完整亮色椭圆本体和突出其外的图标，剔除上方可选三角、外围细环及地图背景；精英和补给从完整原图重新确定取样范围。椭圆轮廓只用于alpha，候选RGB保持原图，不绘制或补造图像。灰态只保留中央较亮图标，利用相应点亮图标的字形辅助排除同亮度地图纹理，删除椭圆底图。

14项的28个完整画面检查通过。局部检查105项通过：7个点亮模板分别检查14种点亮/灰态原始样本，共98项；7个灰态模板各做一次原始局部正例检查。灰态反例使用含原始椭圆和地图的完整局部截图，没有用裁小后的图标造成模板放不下的平凡拒绝。局部检查不能替代完整客户区或实机验证。

4项灰态参考（补给、商店、精英、首领）仍缺可靠完整画面对应；补给点亮素材仍是经过状态，缺少带可选三角的补给正例。相关说明保留在审核条目。以前通过的35项及其余候选PNG未变，变更范围核对只涉及指定14项。没有正式登记或实现运行功能。

新增审核页面previews/revision4.html，附点亮/灰态两张PNG总览和搜索区域；本轮结果在results/revision4_validation.json、提取参数在results/revision4_extraction.json。重现顺序为scratch/revise_batch4.py、build、scratch/validate_revision4.py、scratch/finalize_review.py。战利品模板未变，121项既有战利品交叉结果沿用；7项节点灰态拒绝记录已更新。

## 第五批补给素材替换与灰态完整对照

用户新增2张完整截图、1张可选补给局部原图和1张商店问题预览截图。游戏原始素材按原字节保存并纳入索引，累计183份；两张完整截图均验证并裁出2322×1306客户区。问题预览单独保留为反馈证据，不作为游戏模板来源。

- node_shop仅补回右下基座遗漏的原始亮色及边缘像素，提供左旧/右新放大对比。
- node_loot换为新提供的可选补给局部原图，与新完整截图中的节点对应；本体排除上方三角，三角仍独立检测。旧经过状态图片不再作为制作来源。
- node_gray_loot、node_gray_shop、node_gray_elite、node_gray_boss的PNG和遮罩不变，新增完整截图正例，均识别通过。局部原图与完整截图无法确认同一帧的项目，如实区分来源对应与独立正例；区域预览显示实际匹配框。

六项共12个已有完整画面/缩放/反例检查通过；节点局部105项检查重新通过。变更范围核对只有node_shop和node_loot两张候选PNG改变，其他候选及已批准35项不变。此前4种灰态缺完整正例和补给缺可选状态样本的限制已由本次素材补齐；补给仍仅有一个可选状态完整来源画面，尚无独立同状态完整正例。

审核页previews/revision5.html，结果results/revision5_validation.json，新增素材对应结果results/revision5_crop_links.json。重现顺序：scratch/revise_batch5.py、build、scratch/validate_revision4.py --revision5、scratch/finalize_review.py。未正式登记、未操作游戏或实现业务流程。

## 第五批6项审核通过

用户通过当前revision5页面中的商店、补给和四种灰态对照，共6项。批准记录results/user_approval_revision5.json绑定候选/遮罩哈希、裁剪、搜索区域及阈值。累计41项明确通过，其余96项仍待审核；本次不修改候选图片或识别参数，只更新审核记录和显示状态，未正式登记或实现运行代码。

用户随后明确补充：之前重做的14个节点模板全部通过。results/user_approval_revision4.json记录该整组当前版本（包含第五批修正），补齐其余8项。累计49项通过，88项待审核；先前41项统计由此更新。模板像素和识别参数保持不变。

## 第六批按钮与结算文字修订

按用户反馈重做10项：node_enter、start_battle、victory、failed、continue_exploration、elite_choose_reward、boss_choose_reward、unclaimed_confirm、journey_close、rank_confirm。七个按钮从完整原图扩大取样，按完整按钮轮廓制作透明遮罩，包含两端装饰或圆角；精英战利品入口及未选奖励确认按钮补回原先被裁切的边缘。VICTORY和FAILED仅保留文字；VICTORY保留笔画内部原始渐变，O/R字腔保持透明。journey_close仅保留左侧关闭图标及文字和原有抗锯齿边缘。

原始RGB不变，不重绘文字、按钮或背景。此次候选PNG变更范围严格为指定10项，已通过49项及其余候选未改动。36项有关完整画面、自匹配、反例与人工缩放检查通过，结果见results/revision6_validation.json。该结果不等于实机验收；没有新增游戏采集。

审核页previews/revision6.html及PNG总览revision6-candidates.png已更新，模板、遮罩、搜索范围和点击位置一并展示。重现顺序为scratch/revise_batch6.py、build、scratch/finalize_review.py。尚未正式登记或实现自动化流程。

用户已明确通过本批10项，批准记录位于results/user_approval_revision6.json，绑定当前候选、遮罩、裁剪、ROI及阈值。累计59项通过、78项待审核；仅更新审核记录与页面状态，候选及识别参数未改动，尚未正式登记。

## 第七批升阶图标与修整离开按钮

重做rank_12、rank_23、rank_34、rank_45及camp_leave共5项。旧等阶遮罩的饱和度条件漏掉青色部分，本轮完整保留两个等阶图标的青色三角及渐变斜边，并保留数字、转换箭头和Max字样；仅去除底图，原始RGB保持。camp_leave从完整客户区重新取样，保留完整按钮、边框与两端装饰，外围透明。

7项已有完整画面/缩放检查和四种升阶之间16项局部交叉检查通过；局部检查验证自身命中及其他等阶拒绝，不代替完整画面验证。rank_23和rank_34仍缺完整对应截图，审核页继续明确标注。PNG变更范围恰好为指定5项；已批准59项不变，未正式登记或实现运行逻辑。

审核页previews/revision7.html、总览revision7-candidates.png、结果results/revision7_validation.json。重现：scratch/revise_batch7.py、build、scratch/revise_batch7.py --validate、scratch/finalize_review.py。

用户已明确通过本批5项，批准记录results/user_approval_revision7.json绑定当前候选/遮罩哈希、裁剪、ROI及阈值。累计64项通过、73项待审核；rank_23和rank_34缺完整截图对照的验证限制仍保留。此次只更新审核记录与页面状态，未改候选或识别参数，尚未正式登记。

## 第八批候补名称与红色减号

按用户要求重做bench_teacher、bench_arowell、bench_selected。候补两名英雄从各自原始候补素材取样，用已批准的身份轮廓辅助定位透明遮罩，清理名称右侧头像/背景，保留完整职业图标与名称。候补截图RGB和原字号不变；参考遮罩的离线尺寸对齐不属于运行时缩放，也未改动已批准的hero_teacher/hero_arowell。

bench_selected改名为“候补选中红色减号图标”，仅保留完整红色圆形减号按钮及边缘，删除白框和卡片背景；仍用于确认已进入更换状态，action为空，不点击减号。完整图标右缘超出原650px候补范围，bench搜索区右扩至700px，三个相关模板的区域预览同步更新。

7项完整画面/缩放/反例检查、4项局部身份交叉检查通过。bench_arowell仍缺替换后的完整画面正例，已保留限制。候选PNG仅变更这3项，已批准64项及其他候选不变；尚未正式登记、未实现运行流程。

审核页previews/revision8.html、PNG总览revision8-candidates.png；结果results/revision8_validation.json、遮罩参考来源和对齐记录results/revision8_alignment.json。重现顺序：scratch/revise_batch8.py、build、scratch/revise_batch8.py --validate、scratch/finalize_review.py。

用户已明确通过本批3项，批准记录results/user_approval_revision8.json绑定当前候选/遮罩哈希、裁剪、扩展后的bench ROI及阈值。累计67项通过、70项待审核；候补雅洛薇缺替换后完整截图正例的限制仍保留。本次只更新审核记录和页面状态，未修改候选或识别参数，尚未正式登记。

## 第九批替换图标、补给与商店

重做replace_button、supply_get_loot、supply_leave、investment、investment_entry、purchase_confirm、investment_done、shop_leave、shop_leave_confirm共9项。替换模板仅保留两个黄色环形箭头，仍沿用骑士图标相对搜索范围；补给入口仅保留文字及句号；未来投资仅保留名称、完整晶体及金色/青色环绕光环，去掉卡片、分隔线、说明图标和外部灰色装饰环。其余六项保留完整按钮本体、边缘及对应两端装饰，外围透明。

purchase_confirm按用户本次要求保留整个购买按钮，包含素材中的货币图标、50及购买文字，名称移除“排除价格”；未验证其他价格显示。所有候选保留原始RGB，仅修改裁剪与alpha；没有运行时缩放或图像重绘。

23项有关完整画面、自匹配、真实反例及人工缩放检查通过。候选PNG变更范围恰好为指定9项；已批准67项及其他候选不变。审核页previews/revision9.html、PNG总览revision9-candidates.png，结果results/revision9_validation.json。重现：scratch/revise_batch9.py、build、scratch/finalize_review.py。尚未正式登记或实现运行流程。

用户已明确通过本批9项，批准记录results/user_approval_revision9.json绑定当前候选/遮罩哈希、裁剪、搜索区域（含适用的相对区域）及阈值。累计76项通过、61项待审核。本次只更新审核记录与页面状态，未修改候选或识别参数，尚未正式登记。

## 第十批购买弹窗信息与金额排除（2026-09-21）

purchase_item重新提取左侧完整晶体/环绕光环及右侧两行文字，紫色底图和中间分隔线透明。图标以已批准investment的轮廓辅助定位alpha，弹窗原始RGB保持，未缩放或重绘候选像素。

purchase_confirm沿用已批准的完整按钮及原裁剪，只将金额显示区域设为透明，以允许数值和位数变化。排除区域在原始局部素材中为[148,84,100,40]，在候选中为[140,46,100,40]；货币图标、价格框边缘、购买文字和外部按钮轮廓不变。候选RGB不变，数字区域外的alpha逐像素一致。

用户明确要求修改已批准的购买按钮，旧批准版本按候选哈希记录于results/superseded_approvals.json；原批准记录保留，当前新版待复审。全包当前75项有效批准、62项待审核，其他已批准模板未改变。

4项原图/缩放识别检查及5项模拟金额变化检查通过。模拟为空白、5、150、9999和随机像素，仅改变排除区域；匹配位置与分数在规定误差内保持一致。它们只证明该金额区域不参与匹配，不代表已有其他商品或货币类型的真实截图验证。验证结果results/revision10_validation.json，编辑依据results/revision10_editing.json。

审核页previews/revision10.html、PNG总览revision10-candidates.png。旧批次页面及总览同步呈现当前版本及待复审状态。重现顺序：scratch/revise_batch10.py、build、scratch/validate_revision10.py、scratch/finalize_review.py。未正式登记或实现自动化流程。

用户随后明确通过第十批两项，results/user_approval_revision10.json绑定当前版本；购买按钮旧批准哈希继续保留为历史记录，新版批准有效。累计77项通过、60项待审核。

## 第十一批文本推进与11种战利品

重做text_continue、11个loot_identity和loot_confirm，共13项。文本推进只保留三角本体；战利品仅保留上方图标、名称及完整效果说明（含数字、标点），去除卡片边框、底纹、公共N标记和其他背景。戒指中空处透明，绘画保留完整画框及画面、排除画框外紫色底纹。文字保留原始灰色或彩色像素，不改色、不重绘。loot_confirm改为完整选择按钮，保留边框和两端装饰，外围透明。战利品优先级保持不变。

35项相关完整画面、自匹配、真实正例/反例和人工缩放检查通过；11×11共121项局部战利品交叉检查重跑通过。未变化的7项节点灰态检查沿用之前有效结果，未用新裁剪覆盖原始灰态反例。次元核和大祭司戒指仍缺完整画面正例，保留相应标注，局部交叉检查不代替完整画面验证。

候选PNG变更范围严格为指定13项，已批准77项及其他候选不变。审核页previews/revision11.html，附三张PNG总览；结果results/revision11_validation.json、results/cross_validation.json。重现：scratch/revise_batch11.py、build、scratch/cross_validate.py --loot-only、scratch/finalize_review.py。未正式登记、未实机或实现运行流程。

用户已明确通过本批13项，批准记录results/user_approval_revision11.json绑定当前候选/遮罩哈希、裁剪、ROI及阈值。累计90项通过、47项待审核；次元核与大祭司戒指缺完整截图正例的验证限制仍保留。本次只更新审核记录与页面状态，未修改候选或识别参数，尚未正式登记。


## 第十二批：统一战利品选中边框

按用户要求，将loot_selected_normal、loot_selected_purple合并为loot_selected；contract_selected保持原候选、遮罩、搜索区域及参数，未纳入合并。两个旧键退出当前候选清单与识别草案，旧产物保留作历史。有效候选134份、公共引用2项，共136项；90项已通过、46项待审核，本次统一候选待复审。

统一候选取普通卡左侧高亮边框及折角、圆点特征，不绑定左右间距；只改变裁剪和alpha，原始RGB未缩放或重绘。后续选中判断限于已定位目标卡片左边框，不得将邻卡选中误作目标选中，无边框点击操作。

8项检查通过（来源自匹配1、普通卡独立完整正例3、普通/紫色未选中完整反例3、人工缩放1）；紫色选中真实局部正例1项另行验证通过，颜色0.987866、结构0.972475。候选阈值0.965、结构门槛0.8；紫色选中仍缺完整画面，不视为实机验收。新候选之外的现有候选哈希全部不变，contract_selected特别核对一致。

预览previews/revision12.html、revision12-validation.html、revision12-candidates.png；结果results/revision12_validation.json，编辑依据results/revision12_editing.json。重现顺序：scratch/revise_batch12.py、build、scratch/validate_revision12.py、scratch/finalize_review.py。未正式登记或启用运行流程。


## 第十三批：统一选中模板补充右侧

按用户要求，在同一个loot_selected中加入右侧高亮边框，与左侧折角、圆点共同匹配；内部卡片图文透明，原RGB不变。候选尺寸460×710，普通/紫色共用一个模板。仅loot_selected候选哈希变化，contract_selected及其他候选不变。

8项来源/真实完整正反例/缩放检查以及1项紫色真实局部正例通过；候选阈值0.966、结构0.8，紫色局部颜色0.987335、结构0.970854。紫色选中仍缺完整截图，不视为实机验收。第十二批单边验证页面标记为历史版本。

预览previews/revision13.html、revision13-validation.html、revision13-candidates.png；结果results/revision13_validation.json，编辑依据results/revision13_editing.json。136项中90项已通过、46项待审核；新版统一选中模板尚待批准，未登记或启用运行流程。


用户已通过第十三批左右边框版loot_selected；results/user_approval_revision13.json绑定当前候选与遮罩哈希、裁剪、ROI和阈值。累计91项通过、45项待审核。紫色选中缺完整画面正例的限制保留；未正式登记或启用流程。


## 第十四批：六项图标边框与文字修订

用户批准上次45项待审核清单中除difficulty_one、stage_selected、map_hero、levelup_close、elite_claim_complete、boss_claim_complete以及两个事件外的37项。批准记录results/user_approval_remaining_before_revision14.json绑定当前候选及参数；原始未使用独立mask_file的模板以候选RGBA哈希绑定遮罩。累计128项通过，8项待审核（本批6项和暂缓的event_torch、event_mimic）。两事件像素与配置均不变。

本次新增完整截图和裁剪各1份，按原字节保存，来源总185份用户素材及2份公共引用。客户区原像素2322×1306；局部阶段图与完整截图匹配0.999996。stage_selected改用新图第03条，保留四周高亮边框及原有发光边缘、内部透明，原始RGB不变。difficulty_one仅绿色图标与数字；map_hero仅图标；levelup_close仅点击关闭文字、无箭头；两种领取完成仅文字。首领使用相同文字的精英轮廓辅助去除底纹，RGB仍来自首领原图，未重绘。

19项来源/完整场景正反例及缩放检查，6项补充检查全部通过；补充含第01条Easy选中正例、第01/02/04/05条未选中目标反例及首领局部自匹配。新增03截图与既有stage_top的该区域像素一致，不作为独立变化场景。首领完成领取仍缺完整画面正例；阶段切换仍暂不启用。只改变指定6个候选，其他候选哈希全部保持。

审核页previews/revision14.html、revision14-validation.html，PNG总览revision14-candidates.png；验证results/revision14_validation.json。重现：scratch/revise_batch14.py、build、scratch/validate_revision14.py、scratch/finalize_review.py。没有正式登记或实机运行。


## 第十五批：阶段边框右侧内部残留

用户批准第十四批除stage_selected外的5项，results/user_approval_revision14.json绑定当前版本；累计133项通过、3项待审核（stage_selected、event_torch、event_mimic）。首领文字缺完整正例限制保留。

stage_selected只在候选相对坐标[1180,48,12,88]内清除内部倍率面板残留及零散点，共326个alpha像素置零。矩形外alpha逐像素保持，原RGB、裁剪、搜索区域和阈值0.97不变，左右边框和右上圆角保留。其他候选哈希均不变。

8项来源、完整正例、缩放、Easy选中与未选中条目检查通过。预览revision15.html、revision15-detail.png前后对照、revision15-candidate.png完整预览；记录results/revision15_editing.json和revision15_validation.json。重现：scratch/revise_batch15.py、build、scratch/validate_revision15.py、scratch/finalize_review.py。未正式登记或实机运行。


用户已确认第十五批stage_selected通过，results/user_approval_revision15.json绑定当前候选与遮罩哈希、裁剪、ROI及阈值。累计134项通过、2项待审核，仅event_torch和event_mimic待审核。未修改候选、未正式登记或启用运行流程。


## 第十六批：两个事件模板与搜索区域

event_torch仅保留左右选项标题、左侧完整效果说明与EXP+80条形图标（含胶囊本体和突出字形）；event_mimic仅保留左右选项标题及左侧效果说明。原选项底纹、卡框、星点和装饰透明。候选仍使用已校准完整客户区对应的原始像素，未缩放或重绘；两张新图按原字节保存为形状与区域意图参考，未伪造为完整校准样本。

按用户示意将共享event_options区域由[250,920,1830,385]改为[0,960,2322,346]：左右覆盖2322宽客户区，上边界下移40像素，下边界到1306，不包含桌面与系统边框。候选裁剪缩小后重算相对action，使原始右侧点击锚点保持不变；两侧选项相对位置保持。

6项来源自匹配、互为反例、人工缩放检查通过；另8项人工场景检查通过，分别验证两个模板平移至客户区左右边缘仍识别，以及缺失左/右一组选项时拒绝。人工场景不是独立实机样本，当前两个事件各只有一份完整画面，仍缺更多真实变化和未知事件样本。只改变两个事件候选及其共享ROI，其余候选哈希与批准状态不变。

审核页previews/revision16.html、revision16-validation.html，PNG总览revision16-candidates.png及两个regions预览；记录results/revision16_editing.json、revision16_validation.json、inputs/revision16_references.json。重现：scratch/revise_batch16.py、build、scratch/validate_revision16.py、scratch/finalize_review.py。当前134项通过、2项待审核，未正式登记或启用运行流程。


用户已通过第十六批两个事件模板及新版全宽ROI，results/user_approval_revision16.json绑定候选/遮罩哈希、裁剪、ROI、阈值及点击锚点。当前136项全部审核通过（134份chaos候选与2份公共网络引用），所有真实样本覆盖不足、人工样本和未实机验证限制继续保留。


## 按钮复用与正式登记完成

用户授权“进行修改吧，下一步按原计划进入逐项正式登记”。未改动任何候选PNG或遮罩，将场景与资源分离：camp_leave、supply_leave复用shop_leave；boss_choose_reward复用elite_choose_reward；shop_leave_confirm复用unclaimed_confirm。136个审核场景保留各自搜索范围、动作和语义，使用130份chaos专用资源及2份既有公共网络资源。资源复用只发生在chaos内部，未移入common，也未重复登记网络模板。四张旧候选仅保留审核历史，未写入正式目录。

本次复用验证93项通过：22项真实完整场景与人工缩放检查、71项其他按钮局部反例。结果与实际共用模板区域预览保存在管理任务的results/button_reuse_validation.json和previews/button_reuse.html。场景映射保存在results/resource_bindings.json及recognition_draft.yaml，运行流程尚未接入。

通过官方scripts.templates.register CLI，逐项执行prepare、核对完整计划、apply。每项仅在前一事务结束后生成下一计划；共130个事务全部committed，无遗留事务。正式PNG和manifest.json位于assets/templates/chaos，登记来源由工具写入docs/calibration/registrations/chaos-*.json。候选目录为artifacts/template-candidates/20260920-170617-81e7982d；逐项计划位于其中registration-plans，审批绑定的来源快照位于registration-sources。全部候选路径、计划路径和事务ID记录在管理任务results/registration_transactions.json，预览previews/registration.html。

130份正式资源可加载、解码，PNG字节与已批准候选一致，来源哈希及事务状态一致；48份原有模板/清单文件哈希保持。隔离临时副本验证资源缺失、损坏和基准不符均被资源加载器拒绝，其他功能资源不存在时可独立加载chaos清单。该结果只覆盖资源层，尚未验证自动卡厄思启动入口、网络依赖加载配置或实机流程。原有局部样本、人工场景和独立真实正例不足的限制保留；阶段切换候选虽已登记，运行分支仍暂不启用。


收尾检查：check_layout通过；check_changes核验130个新增模板，problems=[]；7项离线审核工具测试通过；页面136项批准、4项复用映射及130项事务链接核对通过；git diff --check通过。未运行全套测试，未提交、推送、打包或发布。


## 2026-09-21 三选一战利品扩充29项

用户批准全部图片与逐项优先级后，29项候选已按原PNG字节逐项正式登记至 `assets/templates/chaos/loot/`，合计40种三选一身份。此次包含召唤标记石真实小卡恢复（T2）；已有五种不重复登记。审核展示为图片预览加白色遮罩，无新增审核网页。

候选：`artifacts/template-candidates/20260921-221809-12d702c0/`。完整候选、核对计划、事务ID、审批及验证结果保存在任务 `20260921-221223-loot-batch-candidates-a1be3bc7/results/`。正式运行32项截图/人工缩放验证、20项相关测试通过；具体范围及限制见[运行验证记录](RUNTIME_VALIDATION.md)。


## 2026-09-22 巨眼事件已批准并登记

event_giant_eye的三选项纯文字候选已由用户通过，按原字节正式登记至events/options。事务146491facbc74a8e91c2a0ffd5ad385a，计划位于候选目录20260921-232433-9b51cf01/plans。已有文字三角图片不改像素，通过识别算法及事件专用等待规则修复白色背景漏识别和上下跳动问题；相关验证见[运行验证记录](RUNTIME_VALIDATION.md)。


## 2026-09-22 牢房已登记、铁笼与祭坛候选

牢房event_prison已批准并正式登记，事务7695c3fa12004f7fa6d6c8ac9636ce97；候选/计划位于20260922-002158-4d0d205d。新增铁笼event_cage、祭坛event_altar候选位于20260922-011544-e6eda8c6，两者保留原像素文字，背景透明；提供图片与白色遮罩等待审核。识别和流程记录见[运行验证](RUNTIME_VALIDATION.md)。


铁笼event_cage和祭坛event_altar现已审核通过并正式登记，批准PNG字节保持。事务cbfa422c88c24db3b446864c5c90c568、cd95e1b69b814ec9805b88b6f446e92b；计划在候选20260922-011544-e6eda8c6/plans。


## 2026-09-22 古剑/石塔/蘑菇及事件升阶入口已登记

四张候选经用户批准，保持原PNG字节正式登记。候选20260922-142540-d69bfa60，计划在该目录plans；逐项事务见任务20260922-142348-events-sword-cairn-mushroom-candidates-b35357ec/results/registration_transactions.json。既有32项候选验证沿用；三处旧模板人工缩放低分按用户要求暂不校准，见[运行验证](RUNTIME_VALIDATION.md)。
