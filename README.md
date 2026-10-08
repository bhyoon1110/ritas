# RITAS

실험 PC에서 생성된 결과 bundle을 Edge 서버로 전송하고, 장비별 전처리와
로컬 LLM을 이용해 실험 결과 보고서를 자동 생성하는 프로젝트이다.

## 구성

| 경로 | 설명 |
|---|---|
| `edge_api_server/` | 파일 수신, 보고서 요청, FT-IR/Raman/XRD 웹 분석 FastAPI 서버 |
| `common/` | 공통 환경 설정과 Plotly 스타일 모듈 |
| `config/` | 개발·운영 환경 프로파일 |
| `sune/` | FT-IR 전처리 및 보고서 생성 로직 |
| `lim/` | XRD 전처리 및 시각화 도구 |
| `ahn/` | AHN 실험장비 프로젝트 영역 |
| `rin/` | RIN 실험장비 프로젝트 영역 |

`RitasAxApp/`는 현재 실제 운영 대상이 아니며, 프로젝트 파악과 문서 기준에서는
`sune`, `lim`, `ahn`, `rin` 네 영역을 활성 프로젝트로 본다.

## API 명세

- [실험 PC - Edge 서버](EXPERIMENT_PC_EDGE_API.md)
- [Edge - Local Spring Boot 결과 전달](EDGE_SPRING_BOOT_API.md)
- [Edge 서버 - 로컬 LLM](EDGE_LOCAL_LLM_API.md)

## Edge API 실행

Python 3.11 이상이 필요하다.

```bash
cd edge_api_server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export RIST_ENV=development
python -m app.run
```

브라우저에서 `http://127.0.0.1:8000/ftir`을 열면 DPT 파일을 선택하거나
드래그 앤 드롭해 전처리·피크 분석 결과를 바로 확인할 수 있다. 재료별 피크
assignment 라이브러리는 여러 개를 동시에 선택할 수 있으며 화면에서 JSON/CSV
파일을 가져오거나 편집창에서 직접 생성·수정할 수 있다. 기본 설치 시 일반
작용기표와 함께 590개 기준 FT-IR 스펙트럼에서 추출한 카테고리별 marker 피크
라이브러리가 제공된다. 라이브러리 삭제는 기본 비활성이고
`RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED=true`로 설정한 뒤 Edge API
서비스를 재시작하면 사용할 수 있다.

보고서 worker는 별도 프로세스로 실행한다.

```bash
cd edge_api_server
source .venv/bin/activate
export RIST_ENV=development
python -m app.report_worker
```

자세한 내용은 [Edge API README](edge_api_server/README.md)를 참고한다.

## 웹 브라우저 지원 정책

- 주 지원·최적화 대상: 보안 지원 중인 OS의 최신 안정 버전 Chrome.
  Windows 업무 PC는 Windows 11을 권장한다.
- 최신 Edge는 주요 기능 호환 확인 대상으로 유지한다. 브라우저 이름만으로
  다른 브라우저를 강제 차단하지 않는다.
- 모바일의 기존 반응형·터치 동작은 유지하며 Android Chrome과 iOS Safari/Chrome의
  실제 업로드·스크롤·다운로드 동작은 별도로 검증한다.
- Windows XP·Windows 7·IE·구형 Edge는 정식 웹 지원 대상에서 제외한다.
  구형 장비 PC는 데이터 수집, 별도 최신 PC는 분석·보고서 검토·SSO 인증을 담당한다.

로그인·가입과 첫 화면에 정책을 표시한다. 네 실험 화면과 계정·운영·보고서 검토
화면은 알려진 구형 환경 또는 필수 기능 누락을 감지하면 안내를 표시한다.
안내는 경고일 뿐 인증·권한을 바꾸거나 API/C# 전송·보고서 다운로드를 차단하지 않는다.
경고가 없다고 최신 버전 또는 모든 기능의 검증 완료를 보장하는 것은 아니다.

`/login`과 `/signup`은 JavaScript 없이 동작하는 HTML POST 양식이다. 구형
브라우저에서도 서버가 입력 검증과 오류 안내를 처리하며, 쿠키와 동일 출처 요청
확인이 필요하다. 기존 JSON 인증 API와 가입 승인·프로젝트 권한·SSO 정책은 유지한다.

이 호환성 범위는 로그인·회원가입이며, 분석·그래프 편집·SSO·운영 화면 전체의
구형 IE 지원을 의미하지 않는다. 구형 OS의 TLS/인증서 오류는 별개이므로 HTTPS나
인증서 검증을 약화하지 않는다. 상세 배포·점검 방법은
[웹 인증 명세](documents/EDGE_WEB_AUTH.md#10-로그인회원가입의-구형-브라우저-호환성)를 참고한다.

구형 장비 PC의 전송 프로그램은 별도 검증 대상이다. 저장소의 C# 참조 예제는
`.NET 8`용이며 XP/Windows 7 운영용이 아니다. 실제 프로그램의 OS·런타임·TLS
지원 또는 최신 전송 중계 PC 사용 여부를 C# 개발자와 확정해야 한다.

## 저장소 제외 대상

실험 원본과 결과, 로컬 라이브러리 데이터, 모델 가중치, SQLite DB, 가상환경,
빌드 산출물 및 인증정보는 Git에서 관리하지 않는다.
