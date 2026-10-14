# 参与开发 / Contributing

在 Apple Silicon macOS 上开发。Python 后端使用标准库；SwiftUI 应用需要 Swift、AppKit 和 SwiftUI。请先阅读 README 的权限和数据边界。

Develop on Apple Silicon macOS. The Python backend uses the standard library; the SwiftUI app needs Swift, AppKit and SwiftUI. Read the permissions and data boundaries in the README first.

## 验证 / Validation

```sh
python3 validate_localization.py
python3 -m unittest discover -s tests -p 'test*.py' -v
python3 scripts/build_app.py
```

测试不得使用真实凭证或发送实盘交易。新功能应保持账户及客户端默认只读、绑定隔离、逐笔人工确认和未知结果不重试。改名和语言修改不得改变账户身份或权限。

Tests must not use real credentials or send live trades. Preserve read-only defaults, client account isolation, manual per-action approval and no retries for uncertain results. Naming and language changes must not change account identity or permissions.

## 修改流程 / Change workflow

在分支上修改，完成相关验证，再发起 Pull Request。说明问题、修改后的行为和验证结果。README、开发文档、插件介绍和新增用户提示需同时维护中文及英文；标识符、API 参数和用户填写的账户名称不翻译。

Make changes on a branch, run relevant validation, then open a pull request. Explain the problem, resulting behavior and validation. Maintain Chinese and English in the README, developer documentation, plugin descriptions and new user messages; do not translate identifiers, API parameters or user-entered account names.

请不要提交本机数据库、钥匙串导出、API 凭证、AI 客户端配置或包含金融信息的截图。源码仓库排除生成的应用和构建缓存。

Do not commit local databases, Keychain exports, API credentials, AI client configuration or screenshots containing financial information. Generated applications and build caches are excluded from this source repository.

`tests/fixtures/trading212-order-schema.json` 是官方订单请求结构摘录，测试不联网下载规格。API 变化时核对夹具中的来源链接并更新摘录。

`tests/fixtures/trading212-order-schema.json` contains excerpts of the official order request schemas; tests do not download specifications over the network. When the API changes, check the source link in the fixture and update the excerpts.
