"""edge_api_server용 LLM 클라이언트.

LLM 호출 로직은 공통 모듈(rist_common.llm)로 이전되었다. 이 모듈은
기존 import 경로(app.llm_client)와 LocalLlmClient 이름의 하위 호환을 위해
공통 구현을 그대로 재노출한다.
"""

from __future__ import annotations

from rist_common.llm import LlmError
from rist_common.llm.client import LlmClient

# 하위 호환: 기존 코드/테스트는 LocalLlmClient 이름을 사용한다.
LocalLlmClient = LlmClient

__all__ = ["LlmError", "LlmClient", "LocalLlmClient"]

