# 安全与私有数据 / Security and private data

不要在公开 Issue、Pull Request、讨论或提交中粘贴 API Key、API Secret、账户响应、交易请求详情或本机数据库。如果误公开凭证，请在 Trading 212 撤销并重新生成；删除 GitHub 文件无法保证清除已有副本。

Do not paste API keys, API secrets, account responses, trade request details or local databases into public issues, pull requests, discussions or commits. If credentials are exposed, revoke and replace them in Trading 212; deleting a GitHub file does not guarantee removal of existing copies.

凭证由用户在原生窗口输入并保存到 macOS 钥匙串。本机数据库含账户元数据、调用记录和操作请求，使用目录与文件权限保护，未额外加密。MCP 配置只包含后端路径和客户端标识。

Users enter credentials in the native window, which stores them in macOS Keychain. The local database contains account metadata, call records and action requests; directory/file permissions protect it, with no additional encryption. MCP configuration contains only the backend path and client identifier.

账户及客户端默认只读。交易需要平台、账户和客户端权限，并由用户逐笔确认。结果未知时先到 212 核对，不自动重试。该边界不防护同一 macOS 用户运行的任意本机代码。

Accounts and clients default to read-only. Trading requires platform, account and client permissions plus individual user confirmation. Check unknown results in 212 instead of retrying automatically. This boundary does not protect against arbitrary local code running as the same macOS user.

报告问题前先移除私有信息。涉及可利用安全漏洞时，不在公开 Issue 提供可直接利用的细节；先通过仓库维护者公开的私下联系方式沟通。本项目未配置专用安全邮箱。

Remove private information before reporting a problem. For exploitable vulnerabilities, avoid publishing directly exploitable details in public issues; contact the maintainer through a privately usable contact they have made available first. This project has no dedicated security email address.
