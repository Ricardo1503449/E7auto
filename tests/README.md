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

源码依赖边界测试位于core/test_source_boundaries.py；源码迁移不改变本目录按被测职责分类的规则。WGC与Qt分进程，测试桩应替换新模块的实际调用位置。

UI生命周期由ui/conftest.py统一管理：会话共用QApplication，用例结束后关闭并销毁本用例的新窗口、停止计时器、处理DeferredDelete，再回收Python对象。Qt辅助代码位于helpers/qt.py，不从helpers/__init__.py导出。ui/test_window_lifecycle.py检查窗口释放和失效包装对象，并在受控子进程进行多轮压力回归；不关闭GC来绕过崩溃。
