# 统计上报服务维护

## 所属和工具

公开项目仅维护客户端、上报Worker与数据库迁移；源码位于scripts/cloudflare/shop_stats。统计网页、具体域名配置和每日归档工具由私有统计仓库维护。运行源码不依赖scripts或私有仓库。

沿用登记的Wrangler 4.147.0及Miniflare 5.20261001.0-alpha，不新增客户端依赖。Node/npm由tools/state/tool-paths.json解析，工具和私有授权均经scripts.common.paths定位，不修改系统PATH或在源码目录安装node_modules。实际工具清单以package.json及lock为准。

使用scripts.project.artifacts创建shop任务或maintenance任务，将实际返回的目录传给tools run --run-dir。tools locate提供Node、Wrangler及缓存路径；tools stage仅复制上报入口、配置与迁移。

## 部署及兼容

生产上报根地址为https://stats.e7helper.com，保留POST /v1/shop-runs及其五字段协议，新增POST /v1/usage-days。原有客户端不需要改上报地址；不删除D1或覆盖原商店迁移。

0002_usage_days.sql只新增活跃日表与日期索引。先列出待执行迁移，确认仅适用新增项，再按授权remote apply；不使用模拟生产计数验收。cron每日清理90日之外的活跃明细，不处理商店去重记录和累计值。

0003_usage_installations.sql新增仅含匿名安装标识的累计表，并从现存usage_days全部日期去重初始化，包含features=0的记录；建表和初始化可重复执行。上线按数据库迁移、上报Worker、私有汇总与网页的顺序执行，迁移及部署分别需要授权。未过期上报将累计登记和日活合并原子提交，过期但有效的上报只登记累计标识并沿用discarded回执；原90日清理仅操作usage_days。客户端和安装器无需修改，旧版上报继续有效，历史缺失数据不补造。

发布使用现有tools run入口。网页服务使用私有仓库配置、同一D1绑定，通过具体配置路径部署；具体网址仅写在私有配置和私有README。两个Worker的workers.dev和版本预览均关闭。不能只增加域名别名，必须删除旧Worker的读取实现。

上报域名的旧/、/stats、/stats.svg、/v1/shop-stats、dashboard.css和dashboard.js均404，无Location或新地址提示。对/v1/shop-runs的HEAD返回405及Allow: POST，可用于无数据写入的HTTPS诊断。

configure --endpoint只更新客户端上报配置，configure --database-id只更新现有D1绑定，均不再重建公开README链接。保持游戏配置其他字节不变。

## 私有历史和快照

原公开统计自动任务已退役；不得在公开项目重建统计分支或自动插入统计链接。私有仓库继续维护每日快照及页面入口，完整旧统计提交链保留。迁移须核对源/目标SHA、文件路径/模式/类型/哈希、私有可见性；成功后才删除公开引用。

私有归档脚本验证只读汇总字段和比率，拒绝明细、错误版本汇总或不完整树；兼容新增的usage.total_users及不含累计字段的历史schema_version=2文档，不重写历史。每天只更新latest.json、latest.svg和当天history/YYYY-MM-DD.json，并按365日边界移除旧日期JSON与旧每日SVG；原tree中的维护文件保留。每日任务在私有仓库默认分支运行，权限为contents:write，不配置个人长期令牌。

## 定向验证与安装

隔离D1验证商店兼容与去重、活跃原子合并、日期保留及旧路由404；私有网页验证各时间窗按安装去重、版本归类、只读接口及失败保留显示。客户端验证旧偏好迁移、日活revision回执、跨日、忙碌暂停、系统代理和退出保留。按具体变更选择测试函数，不运行全套。

编译版--self-check在临时目录验证商店队列和日活队列，不接触用户状态、不创建主窗口、不产生正式活跃上报。--self-check-statistics-https以HEAD探测保留的上报入口，不写入生产记录。安装包验收仍由发布工具判断范围，覆盖升级保留state，卸载按原规则清理state。
