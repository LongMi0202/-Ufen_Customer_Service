"""Expire remaining lots in a business task; read-only Agent tools never mutate balances."""
import asyncio
from datetime import UTC, datetime
import logging
from sqlalchemy import select
from ecommerce_service.database import SessionLocal
from ecommerce_service.models import PointsAccount, PointsBatch, PointsTransaction
from ecommerce_service.welfare import uid

logger=logging.getLogger(__name__)


def settle_expired(db):
    now=datetime.now(UTC)
    account_ids=select(PointsBatch.account_id).where(PointsBatch.expires_at<=now,PointsBatch.remaining>0)
    accounts=db.scalars(select(PointsAccount).where(PointsAccount.id.in_(account_ids)).with_for_update(skip_locked=True)).all()
    count=0
    for account in accounts:
        lots=db.scalars(select(PointsBatch).where(PointsBatch.account_id==account.id,
            PointsBatch.expires_at<=now,PointsBatch.remaining>0).with_for_update()).all()
        for lot in lots:
            expired=lot.remaining
            lot.remaining=0;account.balance-=expired;account.version+=1
            db.add(PointsTransaction(id=uid("PT"),account_id=account.id,kind="expire",delta=-expired,
                reason=f"积分批次 {lot.id} 到期失效"))
            count+=1
    return count


def settle_once():
    with SessionLocal.begin() as db:
        return settle_expired(db)


async def expiry_loop():
    while True:
        try:
            await asyncio.to_thread(settle_once)
        except Exception:
            logger.exception("积分到期处理失败，下轮重试")
        await asyncio.sleep(60)
