"""Customer Service integration tests. Temporary DB rows are rolled back."""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from atguigu.app.routers.internal import ValidityRequest, turn_validity
from atguigu.app.schemas.admin.user import CurrentUser
from atguigu.app.services.admin.auth import AuthService
from atguigu.common.config import get_settings
from atguigu.common.utils import get_utcnow
from atguigu.infrastucture.db import session_factory
from atguigu.models.models import Conversation, ConversationTurn
from atguigu.worker.ai.worker import TurnProcessor


class ValidityTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_revision_lease_attempt_and_service_authorization(self):
        uid = uuid4().hex
        user_id = "validity_" + uid[:20]
        actor = CurrentUser(user_id=user_id, role="customer", tenant_id="tenant_a", welfare_project_id="welfare_a")
        auth = "Bearer " + AuthService().encode_access_token(actor)
        now = get_utcnow()
        async with session_factory() as session:
            try:
                conversation = Conversation(id="conv_" + uid, user_id=user_id, tenant_id="tenant_a",
                    welfare_project_id="welfare_a", mode="AI", input_revision=2, answered_revision=0)
                session.add(conversation)
                await session.flush()
                turn = ConversationTurn(id="turn_" + uid, conversation_id=conversation.id, user_id=user_id,
                    status="RUNNING", start_revision=1, snapshot_revision=2, attempts=1,
                    collect_until=now, max_collect_until=now, locked_until=now + timedelta(minutes=1))
                session.add(turn)
                await session.flush()
                body = ValidityRequest(conversation_id=conversation.id, snapshot_revision=2, turn_attempt=1)
                async def check(authorization=auth, internal_token=get_settings().internal_service_token, payload=body):
                    return await turn_validity(turn.id, payload, session, authorization, internal_token)
                self.assertTrue((await check())["valid"])
                self.assertFalse((await check(payload=body.model_copy(update={"turn_attempt": 2})))["valid"])
                conversation.input_revision = 3
                await session.flush()
                self.assertFalse((await check())["valid"])
                conversation.input_revision = 2
                conversation.mode = "HUMAN"
                await session.flush()
                self.assertFalse((await check())["valid"])
                conversation.mode = "AI"
                turn.locked_until = now - timedelta(seconds=1)
                await session.flush()
                self.assertFalse((await check())["valid"])
                with self.assertRaises(HTTPException) as denied:
                    await check(internal_token="invalid")
                self.assertEqual(denied.exception.status_code, 403)
                other = actor.model_copy(update={"tenant_id": "tenant_b"})
                with self.assertRaises(HTTPException) as hidden:
                    await check(authorization="Bearer " + AuthService().encode_access_token(other))
                self.assertEqual(hidden.exception.status_code, 404)
            finally:
                await session.rollback()

    async def test_old_attempt_cannot_finalize_new_lease(self):
        session = MagicMock()
        session.commit = AsyncMock()
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        service = MagicMock()
        service.find_turn_conversation_by_id = AsyncMock(return_value=(
            SimpleNamespace(status="RUNNING", attempts=2), SimpleNamespace()))
        result_service = MagicMock()
        result_service.save_ai_result = AsyncMock()
        with patch("atguigu.worker.ai.worker.session_factory", return_value=manager), \
             patch("atguigu.worker.ai.worker.TurnService", return_value=service), \
             patch("atguigu.worker.ai.worker.AIResultService", return_value=result_service):
            await TurnProcessor()._finalize_turn("turn", "old_run", {"outcome_type": "message"}, None, expected_attempt=1)
        session.commit.assert_not_awaited()
        result_service.save_ai_result.assert_not_awaited()

    async def test_old_attempt_cannot_prepare_new_lease(self):
        session = MagicMock()
        session.commit = AsyncMock()
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=session)
        manager.__aexit__ = AsyncMock(return_value=False)
        turn = SimpleNamespace(status="RUNNING", attempts=2, run_id="replacement_run")
        service = MagicMock()
        service.find_turn_conversation_by_id = AsyncMock(return_value=(turn, SimpleNamespace()))
        with patch("atguigu.worker.ai.worker.session_factory", return_value=manager), \
             patch("atguigu.worker.ai.worker.TurnService", return_value=service):
            self.assertFalse(await TurnProcessor()._validate_before_commit("turn", "old_run", expected_attempt=1))
        self.assertEqual(turn.run_id, "replacement_run")
        session.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main(verbosity=2)
