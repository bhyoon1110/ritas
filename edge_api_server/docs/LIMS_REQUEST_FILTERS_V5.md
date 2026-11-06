# v5 의뢰·시험 조회

`GET /api/v1/requests`의 기본 조회 대상은 Edge에 동기화된 의뢰 목록(`source=cache`)이다. `source=lims`는 Python → 내부 Spring → LIMS 경로를 통해 최신 데이터를 조회한다. 실시간 조회 결과는 전체 동기화 테이블과 별도로 반환한다.

| 조건 | 저장된 의뢰 | LIMS 실시간 |
|---|---|---|
| `page`, `pageSize` | 1부터, 최대 1,000건 | 동일. Spring에서는 `size`로 전달 |
| `reqNumber`, `testMtdCode` | 일치 검색 | 일치 검색 |
| `fromDate`, `toDate` | 의뢰일, 양 끝 포함 | 동일 |
| `testChargerNo`, `testChargerId` | 미지원. 명확한 오류 반환 | 시험담당자 기준, 두 조건을 함께 쓰면 AND |
| `assetNumber` | 캐시에 자산번호가 없어 `LIMS_LOOKUP_REQUIRED` 반환 | LIMS 장비 자산번호 일치 검색 |
| `testState` | 시험 상태 코드 한 개 또는 쉼표로 구분한 여러 개 | 동일 |
| `experimentType` | 실험 종류에 해당하는 시험명/코드 키워드 검색 | 특정 장비 검색은 `assetNumber`, 시험방법 검색은 `testMtdCode` 사용 |
| `includeCompleted` | `false`면 미완료 시험, `true`면 전체 상태. 두 상태 조건을 모두 생략하면 미완료 시험 | `false`면 미완료 시험. 두 상태 조건을 모두 생략하면 전체 상태 |

모든 필터는 AND로 결합한다. 응답에 `totalCount`, `source`, 기존 `items` 구조가 포함된다. 실시간 조회의 `syncedAt`은 null이며, LIMS의 값 없는 필드도 null로 전달한다. 담당자 이름에서 ID를 추정하거나 잘못된 조건을 조용히 무시하지 않는다.

FT-IR·Raman·TEM·XRD의 의뢰 선택 화면과 보고서 편집기에서는 기본적으로 **미완료 시험**(`testState=1,3,5,7`)을 조회한다. 1은 시험의뢰대기, 3은 시험의뢰, 5는 시험접수, 7은 시험중이다. 시험취소(0), 시험완료(9), 전체 상태도 선택할 수 있다. 기준 필드는 각 시험의 `testState`다. `testState`를 지정하면 상위 의뢰의 상태명과 관계없이 지정한 시험 상태를 조회한다. 미완료 조건(`includeCompleted=false`)과 취소·완료 코드를 함께 지정하면 잘못된 조합으로 응답한다.

실시간 조회의 예는 `GET /api/v1/requests?source=lims&assetNumber=ASSET-001&testState=1,3,5,7`이다. `ASSET-001`에는 실제 LIMS 자산번호를 입력한다. 하나의 시험방법에 여러 장비가 대응할 수 있으므로 자산번호는 시험방법코드나 Edge 장비코드와 독립적으로 지정한다. 미완료 시험의 장비 조건은 해당 장비로 수행할 수 있는 시험을 뜻한다. 장비 조건과 담당자·의뢰번호·기간을 조합할 수 있다.

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

위존 회신에 따르면 의뢰조회와 두 새 조건은 개발 WAS에 반영되었다. 실제 연계 토큰과 내부 키를 설정하고 실시간 조회를 활성화한 뒤 현장에서 확인한다. 의뢰조회 응답의 `reqResultNo`는 별도 의뢰자 이메일 조회 API에 사용하는 의뢰 내부번호다.
