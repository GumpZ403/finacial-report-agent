"""
基础测试：验证各模块可正常导入和初始化
运行：pytest tests/ -v
"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestCoreImports:
    """测试核心模块导入"""

    def test_config_import(self):
        from app.core.config import get_settings
        settings = get_settings()
        assert settings is not None
        assert settings.LLM_MODEL is not None

    def test_llm_client_import(self):
        from app.core.llm_client import LLMClient
        assert LLMClient is not None

    def test_embedding_import(self):
        from app.core.embedding import EmbeddingService
        assert EmbeddingService is not None


class TestDataLayer:
    """测试数据层（app/data）"""

    def test_repository_import(self):
        from app.data.repository import Database, get_db
        assert Database is not None and get_db is not None

    def test_schema_import(self):
        from app.data.schema import (
            CompanyMaster, FinancialPeriod, FinancialMetric,
            FinancialCore, MetricDict,
        )
        assert all([CompanyMaster, FinancialPeriod, FinancialMetric,
                    FinancialCore, MetricDict])

    def test_datasource_import(self):
        from app.data.datasource import get_adapter
        assert get_adapter is not None


class TestRAGLayer:
    """测试 RAG 层（agents/rag_agent）"""

    def test_retriever_import(self):
        from app.tools.rag.retriever import HybridRetriever
        assert HybridRetriever is not None

    def test_query_rewrite_import(self):
        from app.tools.rag.query_rewrite import QueryRewriter
        assert QueryRewriter is not None

    def test_generator_import(self):
        from app.tools.rag.rag_agent import RAGGenerator
        assert RAGGenerator is not None


class TestAgentLayer:
    """测试 Agent 层"""

    def test_intent_import(self):
        from app.agent.intent import IntentRecognizer
        recognizer = IntentRecognizer()
        # 测试规则匹配
        result = recognizer._rule_based_match("帮我生成一份周报")
        assert result["intent"] == "report"

        result = recognizer._rule_based_match("导入这个Excel文件")
        assert result["intent"] == "data_import"

    def test_memory_import(self):
        from app.core.memory import Memory
        memory = Memory()
        memory.add("user", "测试问题")
        memory.add("assistant", "测试回答")
        assert len(memory.short_term) == 2

    def test_orchestrator_import(self):
        from app.tools.report.workflow import WorkflowEngine
        engine = WorkflowEngine("test")
        assert engine.name == "test"

    def test_workflow_execution(self):
        """测试简单工作流执行"""
        from app.tools.report.workflow import WorkflowEngine

        engine = WorkflowEngine("test-workflow")
        calls = []

        def step1(ctx):
            calls.append("step1")
            ctx.data["value"] = 1
            return {"result": 1}

        def step2(ctx):
            calls.append("step2")
            ctx.data["value"] += 1
            return {"result": ctx.data["value"]}

        engine.add_node("step1", step1, "第一步")
        engine.add_node("step2", step2, "第二步")
        engine.add_edge("step1", "step2")

        result = engine.run()
        assert result["status"] == "success"
        assert calls == ["step1", "step2"]
        assert result["context"]["value"] == 2

    def test_agents_import(self):
        from app.tools.report.roles.retrieval_agent import RetrievalAgent
        from app.tools.report.roles.analysis_agent import AnalysisAgent
        from app.tools.report.roles.writer_agent import WriterAgent
        from app.tools.report.roles.reviewer_agent import ReviewerAgent
        assert all([RetrievalAgent, AnalysisAgent, WriterAgent, ReviewerAgent])

    def test_tools_import(self):
        from app.tools.report.tools.search_tool import SearchTool
        from app.tools.report.tools.write_tool import WriteTool
        from app.tools.report.tools.compliance_tool import ComplianceTool
        assert all([SearchTool, WriteTool, ComplianceTool])


class TestCompliance:
    """测试合规工具（tools/report）"""

    def test_sensitive_word_detection(self):
        from app.tools.report.tools.compliance_tool import ComplianceTool
        tool = ComplianceTool()
        hits = tool.check_sensitive_words("这篇文章涉及暴力内容")
        assert "暴力" in hits
        hits = tool.check_sensitive_words("这是一篇正常的新闻报道")
        assert len(hits) == 0
