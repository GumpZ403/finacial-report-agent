"""interfaces：对外接口层（HTTP / 未来 CLI、MCP server）

纪律：只做协议适配（请求解析、SSE 编码、错误映射），不写业务逻辑；
业务一律调用 app.domains.* 与 app.harness.*。
"""
