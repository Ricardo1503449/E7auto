# 测试归属

| 目录 | 被测职责 |
| --- | --- |
| core | 配置与领域状态 |
| vision | 图像匹配、数字、入口、购买和滚动识别 |
| resources | 模板目录、加载和资产完整性 |
| platform | Windows、后台输入、分辨率与WGC |
| logging | 日志与异常停止诊断 |
| automation | 自动化流程、会话和停止控制 |
| ui | Qt页面、控件和工作线程 |
| scripts | calibration、validation、release、templates、project工具契约 |
| helpers | 公共工厂、替代服务和fixture |
| fixtures | 按common/shop/penguin等已登记功能归属的固定样本及来源记录 |

新增回归测试放回所属目录，不向根目录追加test文件。根目录只保留本说明、`__init__.py`和需要时的`conftest.py`。
项目路径从 `tests.helpers.paths` 导入；测试临时输出使用 `tmp_path`，不得写入fixtures。共用fixture放在helpers或conftest，不能从其他test模块导入。

WGC测试 `platform/test_wgc_capture.py` 必须与Qt测试分进程。只运行改动直接相关的测试；完整入口 `scripts/test-source.ps1` 仅在用户要求全套验证时执行。
