---
name: trading212-connection
description: "使用Trading212 Connection连接212账户与Codex或Claude，管理连接、权限、用量及确认请求。 / Connect Trading 212 to Codex or Claude; manage connections, permissions, usage and approval requests through the local hub."
---

# Trading212 Connection

中文说明如下，英文说明见下方 English instructions。 / Chinese instructions follow; the English equivalent appears below.

使用管理窗口注册的 `trading212-connection` MCP 服务。固定管理窗口和 skill 都以 **Trading212 Connection** 展示。插件提供固定skill；MCP由窗口的“AI客户端”页面统一连接、断开和选择账户。

## 连接与窗口

用户要添加、删除、暂停、切换或重命名连接、输入 API Key 或修改权限时，调用 `open_connection_manager`。用户直接在窗口粘贴 API Key 与 API Secret；不在聊天中收集或显示凭证。若 MCP 工具尚未加载，运行本 skill 的 [scripts/open_manager.py](scripts/open_manager.py)，并告知用户重启应用以加载插件工具。

用户要选择Codex、Claude Code或Claude Desktop时，打开窗口的“AI客户端”页。安装状态、配置状态和最近握手分开显示；“已配置”不能断言该AI客户端当前在线。每个客户端选择自己的212账户；连接/断开、账户绑定及权限变更由用户在原生窗口完成，不通过shell或数据库绕过。缺少Claude Code命令行时显示未安装，并提供官方安装说明，不自动安装它。

`list_connections` 和 `connection_status` 返回本机连接状态，不代表本次网络验证。窗口中的“测试连接”会读取账户和持仓并显示结果。不要重新配置已有有效密钥。

## 读取账户与用量

先确认本AI客户端绑定的连接及实盘/模拟盘环境。工具只能访问用户为该客户端分配的账户；`connectionId`不能越过此绑定。窗口当前账户只影响管理视图，不自动改变AI客户端绑定。

账户查询结果包含 `environment`、`source` 和 `fetchedAt`；报告中包含这些信息。历史订单、分红和流水按页返回，完整记录需沿 `nextCursor` 继续并保持筛选条件一致。账户价格是API快照，不应称为独立实时行情。

`get_api_usage` 统计当前AI客户端经本工具发出的最近24小时请求。原生窗口可以按来源查看各客户端和管理窗口的统计；升级前的请求标记为旧版/未标记。212额度按账户共享，来自响应头的最近一次观测，不能覆盖其他应用的完整记录。观测窗口过期时不要把旧剩余值当成当前额度。

## 用户请求交易操作时

本工具独立于212密钥权限，默认只读。交易同时需要212授权、本工具账户层开关和AI客户端开关；下单和撤单分别控制。新客户端及改变账户绑定后的客户端均从只读开始。不要通过shell、数据库或管理后端绕过窗口开启权限；用户提出“支持交易功能”不等于授权当前提交一笔交易。

用户明确要求具体下单/撤单且信息完整时，检查目标账户及相应权限，使用 `request_order` / `request_cancellation` 创建待确认请求，并使用唯一 `clientRequestId`（UUID）。订单数量为正数，`side` 指定买卖方向。交易类型支持 market、limit、stop、stop_limit；市价单可指定 extendedHours，其他类型可指定 timeValidity（DAY / GOOD_TILL_CANCEL）及必需价格。

创建请求不会提交订单。让用户到固定窗口“操作确认”逐笔确认；不要自行点击确认或代为提交。使用 `get_action_status` 查看结果。权限改变、连接暂停/删除及10分钟过期会撤销或终止尚未提交的请求。

`accepted` 仅表示212接受提交，不能断言已经成交或保证撤单成功。`unknown` / `submitting` 表示结果待核对，先查212订单或历史记录；不得自动重试或用新ID重复发送。相同 `clientRequestId` 只能用于同一账户的相同操作。

## English instructions / 英文说明

Use the `trading212-connection` MCP service registered by the management window. Both the window and skill are named **Trading212 Connection**. The plugin supplies the reusable skill; the app's **AI clients** page manages MCP connection, disconnection and account assignment.

### Connections and window / 连接与窗口

For adding, deleting, pausing, selecting or renaming connections, entering credentials or changing permissions, call `open_connection_manager`. Users paste API keys and secrets directly in the native window; never collect or display credentials in chat. If MCP tools are not loaded, run this skill's [scripts/open_manager.py](scripts/open_manager.py) and tell the user to restart the app to load tools.

To choose Codex, Claude Code or Claude Desktop, open **AI clients**. Installation, configuration and the last handshake are distinct; **Configured** does not prove the client is currently online. Users make connection, assignment and permission changes in the native window, without bypassing it through shell or database changes. If the Claude Code CLI is missing, show its installation instructions; do not install it automatically.

`list_connections` and `connection_status` describe local state, not a fresh network verification. **Test connection** reads account and position data and displays its result. Do not reconfigure valid credentials.

### Accounts and usage / 账户与用量

Confirm this AI client's assigned connection and Live/Paper environment first. `connectionId` cannot bypass the assigned account. The current window account affects the management view only, not client assignments.

Account responses include `environment`, `source` and `fetchedAt`; include them in reports. History, dividends and transactions are paginated; follow `nextCursor` while preserving filters for complete records. Account prices are API snapshots, not independent real-time quotes.

`get_api_usage` covers this AI client's requests through the tool in the last 24 hours. The native window groups usage by client and management window; older records may be labelled Legacy / Unlabelled. 212 quotas are shared per account and come from recently observed headers, not a complete cross-app usage ledger. Do not report expired remaining values as current quota.

### Requested trades / 交易请求

The tool is read-only by default independently of the 212 key's permissions. Trading requires 212 authorization plus account-level and client-level switches; placement and cancellation are separate. New clients and changed account assignments start read-only. Never bypass the native permission controls through shell, database or backend calls. Asking to support trading does not authorize submitting a particular trade.

When the user explicitly requests a specific order or cancellation and all information is present, check the target account and permissions, then use `request_order` / `request_cancellation` with a unique UUID `clientRequestId`. Quantity is positive and `side` chooses buy or sell. Types are market, limit, stop and stop_limit. Market orders can specify extendedHours; other types can specify timeValidity (DAY / GOOD_TILL_CANCEL) and required prices.

Creating a request does not submit an order. Direct the user to **Approvals** to approve each action; do not click confirmation or submit on their behalf. Read results with `get_action_status`. Permission changes, pausing/deleting connections and the 10-minute timeout revoke or terminate unsubmitted requests.

`accepted` means only that 212 accepted submission, not that the order filled or cancellation succeeded. `unknown` / `submitting` require checking 212 orders or history; do not retry automatically or send a duplicate under a new ID. A `clientRequestId` can be used only for the same action on the same account.
