"""报告写作阶段的子代理（多 agent 协作）—— 待实现。

设计约定（下一步落地，先写死接口再写实现）：

每个子代理都是“纯函数式”的：只吃白名单输入，只吐结构化产物，不自由发挥。

    class ReportSectionAgent(Protocol):
        section: str                                  # 章节名（概览/异常/同行/风险…）
        def run(self, ctx: SectionContext) -> SectionDraft: ...

    @dataclass
    class SectionContext:
        report_id: str
        company: str
        allowed_numbers: list[NumberWhitelistItem]    # {metric_code, value, unit_type, period}
        allowed_texts: list[TextEvidence]             # {chunk_id, text, source}
        industry_template: str

    @dataclass
    class SectionDraft:
        section: str
        markdown: str                                 # 数字必须写成 {{m:report_id:metric_code}} 锚点
        used_chunk_ids: list[str]                     # 文本断言必须回指 chunk_id
        caveats: list[str]                            # 数据缺失 / 口径差异的显式声明

父流程职责（不放进子代理）：
1. 扇出：为每个章节构造 SectionContext（白名单必须由 SQL/pandas 预先算好，子代理不能算数）；
2. 扇入：合并 SectionDraft，交给 compliance 节点逐字符校验数字与措辞；
3. 失败重试：合规不通过 → 带具体违规点回退给对应子代理，最多 2 轮。
"""
