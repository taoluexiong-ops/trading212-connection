# Trading212 Connection

**将 Trading 212 账户连接到 Codex、Claude Code 和 Claude Desktop 的 macOS 本机中转站。默认只读，交易逐笔确认。**

**A local macOS hub connecting Trading 212 accounts to Codex, Claude Code and Claude Desktop. Read-only by default, with manual approval for every trade.**

当前版本 / Current version: **1.2.2** · Apple Silicon · macOS 13+

## 功能 / Features

| 中文 | English |
|---|---|
| 在固定窗口输入 API Key 和 API Secret，凭证保存到 macOS 钥匙串 | Enter API credentials in a native window; store them in macOS Keychain |
| 多账户管理；每个 AI 客户端绑定自己的可访问账户 | Manage multiple accounts and assign an account to each AI client |
| 账户和客户端均默认只读，下单与撤单权限分别手动开启 | Account and client permissions default to read-only; enable placement and cancellation separately |
| 所有交易请求须由用户在原生窗口逐笔确认 | Every trade request requires the user's approval in the native window |
| 查看 API 请求、限流头、错误和按来源统计的使用记录 | View API requests, observed rate limits, errors and usage by client |
| 中文／英语即时切换；可继续增加其他语言 | Switch between Chinese and English immediately; extend with more languages |
| 账户名称由用户编辑，不随界面语言改变 | Edit account display names independently of the interface language |

支持本地 AI 客户端。ChatGPT／Claude 网页版、手机端和云端会话需要另行建设可认证的远程服务。本项目不是 Trading 212 官方产品，也未发布到公共插件目录。

Supports local AI clients. ChatGPT/Claude web, mobile and cloud sessions require a separate authenticated remote service. This project is not an official Trading 212 product and is not published in a public plugin marketplace.

## 从源码开始 / Get started from source

需要 Apple Silicon Mac、macOS 13 或更新版本、Apple 命令行开发工具（提供 Swift 编译器），以及 `/usr/bin/python3`。Python 后端仅使用标准库。

Requires an Apple Silicon Mac, macOS 13 or newer, Apple's command line developer tools (for Swift), and `/usr/bin/python3`. The Python backend uses only the standard library.

```sh
git clone https://github.com/taoluexiong-ops/trading212-connection.git
cd trading212-connection
python3 scripts/build_app.py
python3 scripts/install_app.py
```

如未安装 Apple 命令行开发工具，可运行 `xcode-select --install` 并完成系统安装提示。源码构建会生成 `.build/Trading212 Connection.app` 和 `.build/Trading212 Connection-macOS-arm64.zip`。安装脚本把应用放在 `~/Applications/Trading212 Connection.app`，不收集凭证，也不自动连接任何 AI 客户端。已有连接数据仍使用原位置。

If Apple's command line developer tools are missing, run `xcode-select --install` and follow the system installation prompt. Building creates `.build/Trading212 Connection.app` and `.build/Trading212 Connection-macOS-arm64.zip`. The installer copies the app to `~/Applications/Trading212 Connection.app`, does not collect credentials or connect AI clients automatically, and retains existing connection data at its original location.

构建采用本机临时签名，未经过 Apple 公证。仓库保存源码和应用模板，不包含预编译应用或用户数据。

Builds use local ad hoc signing and are not notarized by Apple. The repository contains source code and an app template, with no precompiled application or user data.

## 连接账户 / Connect an account

1. 打开应用，在“连接与权限”添加连接，填写名称、环境和 API 凭证。环境标签为中文 **实盘／模拟盘**，英文 **Live／Paper**；底层 API 标识仍为 `live` / `demo`。账户和持仓读取验证成功后，凭证保存到钥匙串。
2. 在“AI 客户端”选择 Codex、Claude Code 或 Claude Desktop，再选择该客户端可访问的账户，点击“连接”，确认后重启或重新加载该客户端。
3. 查看安装状态、配置状态和最近握手。“已配置”仅表示配置通过校验，不表示 AI 客户端当前在线。

1. Open the app, add a connection under **Accounts & permissions**, and enter a name, environment and API credentials. Chinese labels are **实盘 / 模拟盘**; English labels are **Live / Paper**. API identifiers remain `live` / `demo`. Credentials are saved to Keychain after account and position access is verified.
2. Under **AI clients**, choose Codex, Claude Code or Claude Desktop and assign its accessible account. Click **Connect**, confirm, then restart or reload that client.
3. Check installation status, configuration status and the last handshake. **Configured** means configuration passed validation; it does not prove the client is online.

Codex 和 Claude Code 需要已安装的命令行程序；Claude Desktop 需要已安装的桌面应用。窗口可打开官方安装说明，但不会自动安装这些客户端。每个客户端只能通过本工具访问所分配的账户。窗口当前账户仅影响管理视图，不改变客户端绑定。

Codex and Claude Code require their installed CLI programs; Claude Desktop requires the desktop application. The window links to official installation instructions but does not install these clients automatically. Each client can access only its assigned account through this tool. The current window account affects the management view, not client assignments.

## 固定 Codex skill / Reusable Codex skill

根目录 `plugin.json` 和 `skills/` 提供固定 skill，仓库的 `.agents/plugins/marketplace.json` 提供本地插件目录。已安装 Codex CLI 时，可在仓库根目录运行下列命令，然后重启 Codex：

The root `plugin.json` and `skills/` supply the reusable skill; `.agents/plugins/marketplace.json` supplies a local marketplace. With the Codex CLI installed, run these commands from the repository root, then restart Codex:

```sh
codex plugin marketplace add .
codex plugin add trading212-connection@trading212-local
```

通过 `$trading212-connection` 或“打开 Trading212 Connection”调用。MCP 配置由应用窗口统一管理，插件本身不重复注册 MCP 服务。应用固定安装路径独立于插件缓存，Claude 客户端不会依赖 Codex 的插件缓存位置。

Invoke it with `$trading212-connection` or “Open Trading212 Connection.” The app window manages MCP configuration; the plugin does not register a duplicate MCP server. The installed app path is independent of the plugin cache, so Claude clients do not depend on Codex's cache location.

## 账户名称与语言 / Account names and language

新连接默认名称为 `Trading 212`。在账户页点击“编辑名称 / Edit name”可改成自己的标签，例如 `Trading 212 ISA`。保存后侧栏、账户选择菜单和当前绑定名称同步更新。名称不因切换语言、读取验证或升级而改变；历史日志和操作请求保留当时的名称快照。改名仅影响本机显示，不修改平台账户或权限。

New connections default to `Trading 212`. Click **Edit name** on the account page to choose your own label, such as `Trading 212 ISA`. The sidebar, account pickers and current assignment labels update together. Language switches, verification and upgrades do not overwrite names; historical logs and actions retain their original name snapshots. Renaming affects local display only, not the platform account or permissions.

当前官方账户概览接口没有账户显示名称或 ISA／Invest 类型字段，因此不自动推断账户类型。此处的示例名称由用户填写。

The current official account summary schema does not provide a display name or ISA/Invest account type, so the app does not infer account types. Example names are user-supplied labels.

在侧栏“设置 / Settings”或按 **⌘,** 选择应用语言，立即生效并保存到本机。首次默认中文；系统菜单和钥匙串提示沿用 macOS 语言。语言扩展说明见 [LOCALIZATION.md](LOCALIZATION.md)。

Choose the app language under **Settings** in the sidebar or press **⌘,**. Changes apply immediately and persist locally. Chinese is the initial default; system menus and Keychain prompts follow macOS. See [LOCALIZATION.md](LOCALIZATION.md) to add languages.

## 权限与交易确认 / Permissions and trade approval

实际交易需要同时满足 **212 API 授权、账户权限开关、客户端权限开关**。即使 API Key 权限全开，工具也默认只读。下单和撤单分别手动开启；后端在创建请求和提交时重新检查权限。仅开启开关不会发送订单。

Trading requires **212 API authorization, account permission and client permission** together. The tool remains read-only by default even if the API key has full permissions. Enable placement and cancellation separately; the backend rechecks permission when creating and submitting requests. Enabling a switch does not send an order.

AI 只能创建待确认请求。用户在“操作确认 / Approvals”核对来源、账户、环境、方向、数量、价格和有效期，再逐笔确认。请求 10 分钟后过期。断开客户端、改变权限、暂停或删除连接会撤销相关待确认请求。新客户端和改绑账户后的客户端恢复默认只读；暂停或恢复连接也会关闭额外交易权限。

AI clients can only create pending requests. The user reviews source, account, environment, side, quantity, prices and validity under **Approvals**, then confirms each action. Requests expire after 10 minutes. Disconnecting clients, changing permissions, pausing or deleting connections revokes related pending requests. New clients and changed account assignments default to read-only; pausing or resuming a connection also disables extra trading permissions.

订单 API 不保证幂等性。本工具使用唯一请求 ID 并提前标记提交状态以防重复提交。网络超时、服务端错误或进程异常后的未知结果需到 212 核对，程序不会自动重试交易。`accepted` 不代表成交，也不保证撤单成功。交易测试使用模拟响应，没有向实盘发送测试订单或撤单。

The order API does not guarantee idempotency. The tool uses unique request IDs and records submission state before sending to prevent duplicates. Check uncertain results from timeouts, server errors or process failures in 212; the tool does not retry trades automatically. `accepted` does not mean filled or guarantee cancellation. Trade tests use mocked responses; no test orders or cancellations have been sent to live accounts.

## API 使用记录 / API usage

显示本工具最近 24 小时的请求数、成功率、错误和限流次数、按来源统计、最近 100 次调用和最新观测到的限流头。额度按 212 账户共享，其他客户端、API Key 和应用也可能占用额度。过期观测不作为当前余额度；历史无来源标签的请求归入“旧版 / 未标记”。

Shows this tool's last 24 hours of request counts, success rates, errors, rate limit events, usage by source, the last 100 calls and most recently observed rate limit headers. Quotas are shared per 212 account; other clients, keys and applications may use them too. Expired observations are not current quotas; older requests without a source are labelled **Legacy / Unlabelled**.

此记录只覆盖本工具，不是 212 所有应用的完整用量。日志记录接口、方法、状态码和耗时，不记录密钥、余额或持仓响应。旧版独立只读服务的历史未迁入。

Records cover this tool, not complete 212 usage across every app. Logs record endpoints, methods, status codes and durations, not credentials, balances or position responses. Historical calls from the older standalone read-only service were not migrated.

## 数据与移除 / Data and removal

| 中文 | English |
|---|---|
| 凭证保存到 macOS 钥匙串服务 `codex.trading212.connection`，按连接 UUID 区分；旧连接可能引用 `codex.trading212.readonly` | Credentials use macOS Keychain service `codex.trading212.connection` per connection UUID; legacy connections may reference `codex.trading212.readonly` |
| 本机数据库位于 `~/Library/Application Support/Trading212 Connection/connections.sqlite3`；目录权限 700，数据库权限 600，未额外加密 | Local database: `~/Library/Application Support/Trading212 Connection/connections.sqlite3`; directory mode 700, database mode 600, no additional encryption |
| 待确认交易内容和结果是私有金融数据，仅保存到本机 | Pending action details and results are private financial data stored locally |
| MCP 使用本机 STDIO，不开放网页端口；客户端配置不包含 212 密钥 | MCP uses local STDIO with no web port; client configuration contains no 212 credentials |

Claude Desktop 配置修改只涉及本工具的 MCP 条目，保留其他配置，写入前创建权限受限的备份。配置无效或存在其他程序的同名连接时不覆盖。管理后端仅接受原生应用调用；MCP 不开放权限修改、凭证读写或提交确认工具。这是本机功能边界，不是针对同一 macOS 用户任意代码的沙箱，也不是对客户端名称的密码学认证。

Claude Desktop changes affect only this tool's MCP entry, preserve other configuration and create a restricted backup before writing. Invalid configuration or conflicting entries from other programs are not overwritten. The management backend accepts only native app calls; MCP exposes no permission editing, credential access or approval tools. This is a local feature boundary, not a sandbox against arbitrary code running as the same macOS user or cryptographic authentication of client names.

“断开”立即拒绝该客户端后续访问，但不会删除账户、密钥或已经提交的平台订单。删除本机连接会删除其钥匙串凭证，不会撤销平台 API Key。完全移除时先在窗口断开客户端、删除连接，再移除插件和应用；平台密钥和数据库审计记录需分别处理。

**Disconnect** immediately blocks that client's subsequent access, but does not delete accounts, credentials or submitted platform orders. Deleting a local connection removes its Keychain credentials but does not revoke the platform API key. To remove the tool fully, disconnect clients and delete connections in the app before removing the plugin and application; handle platform keys and database audit records separately.

## 开发与测试 / Development and tests

```sh
python3 validate_localization.py
python3 -m unittest discover -s tests -p 'test*.py' -v
```

测试使用临时数据库、虚拟钥匙串和模拟网络；覆盖默认只读、客户端账户隔离、交易确认、重复请求、未知结果、改名和语言回退。Swift 本地化运行时检查需要 macOS 上的 Swift 编译器。详见 [CONTRIBUTING.md](CONTRIBUTING.md)、[SECURITY.md](SECURITY.md) 和 [CHANGELOG.md](CHANGELOG.md)。

Tests use temporary databases, fake Keychain storage and mocked networking. They cover read-only defaults, client isolation, trade approval, duplicate requests, uncertain results, naming and language fallback. Swift localization runtime checks require a Swift compiler on macOS. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [CHANGELOG.md](CHANGELOG.md).

## GitHub 新手指南 / GitHub beginner guide

仓库主页展示此 README。“Code”菜单可下载 ZIP；下载后解压并按上述步骤构建。用“Issues”反馈问题，提供版本和重现步骤，不要贴 API 凭证或账户截图。未来修改源码后，可通过分支和 Pull Request 提交变化；版本记录见 CHANGELOG。

The repository home displays this README. Use **Code → Download ZIP** to download a source archive, extract it, then follow the build steps above. Report problems in **Issues** with a version and reproduction steps, without API credentials or account screenshots. Future code changes can be submitted through branches and pull requests; see the CHANGELOG for version history.

## 资料与许可 / References and licensing

- [Trading 212 官方 OpenAPI / Official OpenAPI](https://docs.trading212.com/_bundle/api.json?download)
- [Trading 212 API 文档 / API documentation](https://docs.trading212.com/)
- [Claude Code 官方说明 / Official documentation](https://code.claude.com/docs/en/overview)

本仓库尚未指定开源许可证；公开源码不等于授予开源许可。面向公众分发已构建应用还需准备签名、公证和发布流程。

No open-source license has been selected for this repository; public source visibility does not grant an open-source license. Public distribution of built applications still requires appropriate signing, notarization and release preparation.
