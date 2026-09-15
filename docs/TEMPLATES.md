# 模板资源组织与新增功能约定

本约定适用于秘密商店、企鹅兑换及后续所有新增功能。新增或迁移识别模板时必须同时维护目录、加载范围、校准记录和相关测试。

## 目录与归属

```text
assets/templates/
├── common/
│   ├── digits/       # 共用数字字形（含窄0和宽0）
│   └── network/      # 共用网络异常和重试文字
├── shop/             # 商店专用商品、按钮、天空石图标
├── penguin/          # 企鹅专用控件及运行必需的manifest.json
└── README.md

docs/calibration/     # 原始素材来源、裁剪参数、历史校准及验证记录
assets/ui/            # 界面背景、图标等展示资源
tests/fixtures/       # 可移植的离线回归样本
```

- 新功能必须建立 `assets/templates/<feature>/`，使用稳定、表意的小写英文目录名。不得把新功能图片直接放在模板根目录或已有功能目录里。
- 只有实际被两个及以上功能复用的模板才放入 `common/<用途>/`。单功能资源保留在该功能目录，确认发生复用后再提取；不要复制相同共享图片到多个目录。
- 同名“确认”“返回”等控件不代表可以共用。只有图片、遮罩、基准尺寸及识别语义适用于多个功能时才共享。
- `sky_stone_digit_*` 虽沿用历史文件名，实际归属为共享数字；`sky_stone_icon.png` 仍为商店专用。现有文件名和逻辑键保留，避免目录迁移改变识别行为。
- UI展示图放在 `assets/ui/`，测试图片放在 `tests/fixtures/`；候选图片和临时预览不进入正式模板目录。

## 加载边界

- 每个功能启动时只检查、加载它的专用模板和明确依赖的共享模板。另一功能的图片缺失或损坏不得导致本功能无法启动。
- 当前 `load_config(..., template_profile="shop")` 加载31张商店及共享模板；`template_profile="penguin"` 先加载13张共享模板，再由 `with_penguin_config` 校验并加入13张企鹅模板。
- 配置结构仍共用 `config/internal.yaml`，企鹅仍校验共用配置结构；上述隔离针对图片依赖，不表示各功能配置已经完全拆分。
- 新功能必须增加对应加载分支及明确的依赖集合，不得通过加载所有模板再忽略多余模板实现。同步功能启动入口和资源校验；未知功能标识应报错。
- `config/internal.yaml` 的 `template_manifests` 只登记common/shop/penguin清单路径。三个清单使用相同格式，文件名、来源与SHA-256仅在清单登记；两个功能共用 `template_manifest.py` 加载和完整性校验。来源信息仅供追溯，不读取来源文件。
- 界面搜索矩形及需要校准的动态区域参数均在YAML维护；两个入口都读取 `rois.left_icon_column`，其他企鹅控件各自读取 `rois.penguin_<控件名>`。清单中的 `source.crop` 不参与运行范围计算，不再自动外扩24像素。
- 动态区域保留代码计算：`vision.purchased_button_padding` 是已购买按钮围绕当前行购买位置的单侧留边（x水平、y垂直）；`vision.penguin_price_rect` 是购买按钮左上角以内的金额相对矩形，同时用于遮罩排除和金额读取。单个数字分割边界属于识别结果，不配置为固定屏幕坐标。
- 入口颜色和结构门槛分别读取 `vision.entry_thresholds.shop/penguin`；其他企鹅控件、金额数字门槛也读取配置。允许各功能使用不同的已验证值，PNG不因配置迁移而改变。

## 清单与来源记录

- 运行时必需的清单与该功能的模板同目录存放，并随程序打包，包括 `common/manifest.json`、`shop/manifest.json`、`penguin/manifest.json`。
- 纯来源、裁剪、历史校准和验证记录放在 `docs/calibration/`，不作为程序启动依赖，也不随模板目录打包。
- 当前历史清单中的 `output_path` 统一相对于 `assets/templates/`；企鹅运行清单中的 `file` 相对于 `assets/templates/penguin/`。原始来源路径仅供追溯，不视为可移植默认输入。
- 维护来源图片、基准尺寸、裁剪区域和遮罩依据。目录整理不得重新编码PNG或修改像素；企鹅等带完整性校验的模板更新须同步已审核的摘要。
- 不要求为格式统一而重写历史JSON/YAML；新清单须注明字段和路径基准。
