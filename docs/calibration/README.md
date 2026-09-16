# 校准与来源记录

此目录保存模板来源、裁剪参数和历史验证证据，不参与运行时模板加载，也不随 `assets/templates` 打包。

清单中的 `output_path` 相对于项目 `assets/templates/`。来源截图路径保持原始记录，可能只在原校准机器存在。运行必需的企鹅清单仍位于 `assets/templates/penguin/manifest.json`。

新增功能和模板请遵循[模板资源组织约定](../development/TEMPLATES.md)。

记录按shop、penguin、platform主题分类；registrations保持固定位置和原始字节。文件名与主题的映射在scripts/project/layout.json中登记，工具使用calibration_record_path定位。
