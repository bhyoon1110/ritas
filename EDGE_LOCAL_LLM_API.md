# Edge 분석 서버 - 로컬 LLM REST API 명세

## 1. 목적

Edge 분석 서버의 보고서 생성 worker가 같은 서버에서 실행되는 로컬 LLM을
호출하여 규칙 기반 보고서의 자유서술 슬롯을 보조 작성하기 위한 REST API를
정의한다.

이 문서의 호출 주체는 `edge_api_server`의 보고서 생성 worker이고, 수신
주체는 OpenAI 호환 API를 제공하는 로컬 LLM 서버(vLLM)이다.

## 2. 기본 정보

| 항목 | 값 |
|---|---|
| API 형식 | OpenAI-compatible REST API |
| Base URL | `http://127.0.0.1:8001` |
| 모델 | `gemma4-e4b` |
| 모델 경로 | `/models/gemma-4-E4B-it` |
| 최대 컨텍스트 | `8192` tokens |
| 문자 인코딩 | UTF-8 |
| 요청/응답 형식 | `application/json` |
| 연결 제한 시간 | `180`초 |
| 인증 | 없음 |

FastAPI는 `8000` 포트, 로컬 LLM은 `8001` 포트를 사용한다.

로컬 LLM API는 외부 또는 실험 PC에서 직접 호출하는 인터페이스가 아니다.
인증을 적용하지 않으므로 LLM 서버는 `127.0.0.1:8001`에만 바인딩하거나
방화벽으로 Edge 서버 내부에서만 접근할 수 있도록 제한해야 한다.

## 3. 환경 설정

공통 기본값은 다음 파일에서 관리한다.

```text
config/environments/development.env
config/environments/production.env
```

| 설정 | 기본값 | 설명 |
|---|---|---|
| `LOCAL_LLM_BASE_URL` | `http://127.0.0.1:8001` | 로컬 LLM Base URL |
| `LOCAL_LLM_MODEL` | `gemma4-e4b` | 요청 모델 ID |
| `LOCAL_LLM_TEMPERATURE` | `0.1` | 생성 무작위성 |
| `LOCAL_LLM_MAX_TOKENS` | `1200` | 최대 출력 토큰 |
| `LOCAL_LLM_CONTEXT_WINDOW` | `8192` | 모델 컨텍스트 길이 참고값 |
| `LOCAL_LLM_CONTEXT_MARGIN` | `256` | 컨텍스트 예산 계산용 예비 설정값 |
| `LOCAL_LLM_VALIDATE_MODEL` | `true` | 모델 조회 및 검증 여부 |
| `LOCAL_LLM_INCLUDE_IMAGES` | `true` | 분석 이미지 전달 여부 |
| `LOCAL_LLM_MAX_IMAGES` | `3` | 요청당 최대 이미지 수 |
| `LOCAL_LLM_MAX_IMAGE_BYTES` | `2097152` | 이미지당 최대 크기 |

실행 환경 변수 `RIST_LLM_*`를 사용하면 worker 설정만 재정의할 수 있다.

## 4. 처리 순서

1. worker가 `processed` 폴더에서 구조화 분석 JSON과 허용된 이미지를 읽는다.
2. 규칙 기반 보고서 작성기가 판정, 수치, 표와 기본 문안을 결정론적으로 만든다.
3. `LOCAL_LLM_VALIDATE_MODEL=true`이면 `/v1/models`를 호출해 설정 모델
   `gemma4-e4b`의 존재를 확인한다.
4. `/v1/chat/completions`에 `summary`, `narrative`, `caption` 슬롯 작성을
   요청한다.
5. 응답 원문과 요청 로그를 작업 폴더의 `logs/`에 저장한다.
6. 유효한 슬롯 JSON을 받으면 해당 슬롯만 LLM 문안으로 교체한다.
7. LLM 호출 또는 응답 파싱이 실패해도 작업은 실패시키지 않고 규칙 기반 기본
   문안으로 `report.json`, `report.md`, 요청 포맷의 `report.pptx` 또는
   `report.pdf`를 완성한다.

`report.json`은 정해진 보고서 양식의 구조화 데이터이며, Markdown과 PPTX/PDF
렌더링은 이 JSON과 동일한 `ReportDocument`를 기준으로 수행한다.

## 5. 모델 목록 조회

### 5.1 요청

```http
GET /v1/models
Host: 127.0.0.1:8001
Accept: application/json
```

### 5.2 성공 응답: `200 OK`

```json
{
  "object": "list",
  "data": [
    {
      "id": "gemma4-e4b",
      "object": "model",
      "owned_by": "vllm",
      "root": "/models/gemma-4-E4B-it",
      "max_model_len": 8192
    }
  ]
}
```

### 5.3 검증 규칙

- `data`는 배열이어야 한다.
- 배열에 `id`가 `gemma4-e4b`인 객체가 있어야 한다.
- `max_model_len`이 양의 정수이면 헬스체크 응답과 진단 로그에 참고값으로 사용한다.
- 모델 목록은 worker 프로세스에서 최초 조회 후 캐시한다.
- FastAPI의 `/health/llm` 점검은 캐시를 사용하지 않고 다시 조회한다.

## 6. 채팅 완료 요청

### 6.1 요청

```http
POST /v1/chat/completions
Host: 127.0.0.1:8001
Content-Type: application/json
```

### 6.2 요청 필드

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `model` | string | Y | `gemma4-e4b` |
| `messages` | array | Y | system 및 user 메시지 |
| `temperature` | number | Y | 기본값 `0.1` |
| `max_tokens` | integer | Y | 기본값 `1200` |
| `response_format` | object | Y | `{"type":"json_object"}` |

`messages`에는 다음 두 메시지를 순서대로 전달한다.

| 순서 | role | content |
|---:|---|---|
| 1 | `system` | 슬롯 작성 원칙과 JSON 출력 형식 |
| 2 | `user` | 구조화 분석 근거 JSON 및 선택적 분석 이미지 |

### 6.3 텍스트 요청 예시

```json
{
  "model": "gemma4-e4b",
  "messages": [
    {
      "role": "system",
      "content": "당신은 재료분석 실험실의 보고서 작성 보조자입니다. 제공된 분석 결과만 근거로 한국어 문안을 작성하세요. 출력은 반드시 JSON 객체 하나로만, 키는 summary/narrative/caption 입니다."
    },
    {
      "role": "user",
      "content": "다음 JSON은 분석 프로그램이 산출한 근거입니다. 이 근거만 사용해 summary, narrative, caption 슬롯을 작성하고, 키가 정확히 그 슬롯들인 JSON 객체 하나로만 응답하세요.\n\n{\"sample\":\"REQ-2026-00123\",\"tier\":\"미동정\",\"top_candidate\":{\"material\":\"후보 물질\",\"composite_pct\":64.5}}"
    }
  ],
  "temperature": 0.1,
  "max_tokens": 1200,
  "response_format": {"type": "json_object"}
}
```

### 6.4 Vision 요청

`processed` 폴더에 허용된 이미지가 있으면 user 메시지의 `content`를 배열로
전달한다.

지원 확장자는 다음과 같다.

- `.png`
- `.jpg`
- `.jpeg`
- `.webp`

```json
{
  "model": "gemma4-e4b",
  "messages": [
    {
      "role": "system",
      "content": "당신은 재료분석 실험실의 보고서 작성 보조자입니다."
    },
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "구조화 분석 JSON과 첨부 이미지를 근거로 summary, narrative, caption 슬롯만 작성하세요."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/png;base64,iVBORw0KGgoAAA..."
          }
        }
      ]
    }
  ],
  "temperature": 0.1,
  "max_tokens": 1200,
  "response_format": {"type": "json_object"}
}
```

- 이미지는 파일명 순으로 최대 3개까지 전달한다.
- 파일 크기가 2 MiB를 초과하면 해당 이미지는 제외한다.
- 이미지는 data URL 형태로 Base64 인코딩한다.
- 이미지에서 관찰한 내용과 JSON 근거를 구분하도록 지시한다.
- 이미지 하나만으로 물질 또는 원인을 확정하지 않는다.
- 요청 로그에는 Base64 본문을 저장하지 않는다.

## 7. 슬롯 작성 원칙

모든 실험에 다음 원칙을 적용한다.

1. Python 전처리 결과와 구조화 분석 JSON만 근거로 사용한다.
2. 제공되지 않은 수치, 물질, 피크, 판정 또는 원인을 추측하지 않는다.
3. 단정 대신 `가능성`, `시사함`, `검토 필요` 중심으로 작성한다.
4. 단일 피크 또는 단일 이미지로 물질명이나 작용기를 확정하지 않는다.
5. QC flag, 데이터 누락, 불확실성 및 분석 한계를 명시한다.
6. 과도한 화학 구조 추정과 근거 없는 인과관계를 작성하지 않는다.
7. 판정, 수치, 표는 규칙 기반 보고서 작성기가 이미 생성하므로 LLM은
   `summary`, `narrative`, `caption` 자유서술 슬롯만 작성한다.

FT-IR에는 다음 원칙을 추가 적용한다.

1. Python 전처리와 룰 기반 판정 결과를 우선한다.
2. 라이브러리 매칭 결과와 룰 기반 판정 결과를 구분한다.
3. 매칭 점수가 임계값 미만이면 확정 동정이 아님을 명시한다.
4. 룰 기반 근거가 높아도 물질명을 확정적으로 표현하지 않는다.
5. 전체 스펙트럼, 라이브러리 점수 또는 QC 정보가 없으면 한계를 명시한다.

출력 형식은 JSON 객체 하나이며 키는 정확히 다음 세 개이다.

- `summary`: 고객 보고서용 요약 정확히 3문장
- `narrative`: 주요 근거와 해석에 대한 보조 설명, 4문장 이내
- `caption`: 발표자료용 한 문장 캡션

## 8. 성공 응답

### 8.1 응답: `200 OK`

```json
{
  "id": "chatcmpl-0f5c9d9b",
  "object": "chat.completion",
  "created": 1781412000,
  "model": "gemma4-e4b",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "{\"summary\":\"요약 문장 1. 요약 문장 2. 요약 문장 3.\",\"narrative\":\"보조 설명입니다.\",\"caption\":\"발표자료용 캡션입니다.\"}"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 420,
    "total_tokens": 1620
  }
}
```

worker가 필수로 사용하는 값은 `choices[0].message.content`이다. 이 값은
비어 있지 않은 문자열이어야 하며, JSON 객체로 파싱했을 때 요청한 슬롯 중
하나 이상의 문자열 값을 포함해야 한다.

## 9. 컨텍스트 제한

모델의 입력과 출력 합계는 `8192` tokens를 초과할 수 없다. worker는 LLM 호출
전에 구조화 분석 근거 JSON 문자열 길이를 `RIST_LLM_MAX_INPUT_CHARS`
기본값 `200000`으로 제한한다. 분석 근거가 이 제한을 초과하면
`LLM_INPUT_TOO_LARGE`로 처리하고 규칙 기반 기본 문안을 사용한다.

vLLM이 HTTP `400`과 함께 `maximum context length` 오류를 반환한 경우에는
`LLM_CONTEXT_LENGTH_EXCEEDED`로 처리한다. 이 오류는 자동 재시도하지 않으며,
worker는 규칙 기반 기본 문안으로 보고서를 완성한다.

## 10. 오류 처리

### 10.1 vLLM 오류 응답 예시

```json
{
  "error": {
    "message": "This model's maximum context length is 8192 tokens...",
    "type": "BadRequestError",
    "code": 400
  }
}
```

### 10.2 Edge worker 오류 코드

| 코드 | 발생 조건 | 재시도 |
|---|---|---:|
| `LLM_MODELS_TIMEOUT` | 모델 목록 조회 시간 초과 | Y |
| `LLM_CONNECTION_FAILED` | LLM 서버 연결 실패 | Y |
| `LLM_MODELS_HTTP_ERROR` | `/v1/models` HTTP 오류 | 5xx만 Y |
| `LLM_MODELS_RESPONSE_INVALID` | 모델 목록 응답 형식 오류 | N |
| `LLM_MODEL_NOT_FOUND` | 설정 모델이 목록에 없음 | N |
| `LLM_TIMEOUT` | 채팅 완료 요청 시간 초과 | Y |
| `LLM_HTTP_ERROR` | 일반 HTTP 오류 | 일부 상태만 Y |
| `LLM_CONTEXT_LENGTH_EXCEEDED` | vLLM 컨텍스트 길이 초과 | N |
| `LLM_RESPONSE_INVALID` | OpenAI 호환 응답 형식이 아님 | N |
| `LLM_RESPONSE_EMPTY` | 생성 문안이 비어 있음 | N |
| `LLM_INPUT_TOO_LARGE` | 분석 JSON 문자 수 제한 초과 | N |

`LLM_HTTP_ERROR` 중 HTTP `408`, `429`, `500`, `502`, `503`, `504`는
재시도 가능한 오류로 분류한다.

보고서 worker는 위 LLM 오류를 작업 실패로 처리하지 않는다. `report.json`의
`llm.error`에 오류를 기록하고 규칙 기반 기본 문안으로 보고서를 완성한다.

## 11. 로그 및 결과 파일

작업별 저장 위치는 다음과 같다.

```text
{jobRoot}/
  processed/
    analysis-result.json
    spectrum.png
  logs/
    llm-request.json
    llm-response.json
  report/
    report.json
    report.md
```

- `llm-request.json`: LLM 슬롯 주석 요청. 이미지 Base64 본문은 마스킹한다.
- `llm-response.json`: 로컬 LLM의 전체 JSON 응답이다. LLM 단계가 실패하면
  생성되지 않을 수 있다.
- `report.json`: 규칙 기반 섹션과 LLM 보조 슬롯 적용 결과를 담은 구조화 보고서이다.
- `report.md`: `report.json`을 사람이 읽는 Markdown 형식으로 렌더링한 보고서이다.
- 원본 실험 bundle은 LLM에 직접 전달하지 않는다.
- LLM은 보고서 전체를 생성하지 않고 `summary`, `narrative`, `caption` 슬롯만
  보조 작성한다. LLM 실패 시에도 규칙 기반 기본 문안으로 보고서는 완성된다.

## 12. 상태 점검

FastAPI를 통해 LLM 연결과 모델 설정을 확인한다.

```http
GET /health/llm
Host: 127.0.0.1:8000
```

성공 응답 예시:

```json
{
  "status": "ok",
  "baseUrl": "http://127.0.0.1:8001",
  "model": "gemma4-e4b",
  "maxModelLength": 8192,
  "temperature": 0.1,
  "maxTokens": 1200,
  "visionEnabled": true
}
```

운영 점검 명령:

```bash
curl http://127.0.0.1:8001/v1/models
curl http://127.0.0.1:8000/health/llm
```

## 13. 변경 관리

- 모델 ID, 컨텍스트 길이 또는 vision 입력 형식이 변경되면 본 문서와 환경
  프로파일을 함께 변경한다.
- vLLM 업그레이드 시 `/v1/models`, `/v1/chat/completions`, 오류 응답 형식을
  회귀 테스트한다.
- 프롬프트 변경 시 기존 실험 결과를 사용해 단정 표현, 누락 정보, 출력 형식
  및 컨텍스트 사용량을 확인한다.
