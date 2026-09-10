from typing import List, Literal
from pydantic import BaseModel, Field

class QueryAnalysis(BaseModel):
    is_clear: bool = Field(
        description="Indicates if the user's question is clear and answerable."
    )
    questions: List[str] = Field(
        description="List of rewritten, self-contained questions."
    )
    clarification_needed: str = Field(
        description="Explanation if the question is unclear."
    )


class CitationDraft(BaseModel):
    """LLM 声称的引用：evidence_id 必须是本次实际取得的证据 ID，quote 是证据原文精确切片。"""

    evidence_id: str = Field(description="生成时看到的证据块 ID（本次检索证据）")
    quote: str = Field(description="从证据原文逐字摘录的引用片段")


class GenerationOutput(BaseModel):
    """生成节点的结构化输出：单次 decision + 答案 + 候选引用。

    候选引用不直接发布：validate_result 校验 evidence_id 属于本次证据集、
    quote 是证据原文子串并推导 span 后，才映射为公开 Citation。
    """

    decision: Literal["answered", "clarification_required", "refused"] = Field(
        description="answered=有依据回答；clarification_required=问题歧义；refused=证据不足拒答"
    )
    answer: str = Field(default="", description="最终答案文本（refused 可为空）")
    clarification_question: str = Field(default="", description="decision=clarification_required 时的澄清问题")
    limitations: List[str] = Field(
        default_factory=list, description="有依据部分的明确缺口"
    )
    citations: List[CitationDraft] = Field(
        default_factory=list, description="answer 依据的证据引用（answered 时非空）"
    )
