"""Run inside the commerce container. All test mutations are rolled back."""
from datetime import UTC, datetime, timedelta
import unittest
from sqlalchemy import select
from ecommerce_service.database import SessionLocal
from ecommerce_service.models import PointsAccount, PointsBatch, PointsTransaction, Product, ProjectProduct
from ecommerce_service.expiry import settle_expired
from ecommerce_service.common.auth import Principal
from ecommerce_service.schemas import RedemptionRequest
from ecommerce_service.welfare import redeem, refund_order, refund_payload, uid


class ExpiryTests(unittest.TestCase):
    def test_expiry_is_idempotent_and_recorded(self):
        with SessionLocal() as db:
            try:
                account=db.scalar(select(PointsAccount).where(PointsAccount.id=="PA_user_002_welfare_a").with_for_update())
                before=account.balance
                lot=PointsBatch(id="PB_TEST_EXPIRY",account_id=account.id,source="test",granted=17,remaining=17,
                                expires_at=datetime.now(UTC)-timedelta(seconds=1))
                db.add(lot);account.balance+=17;db.flush()
                self.assertGreaterEqual(settle_expired(db),1);db.flush()
                self.assertEqual(account.balance,before)
                self.assertEqual(lot.remaining,0)
                self.assertEqual(settle_expired(db),0)
                rows=db.scalars(select(PointsTransaction).where(PointsTransaction.account_id==account.id,
                    PointsTransaction.reason.contains("PB_TEST_EXPIRY"))).all()
                self.assertEqual(len(rows),1);self.assertEqual(rows[0].delta,-17)
            finally:db.rollback()

    def test_refund_preserves_expiry_and_separates_usable_points(self):
        with SessionLocal() as db:
            try:
                actor = Principal(user_id="user_002", role="customer", tenant_id="tenant_a", welfare_project_id="welfare_a")
                account = db.scalar(select(PointsAccount).where(PointsAccount.id == "PA_user_002_welfare_a").with_for_update())
                lots = db.scalars(select(PointsBatch).where(PointsBatch.account_id == account.id).with_for_update()).all()
                for lot in lots:
                    lot.remaining = 0
                price = db.get(ProjectProduct, ("welfare_a", "PRODUCT_005")).points_price
                self.assertGreater(price, 17)
                account.balance = price
                now = datetime.now(UTC)
                short = PointsBatch(id=uid("PB_TEST"), account_id=account.id, source="test", granted=17, remaining=17, expires_at=now+timedelta(days=1))
                valid = PointsBatch(id=uid("PB_TEST"), account_id=account.id, source="test", granted=price-17, remaining=price-17, expires_at=now+timedelta(days=2))
                db.add_all([short, valid]); db.flush()
                product = db.scalar(select(Product).where(Product.id == "PRODUCT_005").with_for_update())
                stock = product.stock
                order = redeem(db, actor, RedemptionRequest(product_id=product.id, quantity=1, address="domain test address"))
                self.assertEqual((short.remaining, valid.remaining), (0, 0))
                short.expires_at = now-timedelta(seconds=1)
                db.flush()
                order.status = "cancelled"
                refund_order(db, order)
                data = refund_payload(db, order)
                self.assertEqual(data["status"], "completed")
                self.assertEqual(data["returned_points"], price)
                self.assertEqual(data["expired_points"], 17)
                self.assertEqual(data["usable_returned_points"], price-17)
                self.assertEqual(account.balance, price-17)
                self.assertEqual((short.remaining, valid.remaining), (0, price-17))
                self.assertEqual(valid.expires_at, now+timedelta(days=2))
                self.assertEqual(product.stock, stock)
                refund_order(db, order)
                rows = db.scalars(select(PointsTransaction).where(PointsTransaction.order_id == order.id)).all()
                self.assertEqual(sum(r.delta for r in rows), -17)
                self.assertEqual(len([r for r in rows if r.kind == "refund"]), 1)
                self.assertEqual(product.stock, stock)
            finally:
                db.rollback()


if __name__=="__main__":unittest.main(verbosity=2)
