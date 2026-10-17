"""
LLM 统一客户端封装
- 兼容 OpenAI 接口协议（DeepSeek、通义千问、本地 vLLM 等）
- 支持重试、超时、流式/非流式
- 所有 Agent / RAG 模块通过本类调用大模型
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger
from openai import OpenAI, APIError, RateLimitError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.platform.settings import get_settings


class LLMClient:
    """大模型统一调用客户端"""

    def __init__(self):
        self.settings = get_settings()
        self._client = OpenAI(
            api_key=self.settings.LLM_API_KEY,
            base_url=self.settings.LLM_BASE_URL,
            timeout=60,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, APIError)),
        reraise=True,
    )
    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str:
        """
        非流式对话

        Args:
            messages: [{"role": "system/user/assistant", "content": "..."}]
            temperature: 不传则用全局配置
            max_tokens: 不传则用全局配置
            response_format: 如 {"type": "json_object"} 强制JSON输出

        Returns:
            模型返回的文本内容
        """
        kwargs: dict[str, Any] = {
            "model": self.settings.LLM_MODEL,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.settings.LLM_TEMPERATURE,
            "max_tokens": max_tokens or self.settings.LLM_MAX_TOKENS,
        }
        if response_format:
            kwargs["response_format"] = response_format

        resp = self._client.chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        logger.debug(f"LLM response ({len(content)} chars): {content[:200]}...")
        return content

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
    ) -> dict:
        """
        强制 JSON 输出并解析

        Returns:
            解析后的 dict；解析失败返回 {"raw": 原始文本, "error": "..."}
        """
        text = self.chat(
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"LLM JSON parse failed: {e}; raw={text[:300]}")
            return {"raw": text, "error": str(e)}

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> dict:
        """带 function calling 的对话判断（OpenAI 兼容协议）。

        Args:
            messages: 对话消息
            tools: 注册的工具 schema 列表
            temperature: 采样温度

        Returns:
            {"tool_calls": [{"id": str, "type": str, "function": {"name": str, "arguments": str}}], "content": str}
            - tool_calls 非空：LLM 决定调用某个工具（执行动作），结构为 OpenAI 标准协议
              （id/type/function 完整保留，arguments 为 JSON 字符串，可直接回填消息轨迹）
            - content 非空：LLM 直接文字回答（闲聊/解释）
        """
        kwargs: dict[str, Any] = {
            "model": self.settings.LLM_MODEL,
            "messages": messages,
            "temperature": temperature or self.settings.LLM_TEMPERATURE,
            "max_tokens": self.settings.LLM_MAX_TOKENS,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        tool_calls: list[dict] = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id or "",
                    "type": getattr(tc, "type", None) or "function",
                    "function": {
                        "name": tc.function.name or "",
                        "arguments": tc.function.arguments or "{}",
                    },
                })

        names = [c["function"]["name"] for c in tool_calls]
        logger.info(f"[chat_with_tools] tool_calls={names} content_len={len(msg.content or '')}")
        return {"tool_calls": tool_calls, "content": msg.content or ""}

# 说明：原先的 chat_stream() 已删除——声明为 AsyncIterator 实为同步生成器，
# 且全仓库零调用（SSE 的流是 LangGraph 的 stage 事件流，不是 token 流）。
# 若要做真正的 token 级流式，应在此实现 async def stream_chat() 并配合 astream。


# 单例
_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    """获取 LLM 客户端单例"""
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
