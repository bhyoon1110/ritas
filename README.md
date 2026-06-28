# RITAS

실험 PC에서 생성된 결과 bundle을 Edge 서버로 전송하고, 장비별 전처리와
로컬 LLM을 이용해 실험 결과 보고서를 자동 생성하는 프로젝트이다.

## 구성

| 경로 | 설명 |
|---|---|
| `edge_api_server/` | 파일 수신, 보고서 요청, FT-IR 웹 분석 FastAPI 서버 |
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
파일을 가져오거나 편집창에서 직접 생성·수정할 수 있다.

보고서 worker는 별도 프로세스로 실행한다.

```bash
cd edge_api_server
source .venv/bin/activate
export RIST_ENV=development
python -m app.report_worker
```

자세한 내용은 [Edge API README](edge_api_server/README.md)를 참고한다.

## 저장소 제외 대상

실험 원본과 결과, 로컬 라이브러리 데이터, 모델 가중치, SQLite DB, 가상환경,
빌드 산출물 및 인증정보는 Git에서 관리하지 않는다.
