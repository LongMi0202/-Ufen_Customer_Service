from pydantic import BaseModel, Field


class KnowledgeItem(BaseModel):
    """定义知识查询返回的一条可信知识内容。"""

    knowledge_id: str = Field(min_length=1,description="知识内容的稳定编号")
    title: str = Field(min_length=1,description="知识内容标题")
    content: str = Field(min_length=1,description="Agent 可以用于回答的知识正文")
    source: str = Field(min_length=1,description="知识内容的来源说明")
    url: str | None = None
    tenant_id: str
    welfare_project_id: str
    version: str
    source_type: str
    reference_only: bool
    effective_from: str | None = None
    effective_to: str | None = None
