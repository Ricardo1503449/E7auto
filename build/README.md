# 构建临时目录

只保存 `nuitka/<构建ID>/` 和 `staging/<构建ID>/` 下可重新生成的文件，构建ID对应 `artifacts/releases/<版本>/<构建ID>/task.json`。

历史源码、输入图片、校准预览和验证报告不得放在这里。独立程序暂存在 `build/nuitka/<构建ID>/launcher.dist`，安装器暂存输出位于 `build/staging/<构建ID>/setup`；最终安装EXE保存在 `dist/`，不再生成ZIP。

安装包完成封存并写入dist后，构建脚本自动删除本次 `build/nuitka/<构建ID>/` 和 `build/staging/<构建ID>/`，包括launcher.build和launcher.dist；失败构建保留现场。清理失败记录警告及build-result.json中的清理状态，不影响已生成的安装包。历史目录由用户手动清理，不扫描删除其他构建。

安装验收按需重新创建本次staging目录，全部验收通过且测试安装完整卸载后自动清理；失败时保留现场。日志和证据写入对应的 `artifacts/releases/` 并保留；dist和项目编译缓存不受自动清理影响。未确认构建或验收结束前不要手动清理构建目录。
