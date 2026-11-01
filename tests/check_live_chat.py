"""Opt-in real model/Worker/Outbox smoke test; keeps labelled demo chat messages."""
import argparse
import json
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))
from test_welfare_api import call, login


def ask(question, user="user_002", project="welfare_a"):
    token=login(user,project=project)
    status,history=call(8000,"/api/v1/chat/history",token)
    assert status==200,history
    sequence=max([m["sequence"] for m in history],default=0)
    status,accepted=call(8000,"/api/v1/chat/messages",token,{"message_id":"live_check_"+uuid4().hex,"type":"text","content":{"text":question}})
    assert status==200,accepted
    start=time.monotonic()
    print("QUESTION",question,flush=True)
    while time.monotonic()-start < 175:
        status,history=call(8000,f"/api/v1/chat/history?after_sequence={sequence}",token)
        assert status==200,history
        replies=[m for m in history if m["role"]=="ai" and m["conversation_id"]==accepted["conversation_id"]]
        if replies:
            content=replies[-1]["content"]
            print("REPLY",json.dumps(content,ensure_ascii=False),flush=True)
            print("SECONDS",round(time.monotonic()-start,2),flush=True)
            assert content.get("kind")!="error",content
            return content
        time.sleep(1)
    raise AssertionError("No AI response within deadline")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--scenario",choices=["points","policy","refund","quote","handoff","birthday"],default="points")
    args=parser.parse_args()
    questions={
        "points":"我的可用积分余额有多少？未来30天有多少积分即将过期？",
        "policy":"我们年度福利项目积分有效期多久？取消订单后如何退分？请按当前项目规则解释。",
        "refund":"请核查订单ORDER_A_003是否已取消，积分是否已经退回？",
        "quote":"请查询商品PRODUCT_001，我的积分能否兑换一个？只做查询，不要操作。",
        "handoff":"积分退回有争议，我要联系人工客服。",
        "birthday":"我的生日福利积分有多少，可以兑换什么商品？",
    }
    content=ask(questions[args.scenario],user="user_001" if args.scenario=="refund" else "user_002",
                project="birthday_a" if args.scenario=="birthday" else "welfare_a")
    if args.scenario in {"points","quote","birthday"}:
        assert content.get("business_facts"),"Missing validated entity evidence"
    if args.scenario=="policy":assert content.get("sources"),"Missing policy citations"
