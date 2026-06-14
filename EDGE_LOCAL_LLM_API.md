# Edge 분석 서버 - 로컬 LLM REST API 명세

## 1. 목적

Edge 분석 서버의 보고서 생성 worker가 같은 서버에서 실행되는 로컬 LLM을
호출하여 실험 결과 보고서 문안을 생성하기 위한 REST API를 정의한다.

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
| `LOCAL_LLM_CONTEXT_WINDOW` | `8192` | 컨텍스트 길이 |
| `LOCAL_LLM_CONTEXT_MARGIN` | `256` | 컨텍스트 안전 여유 |
| `LOCAL_LLM_VALIDATE_MODEL` | `true` | 모델 조회 및 검증 여부 |
| `LOCAL_LLM_INCLUDE_IMAGES` | `true` | 분석 이미지 전달 여부 |
| `LOCAL_LLM_MAX_IMAGES` | `3` | 요청당 최대 이미지 수 |
| `LOCAL_LLM_MAX_IMAGE_BYTES` | `2097152` | 이미지당 최대 크기 |

실행 환경 변수 `RIST_LLM_*`를 사용하면 worker 설정만 재정의할 수 있다.

## 4. 처리 순서

1. worker가 `/v1/models`를 호출한다.
2. 응답에 설정 모델 `gemma4-e4b`가 존재하는지 확인한다.
3. `processed` 폴더에서 구조화 분석 JSON과 허용된 이미지를 읽는다.
4. 입력, 이미지, 출력 토큰 및 안전 여유가 컨텍스트 한도 이내인지 확인한다.
5. `/v1/chat/completions`를 호출한다.
6. 응답 원문과 생성 문안을 작업 폴더에 저장한다.
7. API 또는 응답 오류가 발생하면 작업을 `FAILED`로 변경한다.

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
- `max_model_len`이 양의 정수이면 worker의 컨텍스트 계산에 사용한다.
- 설정값과 조회값 중 더 작은 컨텍스트 길이를 실제 한도로 사용한다.
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

`messages`에는 다음 두 메시지를 순서대로 전달한다.

| 순서 | role | content |
|---:|---|---|
| 1 | `system` | 보고서 작성 원칙과 출력 형식 |
| 2 | `user` | PK, 구조화 분석 JSON 및 선택적 분석 이미지 |

### 6.3 텍스트 요청 예시

```json
{
  "model": "gemma4-e4b",
  "messages": [
    {
      "role": "system",
      "content": "당신은 재료분석 실험실의 보고서 작성 보조자입니다. 제공된 분석 결과만 근거로 한국어 문안을 작성하세요."
    },
    {
      "role": "user",
      "content": "다음 JSON은 분석 프로그램이 생성한 입력입니다. 이 결과만 근거로 고객 보고서 문안을 작성하세요.\n\n{\"jobId\":\"e575b716-25d6-49c6-a7c0-3e2b7136fb2c\",\"pk\":{\"requestNumber\":\"REQ-2026-00123\",\"experimentCode\":\"FTIR\",\"equipmentCode\":\"FTIR-01\",\"operatorId\":\"user01\"},\"analysisResults\":[{\"relativePath\":\"analysis-result.json\",\"data\":{\"peaks\":[3400,1705,1240]}}]}"
    }
  ],
  "temperature": 0.1,
  "max_tokens": 1200
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
          "text": "구조화 분석 JSON과 첨부 이미지를 근거로 보고서 문안을 작성하세요."
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
  "max_tokens": 1200
}
```

- 이미지는 파일명 순으로 최대 3개까지 전달한다.
- 파일 크기가 2 MiB를 초과하면 해당 이미지는 제외한다.
- 이미지는 data URL 형태로 Base64 인코딩한다.
- 이미지에서 관찰한 내용과 JSON 근거를 구분하도록 지시한다.
- 이미지 하나만으로 물질 또는 원인을 확정하지 않는다.
- 요청 로그에는 Base64 본문을 저장하지 않는다.

## 7. 보고서 작성 원칙

모든 실험에 다음 원칙을 적용한다.

1. Python 전처리 결과와 구조화 분석 JSON만 근거로 사용한다.
2. 제공되지 않은 수치, 물질, 피크, 판정 또는 원인을 추측하지 않는다.
3. 단정 대신 `가능성`, `시사함`, `검토 필요` 중심으로 작성한다.
4. 단일 피크 또는 단일 이미지로 물질명이나 작용기를 확정하지 않는다.
5. QC flag, 데이터 누락, 불확실성 및 분석 한계를 명시한다.
6. 과도한 화학 구조 추정과 근거 없는 인과관계를 작성하지 않는다.

FT-IR에는 다음 원칙을 추가 적용한다.

1. Python 전처리와 룰 기반 판정 결과를 우선한다.
2. 라이브러리 매칭 결과와 룰 기반 판정 결과를 구분한다.
3. 매칭 점수가 임계값 미만이면 확정 동정이 아님을 명시한다.
4. 룰 기반 근거가 높아도 물질명을 확정적으로 표현하지 않는다.
5. 전체 스펙트럼, 라이브러리 점수 또는 QC 정보가 없으면 한계를 명시한다.

출력 형식은 다음과 같다.

1. 고객 보고서용 요약: 정확히 3문장
2. 주요 근거: bullet 4개 이내
3. 해석 한계 및 검토 필요사항: bullet 3개 이내
4. PPT caption: 1문장

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
        "content": "1. 고객 보고서용 요약\n..."
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

worker가 필수로 사용하는 값은
`choices[0].message.content`이다. 이 값은 비어 있지 않은 문자열이어야 한다.

## 9. 컨텍스트 제한

모델의 입력과 출력 합계는 `8192` tokens를 초과할 수 없다. worker는 LLM
호출 전에 다음 보수적 추정식을 적용한다.

```text
예상 입력 토큰 = ceil(UTF-8 텍스트 바이트 수 / 2)
예상 이미지 토큰 = 이미지 수 * 512

예상 총량 =
  예상 입력 토큰
  + 예상 이미지 토큰
  + max_tokens
  + context margin
```

기본값은 `max_tokens=1200`, `context margin=256`이다. 예상 총량이
컨텍스트 한도를 초과하면 API를 호출하지 않고
`LLM_CONTEXT_BUDGET_EXCEEDED`로 처리한다.

vLLM이 HTTP `400`과 함께 `maximum context length` 오류를 반환한 경우에는
`LLM_CONTEXT_LENGTH_EXCEEDED`로 처리한다. 이 오류는 자동 재시도하지 않는다.

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
| `LLM_CONTEXT_BUDGET_EXCEEDED` | 호출 전 컨텍스트 예상량 초과 | N |

`LLM_HTTP_ERROR` 중 HTTP `408`, `429`, `500`, `502`, `503`, `504`는
재시도 가능한 오류로 분류한다.

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
    report-draft.md
```

- `llm-request.json`: LLM 요청. 이미지 Base64 본문은 마스킹한다.
- `llm-response.json`: 로컬 LLM의 전체 JSON 응답이다.
- `report-draft.md`: `choices[0].message.content`에서 추출한 보고서 초안이다.
- 원본 실험 bundle은 LLM에 직접 전달하지 않는다.

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
