import asyncio
from types import SimpleNamespace
import unittest
from atguigu.agent.harness.knowledge.service import KnowledgeQueryService
from atguigu.agent.harness.validator.evidence import validate_evidence
from atguigu.agent.harness.errors import AgentOutputValidationError
from atguigu.agent.llm.output import AgentOutput, BusinessFactReference
from atguigu.agent.harness.validator.answer import AnswerValidator
from atguigu.agent.harness.validator.evidence import read_field


def call(data, name="get_points_account"):
    return SimpleNamespace(success=True,tool_name=name,arguments={},result={"evidence_id":"call_test","data":data})


def output(value="8000",entity="PA_A",field="available_points"):
    return AgentOutput(reply_type="ANSWER",reply_content=f"{value}积分",business_facts=[
        BusinessFactReference(tool_call_id="call_test",entity_id=entity,field_path=field,value=value,unit="points")])


class EvidenceTests(unittest.TestCase):
    def test_valid_account_evidence(self):
        self.assertEqual(validate_evidence(output(),[call({"account_id":"PA_A","available_points":8000})]),[])

    def test_same_number_wrong_entity_is_rejected(self):
        with self.assertRaises(AgentOutputValidationError):
            validate_evidence(output(entity="PA_B"),[call({"account_id":"PA_A","available_points":8000})])

    def test_same_number_wrong_unit_is_rejected(self):
        with self.assertRaises(AgentOutputValidationError):
            validate_evidence(output(entity="PRODUCT_A",field="price"),[call({"product_id":"PRODUCT_A","price":8000},"get_product")])

    def test_pending_refund_cannot_be_claimed_returned(self):
        with self.assertRaises(AgentOutputValidationError):
            validate_evidence(output(value="200",entity="ORDER_A",field="returned_points"),[
                call({"order_id":"ORDER_A","expected_points":200,"returned_points":0},"get_points_refund_status")])

    def test_point_amount_requires_field_reference(self):
        with self.assertRaises(AgentOutputValidationError):
            validate_evidence(AgentOutput(reply_type="ANSWER",reply_content="8000积分"),[call({"account_id":"PA_A","available_points":8000})])

    def test_expired_refund_cannot_be_claimed_usable(self):
        with self.assertRaises(AgentOutputValidationError):
            validate_evidence(output(value="80",entity="ORDER_A",field="usable_returned_points"),[
                call({"order_id":"ORDER_A","returned_points":80,"expired_points":80,"usable_returned_points":0},"get_points_refund_status")])

    def test_unknown_policy_citation_is_rejected(self):
        with self.assertRaises(AgentOutputValidationError):
            validate_evidence(AgentOutput(reply_type="ANSWER",reply_content="policy",knowledge_references=["FAKE"]),[])


class PolicyTests(unittest.TestCase):
    def test_array_field_paths_are_read_without_executing_expressions(self):
        self.assertEqual(read_field({"items":[{"product_id":"PRODUCT_A"}]}, "items[0].product_id"), "PRODUCT_A")
        self.assertEqual(read_field({"specs":{"续航":"40小时"}},"specs.续航"),"40小时")
        for path in ("items[-1]", "__import__('os')", "items[0:2]"):
            with self.assertRaises(KeyError):
                read_field({"items":[]}, path)

    def test_verified_rule_quote_is_not_current_order_status(self):
        business=call({"id":"ORDER_A","status":"completed"},"get_order")
        policy=call([{"knowledge_id":"RULE_A","content":"已发货不能直接取消，应申请售后。"}],"search_knowledge")
        answer=AgentOutput(reply_type="ANSWER",reply_content="订单已完成。规则说明：“已发货不能直接取消，应申请售后”。",knowledge_references=["RULE_A"])
        self.assertEqual(AnswerValidator().validate(answer,(business,policy)),answer)
        with self.assertRaises(AgentOutputValidationError):
            AnswerValidator().validate(answer.model_copy(update={"reply_content":"该订单已发货。"}),(business,policy))
        with self.assertRaises(AgentOutputValidationError):
            AnswerValidator().validate(answer.model_copy(update={"knowledge_references":[]}),(business,policy))

    def test_tenant_project_and_external_scope(self):
        service=KnowledgeQueryService()
        docs=asyncio.run(service.search("积分有效期", "tenant_a","welfare_a"))
        self.assertIn("DEMO_A_EXPIRY",[d.knowledge_id for d in docs])
        self.assertNotIn("DEMO_B_EXPIRY",[d.knowledge_id for d in docs])
        self.assertFalse(any(d.reference_only for d in docs))

    def test_named_public_rule_remains_reference(self):
        docs=asyncio.run(KnowledgeQueryService().search("浙江邮政积分兑换比例", "tenant_a","welfare_a"))
        self.assertEqual(docs[0].knowledge_id,"REF_POSTAL")
        self.assertTrue(docs[0].reference_only)


if __name__=="__main__":unittest.main(verbosity=2)
