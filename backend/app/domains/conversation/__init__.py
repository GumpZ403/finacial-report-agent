"""conversation 领域：对话编排（单 agent 入口）

- routing.py  意图分类节点（一次 LLM 调用产出 intent + task_type + entities）
- graph.py    主图：classify → 条件边 → simple / react / plan / chat
- react.py    ReAct 循环（标准 function calling 协议）

纪律：本领域不直接查库，全部通过 harness.registry 里注册的工具。
"""
