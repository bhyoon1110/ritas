from __future__ import annotations


class LlmError(Exception):
    """로컬 LLM 호출 과정에서 발생하는 표준 오류.

    code/message/retryable 세 필드로 상위 호출자(워커, CLI 등)가 재시도
    여부와 사용자 표시 메시지를 일관되게 처리할 수 있도록 한다.
    """

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
