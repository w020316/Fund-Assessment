# 单 worker（2026-10-26 修复）
# 本应用的模拟持仓/风控状态/限流计数/内存缓存全部在**进程内**：
#   SimulatedBroker._cash/_positions、RiskManager 内存字段、
#   slowapi 计数器、DataCache/_MemoryLayer。
# 起 2 个 worker 会让这些状态各持一份 → 用户连续两次请求可能落到不同 worker，
# 表现为"持仓凭空消失、限流额度翻倍、风控状态错乱"。
# 若将来要真多 worker，必须先把这些状态外置（SQLite 已可用于 broker/风控），
# 否则不要改回 >1。
web: uvicorn web.api:app --host 0.0.0.0 --port $PORT --workers 1
