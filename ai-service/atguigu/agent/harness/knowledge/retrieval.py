"""Local versioned policy retrieval; can later call an external RAG service."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import re
from atguigu.agent.harness.knowledge.output import KnowledgeItem

POLICY_FILE = Path(__file__).resolve().parents[5] / "knowledge/policies/policies.json"


class KnowledgeQueryService:
    def documents(self, tenant_id, project_id, *, include_references=True):
        documents=json.loads(POLICY_FILE.read_text(encoding="utf-8"))["documents"]
        today=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        return [d for d in documents if d["tenant_id"] in ("*",tenant_id)
            and d["welfare_project_id"] in ("*",project_id)
            and (not d.get("effective_from") or d["effective_from"] <= today)
            and (not d.get("effective_to") or today <= d["effective_to"])
            and (include_references or not d["reference_only"])]

    async def search(self, query: str, tenant_id: str, project_id: str) -> list[KnowledgeItem]:
        value=query.strip().lower()
        if not value:return []
        tokens=set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]",value))
        ranked=[]
        for doc in self.documents(tenant_id,project_id):
            if doc["reference_only"]:
                names={"REF_UFEN_FANUC":["发那科","bfm"],"REF_UFEN_LIBY":["立白"],
                       "REF_POSTAL":["浙江邮政"],"REF_GDIPU":["广东轻工","东方福利网"],
                       "REF_DD":["点滴关怀"],"REF_VMALL":["华为","vmall"]}
                if not any(name in value for name in names.get(doc["knowledge_id"],[])):continue
            score=sum(4 for k in doc.get("keywords",[]) if k.lower() in value)
            score+=len(tokens & set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]",doc["title"].lower()))) * .2
            if doc["welfare_project_id"]==project_id:score+=1
            if doc["reference_only"]:score+=10
            if score>=2:ranked.append((score,doc))
        ranked.sort(key=lambda item:(-item[0],item[1]["knowledge_id"]))
        return [KnowledgeItem.model_validate(d) for _,d in ranked[:4]]
