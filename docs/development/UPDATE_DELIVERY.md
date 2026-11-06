# 更新清单与安装包分发

## 来源与用户行为

客户端读取本仓库独立`updates`分支的`latest.json`，不查询GitHub REST API。
正式清单地址为`https://raw.githubusercontent.com/Ricardo1503449/E7auto/updates/latest.json`；
另一来源为在此前加`https://gh-proxy.org/`。两个来源同时请求，每次逻辑检查最多10秒，
每个响应最多256 KiB。手动检查加入正在进行的检查；当前进程60秒内复用完整成功结果，
弹窗显示检查时间。后台仍按6小时成功间隔及原失败退避调度。

两个有效清单选择更高正式版本；同版本的安装包名称、地址、大小或摘要冲突时拒绝更新。
来源较旧不能覆盖本机已确认的较高版本记录。一个来源失败时，另一个有效来源仍可完成检查。
失败保留版本提示，历史提醒缓存不恢复可执行的安装包元数据。后台只检查版本，不预下载或安装。

安装包按`gh-proxy.org/<官方安装包地址>`、官方地址的顺序尝试，每源至多一次。
网络/HTTP/完整性失败可以换源；每次清空本任务部分文件、重置进度和摘要，始终绑定
同一清单的版本与安装包身份。写盘失败、用户取消或15分钟总期限耗尽立即终止。
每条传输沿用30秒空闲超时及最多5次重定向；HTTPS跳转仅允许既有GitHub资源主机和
`gh-proxy.org`/`gh-proxy.com`，加速地址必须仍指向本仓库的Release文件。

限流按来源处理。查询的`Retry-After`及额度耗尽时的`X-RateLimit-Reset`写入schema 2缓存，
即使另一来源尚未完成时关闭窗口，也保留已经观察到的等待。所有查询来源均在等待时才
暂停整次检查。下载在当前进程内记录来源等待，重试时跳过尚未到期的来源。
schema 1旧缓存只恢复版本、说明和检查时间，丢弃旧REST API等待并重新确认新来源。
等待弹窗区分当前响应和此前等待，禁用即时重试并提供发布页面按钮。

## 签名与清单协议

外层JSON只有`schema_version: 1`、`payload`及`signature`。后两者分别是原始UTF-8载荷
和分离式CMS签名的Base64；客户端验证原字节，禁止重复JSON字段，验签成功后再解释载荷。
载荷包括`repository`、`version`、`published_at`、`notes`、`commit`、`asset`：

- 仓库固定为`Ricardo1503449/E7auto`，版本为`vX.Y.Z`正式版，提交为40位Git摘要，时间为UTC。
- `asset`包含`name`、`url`、`size`、`sha256`；名称和官方URL必须与版本及本仓库一致，
  大小为不超过512 MiB的正整数，SHA-256为64位小写十六进制。
- 下载来源由客户端固定列表派生，清单不能指定任意加速站或可执行文件地址。

发布端使用RSA 3072位、SHA-256和分离式CMS签名。客户端使用已有PyWin32的
`CryptVerifyDetachedMessageSignature`并固定签名证书DER的SHA-256；证书作为公钥容器，
不安装到系统证书库，也不依赖购买代码签名证书。原有文件大小、摘要、PE内嵌版本及
安装目录/登记/拥有文件检查继续执行。

签名私钥经Windows DPAPI CurrentUser保护，位置由公共路径入口解析为
`tools/state/releases/update-signing.pfx.dpapi`，不提交、不打包、不输出解密后的密钥。
首次身份初始化使用`python -B -m scripts.release.update_feed --init-key`；已有身份拒绝覆盖。
证书摘要和公开验签样本固定在平台适配器中。初始化新身份不等于换钥；更换发布身份
必须同步客户端信任锚点并重新交付，不能用新建密钥冒充丢失的现有私钥。
编译版必须包含`win32crypt.pyd`；`--self-check`在Qt/WGC同进程中执行固定样本验签，
输出`update_signature_ready`与错误信息，失败退出非零。

## 发布与独立分支

源码版本整理、相关验证、构建安装验收、集中提交推送及GitHub Release遵循既有流程。
Release仍仅上传标准安装EXE；静态清单不加入Release附件，也不写入源码默认分支。
GitHub发布完成且确定为Latest正式版后，在本项目`.venv`中执行：

```powershell
.\.venv\Scripts\python.exe -B -m scripts.release.update_feed --run-dir '<本次发布记录目录>'
# 获得updates分支发布授权后：
.\.venv\Scripts\python.exe -B -m scripts.release.update_feed --run-dir '<本次发布记录目录>' --publish
```

默认命令只在本次release任务内生成并验签`latest.json`，同时保存公开载荷与结果。
工具先运行最终workflow检查，再核对GitHub正式Release、标签实际提交、唯一附件的
名称/URL/大小/服务端摘要。签名私钥必须与客户端固定身份一致。

`--publish`只更新`updates/latest.json`，首次建立独立无源码父提交的分支，之后保留历史及
其他既有文件。拒绝降低版本或替换同版本安装包身份。更新引用前再次核对官方Latest、
标签及附件；使用非强制引用更新，冲突不覆盖。成功后核对远端提交和文件原字节。
超时或结果不明确时先只读核对远端，不盲目重试；生成/发布失败保留旧清单及本地证据。
开发者发布工具可以使用本机已授权的GitHub CLI；客户端不携带该凭据。

首次启用前须真实发布独立清单，并用正式Qt客户端核对查询及完整下载。第三方服务仍
可能限流、缓存或中断，不能承诺任何网络均可用。旧客户端需要升级后才使用新链路。
验证范围与未完成项见[更新分发验证](../validation/common/UPDATE_DELIVERY_VALIDATION.md)。
