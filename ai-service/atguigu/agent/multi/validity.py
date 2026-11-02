import asyncio
import json
from urllib.request import Request, urlopen

from atguigu.agent.multi.budget import ValidityUnavailable


class TurnValidity:
    """Authenticated callback: it reads turn/version/lease state, never model arguments."""
    def __init__(self, settings, request, context):
        self.settings, self.request, self.context = settings, request, context
        self.last_checked = 0.0
        self.last_valid = True
        self.lock = asyncio.Lock()

    async def __call__(self):
        from time import monotonic
        if self.request.snapshot_revision is None or self.request.turn_attempt is None:
            # Internal diagnostic requests may omit a real Customer Service turn.
            return True
        async with self.lock:
            if monotonic() - self.last_checked < 1:
                return self.last_valid
            path = f"/internal/v1/turns/{self.request.turn_id}/validity"
            payload = {"conversation_id": self.request.conversation_id,
                       "snapshot_revision": self.request.snapshot_revision, "turn_attempt": self.request.turn_attempt}
            headers = {"Authorization": f"Bearer {self.context.access_token}",
                       "X-Internal-Service-Token": self.settings.internal_service_token,
                       "Content-Type": "application/json"}
            def fetch():
                req = Request(self.settings.customer_service_base_url.rstrip("/") + path,
                              data=json.dumps(payload).encode(), headers=headers, method="POST")
                with urlopen(req, timeout=3) as response:
                    return json.load(response)["valid"] is True
            # Fail closed: an unavailable validity service cannot authorize more work.
            try:
                valid = await asyncio.to_thread(fetch)
            except Exception as exc:
                raise ValidityUnavailable("暂时无法确认本轮执行有效性，需要重试") from exc
            self.last_checked = monotonic()
            self.last_valid = valid
            return valid
