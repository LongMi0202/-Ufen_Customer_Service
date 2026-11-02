"""Verify entity/field-bound point claims and retrieved policy references."""
from decimal import Decimal, InvalidOperation
import re
from atguigu.agent.harness.errors import AgentOutputValidationError, CorrectableErrorCode


def fail(message):
    raise AgentOutputValidationError(CorrectableErrorCode.UNSUPPORTED_FACT,message)


def entities(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from entities(child)
    elif isinstance(value,list):
        for child in value:yield from entities(child)


def scalar_text(value):
    if isinstance(value,bool):return str(value).lower()
    return str(value)


def read_field(value, path):
    """Read a dotted field or nonnegative JSON array index; never eval a model expression."""
    if not re.fullmatch(r"[^\W\d]\w*(?:\[\d+\])?(?:\.[^\W\d]\w*(?:\[\d+\])?)*", path):
        raise KeyError(path)
    for key, index in re.findall(r"([^\W\d]\w*)|\[(\d+)\]", path):
        value = value[key] if key else value[int(index)]
    return value


def validate_evidence(output, calls):
    successful=[c for c in calls if c.success is True and c.result]
    evidence={c.result.get("evidence_id"):c for c in successful}
    point_values=set()
    allowed_point_fields={"available_points","ledger_balance","expiring_points","expired_unsettled_points",
        "points_price","total_points","required_points","expected_points","returned_points","expired_points","usable_returned_points","delta","points","remaining","granted"}
    for fact in output.business_facts:
        call=evidence.get(fact.tool_call_id)
        if call is None or call.tool_name=="search_knowledge":fail("事实引用不存在或不是成功业务工具")
        matched=False
        for obj in entities(call.result.get("data")):
            if fact.entity_id not in {str(obj[k]) for k in ("id","account_id","order_id","product_id","batch_id") if k in obj}:continue
            value=obj
            try:
                value=read_field(value,fact.field_path)
            except (KeyError,TypeError,IndexError,ValueError):continue
            if isinstance(value,(dict,list)) or scalar_text(value)!=fact.value:continue
            matched=True
            break
        if not matched:
            hint = ""
            if call.tool_name == "list_points_transactions":
                hint = "；流水工具返回列表：entity_id使用流水自己的id，field_path直接填delta、kind等标量字段，不是transactions[0].delta"
            elif fact.field_path == "specs":
                hint = "；specs是对象，应引用specs.续航等实际标量子字段，不能引用整个对象"
            fail(f"实体或字段证据不匹配：{fact.entity_id}.{fact.field_path}{hint}")
        if fact.unit=="points":
            if fact.field_path.split(".")[-1] not in allowed_point_fields:fail("积分单位不能引用价格、库存或其他数值字段")
            try:point_values.add(Decimal(fact.value))
            except InvalidOperation:fail("积分字段值无效")

    knowledge={d["knowledge_id"]:d for c in successful if c.tool_name=="search_knowledge"
               for d in (c.result.get("data") or [])}
    for key in output.knowledge_references:
        if key not in knowledge:fail("政策引用不在本次成功检索结果中")
    if knowledge and output.reply_type=="ANSWER" and not output.knowledge_references:
        fail("规则解释必须提供 knowledge_references")
    cited=[knowledge[key] for key in dict.fromkeys(output.knowledge_references)]
    policy_text=" ".join(d["content"] for d in cited)
    point_mentions = re.findall(r"(-?\d[\d,]*(?:\.\d+)?)\s*(?:个积分|积分|分(?!钟|秒))", output.reply_content)
    point_mentions += re.findall(r"(?:可用积分|积分余额|临期积分|已退积分|应退积分|所需积分)\s*(?:为|是|：|:)?\s*(-?\d[\d,]*(?:\.\d+)?)", output.reply_content)
    for raw in point_mentions:
        number=Decimal(raw.replace(",",""))
        if number not in point_values and not any(Decimal(x.replace(",",""))==number for x in re.findall(r"(-?\d[\d,]*(?:\.\d+)?)\s*(?:个积分|积分)",policy_text)):
            fail(f"回答中的{raw}积分缺少实体字段证据或政策引用")
    return [{"knowledge_id":d["knowledge_id"],"title":d["title"],"source":d["source"],"url":d.get("url"),
             "version":d["version"],"source_type":d["source_type"],"reference_only":d["reference_only"]} for d in cited]
