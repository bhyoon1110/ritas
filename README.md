# RITAS

실험 PC에서 생성된 결과 bundle을 Edge 서버로 전송하고, 장비별 전처리와
로컬 LLM을 이용해 실험 결과 보고서를 자동 생성하는 프로젝트이다.

## 구성

| 경로 | 설명 |
|---|---|
| `edge_api_server/` | 실험 PC 파일 수신 및 보고서 생성 요청 FastAPI 서버 |
| `common/` | 공통 환경 설정과 Plotly 스타일 모듈 |
| `config/` | 개발·운영 환경 프로파일 |
| `sune/` | FT-IR 전처리 및 보고서 생성 로직 |
| `lim/` | 실험장비 전처리 및 보고서 생성 로직 |
| `ahn/` | 실험장비 프로젝트 영역 |
| `RitasAxApp/` | 기존 Android 앱 프로젝트. 실제 배포 사용 여부 검토 중 |

## API 명세

- [실험 PC - Edge 서버](EXPERIMENT_PC_EDGE_API.md)
- [Edge 서버 - Spring Boot](EDGE_SPRING_BOOT_API.md)
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
