# RIST Edge XRD/TEM C# 참조 클라이언트

별도 C# 전송 프로그램 개발자가 `EXPERIMENT_PC_EDGE_API.md` 계약을 그대로
확인할 수 있는 .NET 8 콘솔 참조 구현이다. 외부 NuGet 패키지를 사용하지 않는다.

이 참조 예제는 Windows XP/Windows 7 운영용 클라이언트가 아니다. 실제 구형
장비 프로그램의 OS·.NET 런타임·TLS 지원은 C# 개발자가 별도 확인해야 한다.
호환되는 전송 프로그램 또는 최신 전송 중계 PC 사용을 결정하되, HTTPS 인증서
검증을 해제하거나 SSO 인증을 우회하지 않는다. 보고서 검토·전송 승인은 보안
지원 중인 OS의 최신 Chrome에서 수행하고, 최신 Edge도 호환 확인 대상으로 둔다.
실제 구형 PC용 전송 프로그램은 검토 URL 자동 열기를 피하고, 반환된 `reviewUrl`을
최신 업무 PC에서 열도록 구성할 수 있다. 이 참조 예제의 `--open-review` 옵션은
.NET 8을 지원하는 환경에서만 사용한다. 외부 실제 전송 프로그램을 이 예제로
자동 대체하지 않는다.
참고: [.NET의 Windows 지원 범위](https://learn.microsoft.com/en-us/dotnet/core/install/windows).

이 예제의 범위는 다음과 같다.

1. LIMS 의뢰 목록 조회
2. 로컬 XRD/TEM bundle의 상대 경로·크기·SHA-256 계산
3. Edge 작업 등록과 파일별 multipart 업로드
4. 서버 파일 목록 재조회와 로컬 무결성 대조
5. 업로드 완료 확정
6. XRD HTML 또는 TEM PPTX 보고서 생성 요청
7. 완료 상태 폴링과 `reviewUrl` 출력

SSO ID/PW 입력과 `/api/v1/reports/{reportId}/send` 호출은 의도적으로 포함하지
않는다. 보고서 검토와 LIMS 전송 승인은 출력된 `reviewUrl`을 로그인된 브라우저에서
사용자가 직접 수행한다.

## 빌드

```bash
dotnet build edge_api_server/examples/csharp-edge-client/Rist.EdgeClient.csproj \
  --configuration Release
```

## 의뢰 목록 조회

```bash
dotnet run \
  --project edge_api_server/examples/csharp-edge-client/Rist.EdgeClient.csproj \
  -- requests \
  --base-url https://edge.example \
  --analysis-type XRD
```

`--include-completed`를 추가하면 완료된 의뢰도 포함한다. `--base-url` 대신
`RIST_EDGE_BASE_URL` 환경변수를 사용할 수 있다.

## 로컬 bundle 사전 확인

```bash
dotnet run \
  --project edge_api_server/examples/csharp-edge-client/Rist.EdgeClient.csproj \
  -- manifest \
  --input /data/xrd/request-001
```

폴더를 지정하면 하위 파일의 상대 경로를 보존한다. ZIP 하나를 지정하는 방식도
지원한다. `.DS_Store`와 `Thumbs.db`는 전송 대상에서 제외한다.

## XRD 전체 흐름

```bash
dotnet run \
  --project edge_api_server/examples/csharp-edge-client/Rist.EdgeClient.csproj \
  -- submit \
  --base-url https://edge.example \
  --analysis-type XRD \
  --input /data/xrd/request-001 \
  --request-number 2026M00001 \
  --experiment-code A23141 \
  --equipment-code XRD-PC-01 \
  --operator-id employee01 \
  --include-raw \
  --open-review
```

XRD 보고서는 `reportFormats: ["HTML"]`로 요청한다.

## TEM 전체 흐름

```bash
dotnet run \
  --project edge_api_server/examples/csharp-edge-client/Rist.EdgeClient.csproj \
  -- submit \
  --base-url https://edge.example \
  --analysis-type TEM \
  --input /data/tem/request-002 \
  --request-number 2026M00002 \
  --experiment-code B54123 \
  --equipment-code TEM-PC-01 \
  --operator-id employee01 \
  --open-review
```

TEM 보고서는 `reportFormats: ["PPTX"]`로 요청한다.

## 동작 원칙

- 모든 요청에 새로운 `X-Request-Id`를 넣는다.
- 상태 변경 요청에는 요청 내용으로 만든 결정적 `Idempotency-Key`를 사용한다.
- 업로드 후 서버가 반환한 파일 목록과 로컬 크기·SHA-256을 다시 대조한다.
- `408`, `429`, `502`, `503`, `504`와 일시적 네트워크 오류만 같은 멱등키로
  최대 5회 재시도한다.
- `400`, `409`, `410`, `422`는 자동 재시도하지 않는다.
- 기본 단일 요청 제한시간은 300초, 보고서 폴링 제한시간은 900초이다.
- `--open-review`를 생략하면 브라우저를 열지 않고 검토 URL만 출력한다.

이 코드는 연동 계약의 참조 구현이다. 운영 프로그램의 UI, 장비 폴더 감시,
Windows 자격정보 보관, 자동 시작과 배포 정책은 C# 프로그램 쪽에서 별도로
구현한다.
