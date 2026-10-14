# 语言扩展 / Adding an app language

应用支持中文 `zh-Hans`（默认）和英语 `en`。在“设置 / Settings → 应用语言 / App language”中切换，或按 ⌘,。偏好保存到本机 `UserDefaults` 的 `appLanguage`，不会改变账户、凭证、绑定或交易权限。macOS 系统菜单和提示使用系统语言。

The app supports Chinese `zh-Hans` (default) and English `en`. Switch under **Settings → App language** or press ⌘,. Preferences are stored in local `UserDefaults` under `appLanguage` without changing accounts, credentials, assignments or trading permissions. macOS menus and system prompts follow the system language.

资源目录为 `app/Trading212 Connection.app/Contents/Resources/Localization/`。`Localization.swift` 读取 `languages.json` 并自动生成语言列表，无需修改语言选择器业务代码。

Catalogs live in `app/Trading212 Connection.app/Contents/Resources/Localization/`. `Localization.swift` reads `languages.json` and generates the language list without changes to picker logic.

## 新增语言 / Add a language

1. 复制 `zh-Hans.json` 为新文件，例如 `fr.json`。保留字典键，仅翻译值。键是界面和已知服务消息共享的稳定来源短语，不能仅为了改文案而改键。
2. 保留 `{0}`、`{1}` 等位置占位符及出现次数，可按语法调整顺序。替换的账户名称、价格和标识符只插入一次，不再翻译或解释为新占位符。
3. 在 `languages.json` 增加唯一语言 ID、原生名称和本目录下的简单文件名，例如下方条目。保留 `defaultLanguage: zh-Hans`。
4. 在 `Contents/Info.plist` 的 `CFBundleLocalizations` 登记新 ID，完成验证，再重新构建和签名。直接修改已签名应用资源会使签名失效。

1. Copy `zh-Hans.json` to a new file, for example `fr.json`. Keep dictionary keys unchanged and translate values only. Keys are stable source phrases shared by UI and known service messages; do not rename them just to change wording.
2. Preserve positional tokens such as `{0}` and `{1}` and their occurrence counts; reorder them for grammar if needed. Substituted names, prices and identifiers are inserted once, without translation or reinterpretation as tokens.
3. Register a unique language ID, native name and simple catalog filename in `languages.json`, as shown below. Keep `defaultLanguage: zh-Hans`.
4. Register the new ID in `CFBundleLocalizations` in `Contents/Info.plist`, validate, then rebuild and sign. Editing signed app resources directly invalidates the signature.

```json
{ "id": "fr", "name": "Français", "file": "fr.json" }
```

缺少键或占位符不匹配时回退到默认中文。文件缺失或格式无效的语言不进入列表；保存的语言不可用时回退到默认。未知服务消息原样显示；显示翻译不改写历史日志。MCP 响应语言独立于应用偏好。

Missing keys or mismatched tokens fall back to default Chinese. Missing or invalid catalog files are excluded; unavailable saved languages fall back to the default. Unknown service messages are displayed verbatim; display translation leaves historical logs intact. MCP response language is independent of app preferences.

## 验证与构建 / Validate and build

```sh
python3 validate_localization.py
python3 -m unittest discover -s tests -p 'test_localization.py' -v
python3 scripts/build_app.py
```

界面文案使用 `L(key, argument0, ...)`；已知服务提示使用 `D(source)`；用户账户名称、ticker 和 API 路径直接显示。新增模板需更新两种语言并检查组合消息。环境标签使用统一映射：中文实盘／模拟盘，英文 Live／Paper；未知环境不伪装成已知环境。

Use `L(key, argument0, ...)` for UI text, `D(source)` for known service messages, and display user names, tickers and API paths directly. Update both catalogs for new templates and check composed messages. Environment labels share one mapping: Chinese 实盘 / 模拟盘, English Live / Paper; unknown environments are not mislabelled as known ones.
