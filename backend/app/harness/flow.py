"""轻量流程执行器（自研，替代 LangGraph）

为什么自研
----------
本项目只有 4 条固定路径 + 一个报告 DAG。LangGraph 的 checkpoint / 人在环 /
可视化收益有限，而它 `stream_mode="values"` 每步重发整个 state，导致 SSE
stage 事件没有信息量（前端只能显示"正在处理（simple）…"）。真正需要的是三件事：

1. 显式图（节点 + 边 + 路由器）——保持 LangGraph 的心智模型，迁移成本低；
2. 逐节点事件（真流式）——每个节点开始/结束都能推给前端；
3. 每节点 checkpoint 钩子——后续把 runs 表接上，实现断点续跑。

契约
----
节点            fn(state: dict) -> dict | None
                返回"增量"，浅合并进 state；返回 None / 空 dict 表示无改动。
路由器          fn(state: dict) -> str
                返回分支键；若 add_router 给了 mapping，按 mapping 映射到节点名。
                返回 END 结束流程。
子流程          直接 `add_node("react", build_react_flow())`：Flow 可调用。
                子流程的事件会自动冒泡到父流程的 on_event（靠 contextvars）。
扇出            `add_fanout(name, fn, split, join)`：split 切分支 → fn 并行 →
                join 合并。**分支异常被捕获并交给 join（status=failed），不中断整图**
                —— 报告章节写作需要"某章失败不影响其他章"。

线程模型
--------
同步执行。LLM 客户端是同步 SDK，FastAPI 的 `def` 端点跑在线程池里，不堵事件循环；
扇出用线程池并行，并显式复制 contextvars，让分支内的事件也能冒泡。

已知限制
--------
扇出分支内若再挂子流程，其事件在分支线程中产生，冒泡依赖 contextvars 复制（已处理）；
但事件顺序不保证与分支完成顺序一致，前端不应依赖顺序。
"""
from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

# 结束哨兵（与 LangGraph 的 END 语义一致，保留同名便于迁移识别）
END = "__end__"

# 扇出并行上限：章节数量通常 3~8，不宜开太多（LLM 客户端有连接池）
DEFAULT_MAX_WORKERS = 8

# 单次执行的默认步数上限（防路由写错导致死循环）
DEFAULT_MAX_STEPS = 50

NodeFn = Callable[[dict], "dict | None"]
RouterFn = Callable[[dict], str]
SplitFn = Callable[[dict], list[dict]]
JoinFn = Callable[[dict, list[dict]], "dict | None"]
EventFn = Callable[["FlowEvent"], None]


class FlowError(RuntimeError):
    """流程配置/执行错误：未知节点、未设入口、路由指向非法节点、超过步数上限"""


@dataclass
class FlowEvent:
    """流程事件（一条 SSE 消息的来源）

    kind:
        start       流程开始
        node_start  节点开始（前端用它更新"正在做什么"）
        node_end    节点结束（带 next：走到了哪个节点）
        branch      扇出分支完成/失败
        done        流程结束（state 为最终状态）
        error       节点抛异常
    """

    kind: str
    flow: str = ""
    node: str = ""
    next: str = ""
    branch: str = ""
    step: int = 0
    elapsed_ms: int = 0
    error: str | None = None
    state: dict = field(default_factory=dict)


# 当前事件的接收者：用 contextvars 传递，节点签名保持 fn(state) 不变，
# 也不必把 on_event 一路手工透传到每个函数。
_emitter: contextvars.ContextVar[EventFn | None] = contextvars.ContextVar(
    "flow_emitter", default=None
)


def _emit(event: FlowEvent) -> None:
    """派发事件。回调本身出错绝不能影响主流程。"""
    cb = _emitter.get()
    if cb is None:
        return
    try:
        cb(event)
    except Exception:  # pragma: no cover - 事件通道故障时只记日志
        logger.exception("[flow] 事件回调异常（已忽略）")


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


class _Fanout:
    """扇出节点：split → 并行执行 fn → join"""

    def __init__(
        self,
        name: str,
        flow: str,
        fn: NodeFn,
        split: SplitFn,
        join: JoinFn,
        max_workers: int | None = None,
    ) -> None:
        self.name = name
        self.flow = flow
        self.fn = fn
        self.split = split
        self.join = join
        self.max_workers = max_workers

    def __call__(self, state: dict) -> dict:
        items = list(self.split(state) or [])
        if not items:
            logger.warning(f"[flow:{self.flow}] 扇出节点 {self.name} 无分支输入")
            return self.join(state, []) or {}

        workers = min(len(items), self.max_workers or DEFAULT_MAX_WORKERS)
        logger.info(f"[flow:{self.flow}] 扇出 {self.name}：{len(items)} 个分支，并发 {workers}")

        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=self.name) as pool:
            futures = {}
            for idx, item in enumerate(items):
                key = str(item.get("key") or f"#{idx + 1}")
                overlay = dict(item.get("state") or {})
                branch_state = {**state, **overlay, "_branch": key}
                # 每个任务独立复制一份 context，让分支内的 _emit 也能冒泡到父流程
                ctx = contextvars.copy_context()
                futures[pool.submit(ctx.run, self.fn, branch_state)] = key

            for future in as_completed(futures):
                key = futures[future]
                try:
                    delta = future.result() or {}
                    results.append({"key": key, "status": "ok", "delta": delta})
                    _emit(FlowEvent(kind="branch", flow=self.flow, node=self.name, branch=key))
                except Exception as e:
                    # 单分支失败不中断整图：状态交给 join 决定如何降级
                    logger.exception(f"[flow:{self.flow}] 扇出分支 {key} 失败")
                    results.append({"key": key, "status": "failed", "delta": {},
                                    "error": str(e)[:300]})
                    _emit(FlowEvent(kind="branch", flow=self.flow, node=self.name,
                                    branch=key, error=str(e)[:300]))

        results.sort(key=lambda r: r["key"])
        return self.join(state, results) or {}


class Flow:
    """图的构建与执行（同步）"""

    def __init__(self, name: str = "flow") -> None:
        self.name = name
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str] = {}
        self._routers: dict[str, tuple[RouterFn, dict[str, str] | None]] = {}
        self._entry: str | None = None

    # ------------------------------------------------------------------ 构图
    def add_node(self, name: str, fn: NodeFn) -> "Flow":
        if name in self._nodes:
            raise FlowError(f"[{self.name}] 节点重复注册：{name!r}")
        self._nodes[name] = fn
        return self

    def add_nodes(self, **nodes: NodeFn) -> "Flow":
        for name, fn in nodes.items():
            self.add_node(name, fn)
        return self

    def set_entry(self, name: str) -> "Flow":
        if name not in self._nodes:
            raise FlowError(f"[{self.name}] 入口节点未注册：{name!r}")
        self._entry = name
        return self

    def add_edge(self, src: str, dst: str) -> "Flow":
        self._edges[src] = dst
        return self

    def add_router(
        self,
        src: str,
        fn: RouterFn,
        mapping: dict[str, str] | None = None,
    ) -> "Flow":
        """条件边。fn 返回分支键，mapping 把分支键翻译成节点名（可指向 END）。"""
        self._routers[src] = (fn, dict(mapping) if mapping else None)
        return self

    def add_fanout(
        self,
        name: str,
        fn: NodeFn,
        split: SplitFn,
        join: JoinFn,
        max_workers: int | None = None,
    ) -> "Flow":
        """扇出节点。

        split(state) -> [{"key": 分支名, "state": 该分支的输入覆盖项}, ...]
        fn(branch_state) -> 分支产出（增量）
        join(state, [{"key","status","delta","error"?}]) -> 汇总增量
        """
        return self.add_node(
            name, _Fanout(name, self.name, fn, split, join, max_workers)
        )

    # ------------------------------------------------------------------ 执行
    def __call__(self, state: dict) -> dict:
        """把本流程当作父流程的一个节点使用（返回最终 state，浅合并幂等）"""
        return self.run(dict(state))

    def run(
        self,
        state: dict | None = None,
        *,
        on_event: EventFn | None = None,
        checkpoint: Callable[[str, dict], None] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
    ) -> dict:
        """执行流程，返回最终状态。

        on_event  逐节点事件回调（None 时继承外层流程的回调，实现子流程冒泡）
        checkpoint(node, state) 每节点结束后的落盘钩子；抛异常不影响流程
        """
        if self._entry is None:
            raise FlowError(f"[{self.name}] 未设置入口节点")

        state = dict(state or {})
        token = _emitter.set(on_event) if on_event is not None else None
        try:
            _emit(FlowEvent(kind="start", flow=self.name, state=dict(state)))
            current = self._entry
            step = 0

            while current != END:
                if step >= max_steps:
                    raise FlowError(
                        f"[{self.name}] 超过最大步数 {max_steps}，疑似死循环"
                        f"（当前节点 {current!r}）"
                    )
                step += 1
                node_fn = self._nodes.get(current)
                if node_fn is None:
                    raise FlowError(f"[{self.name}] 未知节点：{current!r}")

                _emit(FlowEvent(kind="node_start", flow=self.name, node=current,
                                step=step, state=dict(state)))
                t0 = time.perf_counter()
                try:
                    delta = node_fn(state)
                except Exception as e:
                    _emit(FlowEvent(kind="error", flow=self.name, node=current, step=step,
                                    elapsed_ms=_ms(t0), error=str(e)[:300]))
                    raise

                if delta:
                    state.update(delta)
                nxt = self._next(current, state)
                elapsed = _ms(t0)
                logger.debug(f"[flow:{self.name}] {current} → {nxt} ({elapsed}ms)")
                _emit(FlowEvent(kind="node_end", flow=self.name, node=current, next=nxt,
                                step=step, elapsed_ms=elapsed, state=dict(state)))

                if checkpoint is not None:
                    try:
                        checkpoint(current, dict(state))
                    except Exception:
                        logger.exception("[flow] checkpoint 失败（已忽略，不影响流程）")

                current = nxt

            _emit(FlowEvent(kind="done", flow=self.name, step=step, state=dict(state)))
            return state
        finally:
            if token is not None:
                _emitter.reset(token)

    # ------------------------------------------------------------------ 内部
    def _next(self, node: str, state: dict) -> str:
        """计算后继节点：优先路由器，否则静态边，缺省 END"""
        router = self._routers.get(node)
        if router is not None:
            fn, mapping = router
            key = fn(state)
            if not isinstance(key, str):
                raise FlowError(
                    f"[{self.name}] 路由器 {node!r} 必须返回字符串，得到 {type(key).__name__}"
                )
            target = mapping.get(key, key) if mapping else key
        else:
            target = self._edges.get(node, END)

        if target != END and target not in self._nodes:
            raise FlowError(
                f"[{self.name}] 节点 {node!r} 指向未注册的节点 {target!r}"
                f"（已注册：{sorted(self._nodes)}）"
            )
        return target

    # ------------------------------------------------------------------ 自省
    @property
    def node_names(self) -> list[str]:
        return list(self._nodes)

    def describe(self) -> dict[str, Any]:
        """用于 /api 调试与文档：一眼看清图结构"""
        return {
            "name": self.name,
            "entry": self._entry,
            "nodes": {
                name: {
                    "edges": self._edges.get(name) or (
                        "router" if name in self._routers else END
                    ),
                }
                for name in self._nodes
            },
        }
