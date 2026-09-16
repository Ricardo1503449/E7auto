# 构建临时目录

只保存 `nuitka/<构建ID>/` 和 `staging/<构建ID>/` 下可重新生成的文件，构建ID对应 `artifacts/releases/<版本>/<构建ID>/task.json`。

历史源码、输入图片、校准预览和验证报告不得放在这里。发布脚本将成功编译的独立程序放到 `dist/launcher.dist`，发行ZIP仍保存在 `dist/`。

日志和证据写入对应的 `artifacts/releases/`。未确认构建结束前不要清理构建目录；不会自动清理其他任务的产物。
