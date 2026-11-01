from __future__ import annotations

import json

import httpx
import pytest

from app.agents.orchestrator import MarketOrchestrator
from app.core.security import AuthContext
from app.llm.provider import FakeLlmProvider
from app.repositories.memory import InMemoryStore


class FaultingProvider(FakeLlmProvider):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def understand_request(self, content, context=None):
        del content, context
        if self.mode == "timeout":
            raise httpx.ReadTimeout("simulated timeout")
        if self.mode == "429":
            response = httpx.Response(429, request=httpx.Request("POST", "https://llm.test"))
            raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)
        if self.mode == "500":
            response = httpx.Response(500, request=httpx.Request("POST", "https://llm.test"))
            raise httpx.HTTPStatusError("server error", request=response.request, response=response)
        if self.mode == "invalid_json":
            raise json.JSONDecodeError("invalid", "{", 1)
        raise AssertionError(self.mode)


@pytest.mark.parametrize("mode", ["timeout", "429", "500", "invalid_json"])
def test_llm_failure_keeps_deterministic_order_query_available(mode):
    store = InMemoryStore()
    orchestrator = MarketOrchestrator(store, 0.015, llm=FaultingProvider(mode))
    auth = AuthContext(
        "tenant_demo", "customer_001", "customer", "req_fault", "customer_001", "customer_001"
    )

    result = orchestrator.invoke(auth, "请查询 order_1001 的物流")

    assert result["kind"] == "order_fact"
    assert result["order"]["order_id"] == "order_1001"
    assert store.tickets == {}
    runs = store.list_agent_runs("tenant_demo")
    assert runs and runs[-1].fallback_used is True
