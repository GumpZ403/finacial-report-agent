"""基础烟测：验证各层模块可正常导入、配置可解析

运行：cd backend && python -m pytest
（业务行为测试见 test_harness.py / test_api.py，下一步补齐）

历史说明：本文件此前混入了上一项目（舆情）遗留用例，引用 query_rewrite / rag_agent /
intent / tools.report.* 等已删除模块，导致 8 个用例恒失败（CI 假绿反面的“假红”）。
"""
import sys
from pathlib import Path

# 确保 backend/ 在 sys.path（pyproject 里也有 pythonpath=["."]，这里是双保险）
BACKEND_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_ROOT))


class TestCoreImports:
    """platform / harness 层"""

    def test_config_import(self):
        from app.platform.settings import get_settings
        settings = get_settings()
        assert settings is not None
        assert settings.LLM_MODEL
        # 相对路径必须按仓库根绝对化（与 cwd 无关）
        assert Path(settings.CHROMA_PERSIST_DIR).is_absolute()
        assert "sqlite:///" in settings.DATABASE_URL
        assert Path(settings.DATABASE_URL.replace("sqlite:///", "")).is_absolute()

    def test_llm_client_import(self):
        from app.harness.llm import LLMClient
        assert LLMClient is not None

    def test_embedding_import(self):
        from app.platform.embedding import EmbeddingService
        assert EmbeddingService is not None

    def test_memory(self):
        from app.harness.memory import Memory
        memory = Memory()
        memory.add("user", "测试问题")
        memory.add("assistant", "测试回答")
        assert len(memory.short_term) == 2
        memory.clear()
        assert len(memory.short_term) == 0


class TestDataLayer:
    """financial 领域"""

    def test_repository_import(self):
        from app.domains.financial.repository import Database, get_db
        assert Database is not None and get_db is not None

    def test_schema_import(self):
        from app.domains.financial.models import (
            CompanyMaster, FinancialPeriod, FinancialMetric,
            FinancialCore, MetricDict,
        )
        assert all([CompanyMaster, FinancialPeriod, FinancialMetric,
                    FinancialCore, MetricDict])

    def test_datasource_import(self):
        from app.domains.financial.ingest.akshare_em import get_adapter
        assert get_adapter is not None

    def test_scope_import(self):
        from app.domains.financial.industry_scope import is_applicable, normalize_template
        assert normalize_template("financial") == "financial"
        assert is_applicable is not None

    def test_formatting(self):
        from app.domains.financial.display import fmt_value
        assert fmt_value(1.234e9, "amount") == "12.34 亿元"
        assert fmt_value(0.1234, "ratio") == "12.34%"
        assert fmt_value(None, "ratio") == "N/A"


class TestRetrieval:
    """retrieval 领域"""

    def test_retriever_import(self):
        from app.domains.retrieval.hybrid import HybridRetriever
        assert HybridRetriever is not None

    def test_vector_import(self):
        from app.domains.retrieval.vector import VectorStore, get_vector_store
        assert VectorStore is not None and get_vector_store is not None

    def test_reranker_import(self):
        from app.domains.retrieval.reranker import RerankerTool
        assert RerankerTool is not None


class TestToolRegistration:
    """工具注册表：import 工具包即完成注册"""

    def test_tools_registered(self):
        import app.domains.financial.tools  # noqa: F401  触发注册
        from app.harness.registry import list_tool_names

        names = list_tool_names()
        for expected in ("query_company", "query_financials", "query_data_stats"):
            assert expected in names, f"工具 {expected} 未注册，实际：{names}"

    def test_rag_search_frozen(self):
        """rag_search 故意不注册（检索模块冻结，见 main.py 组合根注释）。

        这条测试是决策锁：谁想重新打开 rag_search，必须先删掉/改写这条测试，
        从而被迫读一遍冻结理由，而不是无声无息地把它加回来。
        """
        import app.domains.financial.tools  # noqa: F401
        from app.harness.registry import list_tool_names

        assert "rag_search" not in list_tool_names()


class TestConversationFlow:
    """对话编排流程可构建（不调用 LLM）"""

    def test_flow_builds(self):
        from app.domains.conversation.graph import get_agent_graph
        flow = get_agent_graph()
        assert flow.node_names == ["classify", "chat", "tool", "answer", "react", "plan"]
        assert flow.describe()["entry"] == "classify"

    def test_react_flow_builds(self):
        from app.domains.conversation.react import build_react_flow
        f = build_react_flow()
        assert set(f.node_names) == {"llm", "tool"}

    def test_report_flow_builds(self):
        from app.domains.report.graph import DEFAULT_PLAN, build_plan_flow
        f = build_plan_flow()
        assert set(f.node_names) == {"plan", "execute", "write"}
        # rag_search 撤下后计划只剩 3 步，且不再引用检索工具
        assert len(DEFAULT_PLAN) == 3
        assert all(step["action"] != "rag_search" for step in DEFAULT_PLAN)
