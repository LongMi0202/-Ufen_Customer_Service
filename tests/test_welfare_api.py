"""Live integration tests for the demo Compose stack. Uses only Python's standard library.

Creates demo orders and cancels them, restoring their stock and usable points.
"""
import concurrent.futures
import json
import unittest
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from uuid import uuid4


def call(port, path, token=None, payload=None, key=None):
    headers={"Authorization":f"Bearer {token}"} if token else {}
    if key:headers["Idempotency-Key"]=key
    data=None
    if payload is not None:
        headers["Content-Type"]="application/json";data=json.dumps(payload).encode()
    request=Request(f"http://127.0.0.1:{port}{path}",data=data,headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(request,timeout=15) as response:
            raw=response.read();return response.status,json.loads(raw) if raw else None
    except HTTPError as error:
        return error.code,json.loads(error.read())


def login(user, role="customer", project="welfare_a"):
    status,body=call(8001,"/api/v1/auth/demo-token",payload={"user_id":user,"role":role,"welfare_project_id":project})
    assert status==200,body
    return body["access_token"]


class WelfareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a=login("user_002");cls.a1=login("user_001")
        cls.b=login("user_003",project="welfare_b")
        cls.birthday=login("user_002",project="birthday_a")
        cls.admin=login("admin_001","admin")
        cls.admin_b=login("admin_002","admin","welfare_b")

    def data(self,path,token=None):
        status,body=call(8001,path,token or self.a)
        self.assertEqual(status,200,body)
        return body["data"]

    def test_scope_and_project_product_pool(self):
        self.assertEqual(call(8001,"/api/v1/orders/ORDER_A_001",self.b)[0],404)
        self.assertEqual(call(8001,"/api/v1/products/PRODUCT_001",self.birthday)[0],404)
        self.assertEqual(call(8001,"/api/v1/points/account?user_id=user_001",self.a)[0],403)
        self.assertEqual(call(8001,"/api/v1/auth/demo-token",payload={"user_id":"user_003","role":"customer","welfare_project_id":"welfare_a"})[0],403)
        cake=self.data("/api/v1/products",self.birthday)
        self.assertEqual([p["id"] for p in cake],["PRODUCT_CAKE"])
        self.assertEqual(self.data("/api/v1/points/account",self.birthday)["available_points"],520)

    def test_cancelled_order_does_not_imply_refunded(self):
        order=self.data("/api/v1/orders/ORDER_A_003",self.a1)
        refund=self.data("/api/v1/points/refunds/ORDER_A_003",self.a1)
        self.assertEqual(order["status"],"cancelled")
        self.assertEqual(refund["status"],"pending")
        self.assertEqual(refund["returned_points"],0)
        self.assertEqual(refund["expected_points"],order["total_points"])

    def test_quote_and_insufficient_points(self):
        normal=self.data("/api/v1/redemptions/quote?product_id=PRODUCT_005&quantity=1")
        self.assertTrue(normal["eligible"])
        quote=self.data("/api/v1/redemptions/quote?product_id=PRODUCT_001&quantity=10",self.b)
        self.assertFalse(quote["eligible"])
        self.assertTrue(quote["reasons"])
        before=self.data("/api/v1/points/account",self.b)["available_points"]
        status,_=call(8001,"/api/v1/redemptions",self.b,{"product_id":"PRODUCT_001","quantity":10,"address":"integration test address"},uuid4().hex)
        self.assertEqual(status,409)
        self.assertEqual(before,self.data("/api/v1/points/account",self.b)["available_points"])

    def test_concurrent_idempotency_and_refund_once(self):
        before=self.data("/api/v1/points/account")["available_points"]
        stock=self.data("/api/v1/products/PRODUCT_005/stock")["stock"]
        key="test_"+uuid4().hex
        payload={"product_id":"PRODUCT_005","quantity":1,"address":"integration test address"}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            responses=list(pool.map(lambda _:call(8001,"/api/v1/redemptions",self.a,payload,key),range(5)))
        for status,body in responses:self.assertEqual(status,200,body)
        ids={body["data"]["id"] for _,body in responses};self.assertEqual(len(ids),1)
        order_id=ids.pop();order=responses[0][1]["data"]
        self.assertEqual(self.data("/api/v1/points/account")["available_points"],before-order["total_points"])
        self.assertEqual(call(8001,"/api/v1/redemptions",self.a,{**payload,"quantity":2},key)[0],409)
        self.assertEqual(call(8001,f"/api/v1/orders/{order_id}",self.a1)[0],404)
        for _ in range(2):
            status,body=call(8001,f"/api/v1/orders/{order_id}/cancel",self.a,{},uuid4().hex)
            self.assertEqual(status,200,body)
            self.assertEqual(body["data"]["refund"]["status"],"completed")
        self.assertEqual(self.data("/api/v1/points/account")["available_points"],before)
        self.assertEqual(self.data("/api/v1/products/PRODUCT_005/stock")["stock"],stock)
        rows=self.data(f"/api/v1/points/transactions?order_id={order_id}")
        self.assertEqual(sum(r["delta"] for r in rows),0)
        self.assertEqual(len([r for r in rows if r["kind"]=="refund"]),1)

    def test_policy_scope_and_reference_labels(self):
        status,docs=call(8002,"/api/v1/knowledge/documents",self.admin)
        self.assertEqual(status,200,docs)
        self.assertTrue(any(d["id"]=="DEMO_A_EXPIRY" for d in docs))
        self.assertFalse(any(d["id"]=="DEMO_B_EXPIRY" for d in docs))
        self.assertTrue(any(d["reference_only"] and d["url"] for d in docs))
        self.assertEqual(call(8002,"/api/v1/knowledge/documents",self.a)[0],403)

    def test_after_sale_refund_is_scoped_and_idempotent(self):
        before=self.data("/api/v1/points/account")["available_points"]
        status,body=call(8001,"/api/v1/redemptions",self.a,
            {"product_id":"PRODUCT_005","quantity":1,"address":"integration test address"},uuid4().hex)
        self.assertEqual(status,200,body);order_id=body["data"]["id"]
        status,_=call(8001,f"/api/v1/orders/{order_id}/after-sales",self.a,
                      {"kind":"refund","reason":"integration refund test"},uuid4().hex)
        self.assertEqual(status,200)
        self.assertEqual(call(8001,f"/api/v1/admin/orders/{order_id}/refund",self.admin_b,{},uuid4().hex)[0],404)
        for _ in range(2):
            status,body=call(8001,f"/api/v1/admin/orders/{order_id}/refund",self.admin,{},uuid4().hex)
            self.assertEqual(status,200,body);self.assertEqual(body["data"]["status"],"completed")
        self.assertEqual(self.data("/api/v1/points/account")["available_points"],before)

    def test_chat_scope_and_staff_access(self):
        _,a=call(8000,"/api/v1/conversations/current",self.a,{})
        _,birthday=call(8000,"/api/v1/conversations/current",self.birthday,{})
        self.assertNotEqual(a["id"],birthday["id"])
        staff_b=login("agent_002","agent","welfare_b")
        self.assertEqual(call(8000,f'/api/v1/conversations/{a["id"]}',staff_b)[0],404)
        staff_a=login("agent_001","agent")
        self.assertEqual(call(8000,f'/api/v1/conversations/{a["id"]}',staff_a)[0],200)
        self.assertEqual(call(8001,"/api/v1/redemptions",staff_a,{"product_id":"PRODUCT_005","quantity":1,"address":"test address"},uuid4().hex)[0],403)


if __name__=="__main__":unittest.main(verbosity=2)
