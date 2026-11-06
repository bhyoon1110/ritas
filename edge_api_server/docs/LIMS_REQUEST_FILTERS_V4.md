# v4 의뢰 조회

이 문서는 v4 기준이다. 2026년 11월 6일 반영한 자산번호·시험상태 필터와 화면 동작은 [v5 의뢰 조회](LIMS_REQUEST_FILTERS_V5.md)를 참조한다.

`GET /api/v1/requests`는 기존 저장 목록과 LIMS 실시간 조회를 구분한다. 기본값인 `source=cache`는 기존 실험별 조회 및 완료 건 제외 동작을 유지한다. `source=lims`는 Python → 내부 Spring → LIMS 조회이며 결과를 전체 동기화 테이블에 저장하거나 덮어쓰지 않는다.

| 조건 | 저장된 의뢰 | LIMS 실시간 |
|---|---|---|
| `page`, `pageSize` | 1부터, 최대 1,000건 | 동일. Spring에서는 `size`로 전달 |
| `reqNumber`, `testMtdCode` | 일치 검색 | 일치 검색 |
| `fromDate`, `toDate` | 의뢰일, 양 끝 포함 | 동일 |
| `testChargerNo`, `testChargerId` | 미지원. 명확한 오류 반환 | 시험담당자 기준, 두 조건을 함께 쓰면 AND |
| `experimentType` | 기존 실험명/코드 키워드 검색 | 미지원. 정확한 `testMtdCode` 사용 |
| `includeCompleted` | 기본 false | 상태 필터 미지원. 생략하거나 true만 허용 |

모든 필터는 AND로 결합한다. 응답에 `totalCount`, `source`, 기존 `items` 구조가 포함된다. 실시간 조회의 `syncedAt`은 null이며, LIMS의 값 없는 필드도 null로 전달한다. 담당자 이름에서 ID를 추정하거나 잘못된 조건을 조용히 무시하지 않는다.

실험 화면의 검색 조건에서 조회 대상, 정확한 시험방법코드, 의뢰번호, 기간, 담당자를 선택하고 이전/다음 페이지를 조회할 수 있다. 실험 화면의 실시간 조회는 다른 시험을 잘못 선택하지 않도록 시험방법코드를 입력해야 한다. API 자체는 v4대로 필터 없는 조회도 지원한다. 실험장비 ID와 상태 코드 필터는 v4에서 제공하지 않아 임의로 추가하지 않았다.

## 설정

Python:

```properties
RIST_LIMS_REQUESTS_ENABLED=true
RIST_LIMS_REQUESTS_URL=http://127.0.0.1:8888/api/lims/requests
RIST_SSO_LIMS_INTERNAL_KEY=<Spring과 동일한 내부 전용 키>
RIST_SSO_LIMS_ALLOW_LOOPBACK_HTTP=true
```

Spring:

```properties
RITASAX_LIMS_REQUEST_PROXY_ENABLED=true
RITASAX_LIMS_SSO_INTERNAL_KEY=<Python과 동일한 내부 전용 키>
```

내부 전용 키는 32~512자의 출력 가능한 ASCII 문자열이다. 외부 LIMS 토큰은 Spring에만 설정한다. HTTP는 명시적으로 허용한 루프백 IP에만 사용할 수 있다. 다른 서버의 Spring은 HTTPS와 신뢰할 CA 설정을 사용한다. 리다이렉트는 따르지 않으며 응답은 최대 4 MiB, 페이지당 최대 1,000건으로 제한한다.

기본 설정은 실시간 조회 비활성화다. 토큰과 LIMS 개발 서버의 의뢰조회 반영을 확인한 뒤 활성화한다. 의뢰조회 응답에는 수신자 이메일이 없으므로 이 기능이 보고서 To 주소를 해결하지는 않는다.
