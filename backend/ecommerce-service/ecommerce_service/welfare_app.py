from contextlib import asynccontextmanager
from contextlib import suppress
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from ecommerce_service.database import Base, engine, SessionLocal, get_db
from ecommerce_service.common.auth import Principal, current_principal, require_roles, create_access_token
from ecommerce_service.models import *
from ecommerce_service.schemas import DemoLoginRequest, AddressUpdate, AfterSaleCreate, RedemptionRequest
from ecommerce_service.migrate import migrate
from ecommerce_service.seed import seed_demo_data
from ecommerce_service.welfare_seed import seed_welfare_data
from ecommerce_service import welfare as service
from ecommerce_service.expiry import expiry_loop, settle_once

DB = Annotated[Session, Depends(get_db)]
Actor = Annotated[Principal, Depends(current_principal)]
Customer = Annotated[Principal, Depends(require_roles("customer"))]
Staff = Annotated[Principal, Depends(require_roles("agent", "admin"))]
Key = Annotated[str | None, Header(alias="Idempotency-Key")]


@asynccontextmanager
async def lifespan(_):
    migrate()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo_data(db)
        seed_welfare_data(db)
    await asyncio.to_thread(settle_once)
    expiry_task=asyncio.create_task(expiry_loop())
    try:
        yield
    finally:
        expiry_task.cancel()
        with suppress(asyncio.CancelledError):
            await expiry_task


app = FastAPI(title="有分积分福利商城（演示）", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[f"http://{host}:{port}" for host in ["localhost","127.0.0.1"] for port in [5173,5174]],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def result(data, message="查询成功"):
    return {"success":True,"code":"OK","message":message,"data":data,"retryable":False}


def projects_for(db, user):
    return db.scalars(select(WelfareProject).join(ProjectMember, ProjectMember.project_id == WelfareProject.id).where(
        ProjectMember.user_id == user.id, WelfareProject.tenant_id == user.tenant_id).order_by(WelfareProject.id)).all()


@app.get("/health")
def health(): return {"status":"ok","service":"ecommerce-service"}


@app.post("/api/v1/auth/demo-token")
def demo_token(request: DemoLoginRequest, db: DB):
    user = db.get(User, request.user_id)
    if not user: raise HTTPException(404,"演示账号不存在")
    role = "admin" if user.id.startswith("admin_") else "agent" if user.id.startswith("agent_") else "customer"
    if request.role != role: raise HTTPException(403,"账号角色不匹配")
    projects = projects_for(db, user)
    chosen = request.welfare_project_id or next((p.id for p in projects if p.id != "birthday_a"), projects[0].id if projects else "")
    if chosen not in {p.id for p in projects}: raise HTTPException(403,"无权访问该福利项目")
    principal = Principal(user_id=user.id, role=role, tenant_id=user.tenant_id, welfare_project_id=chosen)
    return {"access_token":create_access_token(principal),"token_type":"bearer","principal":principal.model_dump(),
            "display_name":user.name,"tenant_name":db.get(Tenant,user.tenant_id).name,
            "projects":[{"id":p.id,"name":p.name} for p in projects]}


@app.get("/api/v1/demo/users")
def demo_users(db: DB, staff: bool = False):
    users = db.scalars(select(User).order_by(User.id)).all()
    return [{"id":u.id,"name":u.name,"membership":u.membership,"tenant_id":u.tenant_id,
             "tenant_name":db.get(Tenant,u.tenant_id).name,"projects":[{"id":p.id,"name":p.name} for p in projects_for(db,u)]}
            for u in users if u.id.startswith(("agent_","admin_")) == staff]


@app.get("/api/v1/products")
def search_products(db: DB, principal: Actor, query: str = Query("", max_length=100)):
    statement = select(Product).join(ProjectProduct).where(ProjectProduct.project_id == principal.welfare_project_id)
    if query:
        statement = statement.where(or_(*(field.ilike(f"%{query}%") for field in [Product.name,Product.category,Product.description])))
    return result([service.product_payload(db,p,principal) for p in db.scalars(statement.order_by(Product.id).limit(30)).all()])


@app.get("/api/v1/products/{product_id}")
def get_product(product_id: str, db: DB, principal: Actor):
    product,_ = service.project_product(db,product_id,principal)
    return result(service.product_payload(db,product,principal))


@app.get("/api/v1/products/{product_id}/stock")
def stock(product_id: str, db: DB, principal: Actor):
    p,_ = service.project_product(db,product_id,principal)
    return result({"product_id":p.id,"stock":p.stock,"status":p.status})


@app.get("/api/v1/points/account")
def points_account(db: DB, principal: Actor, user_id: str | None = None):
    if user_id and principal.role == "customer" and user_id != principal.user_id: raise HTTPException(403,"无权查询其他员工账户")
    return result(service.account_payload(db,service.account(db,principal,user_id=user_id)))


@app.get("/api/v1/points/transactions")
def points_transactions(db: DB, principal: Customer, order_id: str | None = None, limit: int = Query(30,ge=1,le=100)):
    acct = service.account(db,principal)
    statement = select(PointsTransaction).where(PointsTransaction.account_id == acct.id)
    if order_id:
        service.owned_order(db,order_id,principal)
        statement = statement.where(PointsTransaction.order_id == order_id)
    records = db.scalars(statement.order_by(PointsTransaction.created_at.desc(),PointsTransaction.id).limit(limit)).all()
    return result([{"id":r.id,"account_id":r.account_id,"kind":r.kind,"delta":r.delta,"order_id":r.order_id,
                    "after_sale_id":r.after_sale_id,"reason":r.reason,"created_at":r.created_at.isoformat()} for r in records])


@app.get("/api/v1/points/expiring")
def expiring_points(db: DB, principal: Customer, days: int = Query(30,ge=1,le=366)):
    acct = service.account(db,principal); now = datetime.now(UTC)
    rows = db.scalars(select(PointsBatch).where(PointsBatch.account_id == acct.id,PointsBatch.remaining > 0,
        PointsBatch.expires_at > now,PointsBatch.expires_at <= now+timedelta(days=days)).order_by(PointsBatch.expires_at)).all()
    return result({"account_id":acct.id,"days":days,"total_points":sum(r.remaining for r in rows),"as_of":now.isoformat(),
                   "batches":[{"batch_id":r.id,"points":r.remaining,"expires_at":service.local_time(r.expires_at),"source":r.source} for r in rows]})


@app.get("/api/v1/points/refunds/{order_id}")
def points_refund(order_id: str, db: DB, principal: Actor):
    return result(service.refund_payload(db,service.owned_order(db,order_id,principal)))


@app.get("/api/v1/redemptions/quote")
def quote(db: DB, principal: Customer, product_id: str, quantity: int = Query(1,ge=1,le=10)):
    return result(service.quote_redemption(db,principal,product_id,quantity))


@app.post("/api/v1/redemptions")
def redeem(request: RedemptionRequest, db: DB, principal: Customer, idempotency_key: Key = None):
    key,fp,cached = service.begin_operation(db,principal,idempotency_key,"redeem",request.model_dump())
    if cached: return cached
    order = service.redeem(db,principal,request)
    return service.finish_operation(db,principal,key,fp,result(service.order_payload(db,order),"兑换成功"))


@app.get("/api/v1/orders")
def orders(db: DB, principal: Actor):
    statement = select(Order).where(*service.scope(Order,principal))
    if principal.role == "customer": statement = statement.where(Order.user_id == principal.user_id)
    return result([service.order_payload(db,o) for o in db.scalars(statement.order_by(Order.created_at.desc())).unique().all()])


@app.get("/api/v1/orders/{order_id}")
def order(order_id: str, db: DB, principal: Actor):
    return result(service.order_payload(db,service.owned_order(db,order_id,principal)))


@app.get("/api/v1/orders/{order_id}/logistics")
def logistics(order_id: str, db: DB, principal: Actor):
    o = service.owned_order(db,order_id,principal)
    if not o.logistics: return {"success":False,"code":"LOGISTICS_NOT_AVAILABLE","message":"订单尚未产生物流信息","data":None}
    l=o.logistics
    return result({"order_id":o.id,"company":l.company,"tracking_number":l.tracking_number,"status":l.status,"latest_event":l.latest_event,"events":l.events})


@app.post("/api/v1/orders/{order_id}/cancel")
def cancel(order_id: str, db: DB, principal: Customer, idempotency_key: Key = None):
    key,fp,cached = service.begin_operation(db,principal,idempotency_key,"cancel",{"order_id":order_id})
    if cached: return cached
    o = service.owned_order(db,order_id,principal,lock=True)
    if o.status not in {"paid","pending_shipment","cancelled"}: raise HTTPException(409,"当前状态不能直接取消，请申请售后")
    if o.status != "cancelled":
        o.status="cancelled";o.version+=1
        service.refund_order(db,o)
    return service.finish_operation(db,principal,key,fp,result(service.order_payload(db,o),"订单已取消，请查看独立退分状态"))


@app.patch("/api/v1/orders/{order_id}/address")
def address(order_id: str, request: AddressUpdate, db: DB, principal: Customer, idempotency_key: Key = None):
    key,fp,cached = service.begin_operation(db,principal,idempotency_key,"address",{"order_id":order_id,**request.model_dump()})
    if cached: return cached
    o=service.owned_order(db,order_id,principal,lock=True)
    if o.status not in {"paid","pending_shipment"}:raise HTTPException(409,"当前状态不能修改地址")
    o.address=request.address;o.version+=1
    return service.finish_operation(db,principal,key,fp,result(service.order_payload(db,o),"地址已更新"))


@app.post("/api/v1/orders/{order_id}/after-sales")
def create_after_sale(order_id: str, request: AfterSaleCreate, db: DB, principal: Customer, idempotency_key: Key = None):
    key,fp,cached=service.begin_operation(db,principal,idempotency_key,"after_sale",{"order_id":order_id,**request.model_dump()})
    if cached:return cached
    o=service.owned_order(db,order_id,principal,lock=True)
    if o.status in {"cancelled","refunded"}:raise HTTPException(409,"当前订单不能申请售后")
    row=db.scalar(select(AfterSale).where(AfterSale.order_id==o.id,AfterSale.status.in_(["pending","processing","approved"])))
    if not row:
        row=AfterSale(id=service.uid("AS"),user_id=o.user_id,order_id=o.id,kind=request.kind,reason=request.reason,status="pending")
        db.add(row);db.flush()
    return service.finish_operation(db,principal,key,fp,result({"after_sale_id":row.id,"status":row.status},"售后申请已受理，审核不等于退分到账"))


@app.get("/api/v1/after-sales")
def after_sales(db: DB, principal: Actor, order_id: str | None = None):
    statement=select(AfterSale).join(Order).where(*service.scope(Order,principal))
    if principal.role=="customer":statement=statement.where(Order.user_id==principal.user_id)
    if order_id:
        service.owned_order(db,order_id,principal);statement=statement.where(Order.id==order_id)
    rows=db.scalars(statement.order_by(AfterSale.created_at.desc())).all()
    return result([{"id":r.id,"order_id":r.order_id,"user_id":r.user_id,"kind":r.kind,"reason":r.reason,"status":r.status,
                    "created_at":r.created_at.isoformat(),"refund":service.refund_payload(db,db.get(Order,r.order_id))} for r in rows])


@app.post("/api/v1/admin/orders/{order_id}/refund")
def complete_refund(order_id: str, db: DB, principal: Staff, idempotency_key: Key = None):
    key,fp,cached=service.begin_operation(db,principal,idempotency_key,"complete_refund",{"order_id":order_id})
    if cached:return cached
    o=service.owned_order(db,order_id,principal,lock=True)
    after=db.scalar(select(AfterSale).where(AfterSale.order_id==o.id,AfterSale.status.in_(["pending","processing","approved"])))
    if o.status not in {"cancelled","refunded"} and (after is None or after.kind=="exchange"):
        raise HTTPException(409,"需要已取消订单或退款退货售后申请；换货不执行退分")
    if after: after.status="completed"
    service.refund_order(db,o,after_sale_id=after.id if after else None)
    if o.status!="cancelled":o.status="refunded"
    o.version+=1
    return service.finish_operation(db,principal,key,fp,result(service.refund_payload(db,o),"演示人工核对后完成退分"))


@app.get("/api/v1/admin/summary")
def summary(db: DB, principal: Staff):
    return {"products":db.scalar(select(func.count()).select_from(ProjectProduct).where(ProjectProduct.project_id==principal.welfare_project_id)),
            "orders":db.scalar(select(func.count()).select_from(Order).where(*service.scope(Order,principal))),
            "after_sales":db.scalar(select(func.count()).select_from(AfterSale).join(Order).where(*service.scope(Order,principal))),
            "tenant_id":principal.tenant_id,"welfare_project_id":principal.welfare_project_id,"generated_at":datetime.now(UTC).isoformat()}
