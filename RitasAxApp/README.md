# RITAS AX Tablet App

Jetpack Compose + Chaquopy 기반의 태블릿 앱 스캐폴드입니다.

## 포함 내용
- Compose 5단계 화면
- Chaquopy Python 전처리 모듈
- Retrofit 기반 Edge/LIMS 통신 계층
- 실험실 PC 파일 서버 연동 예시
- TEM, GD-MS, XRD, FT-IR, XPS용 전처리 엔트리 포인트

## 빌드 전 확인
1. Android Studio에서 프로젝트를 엽니다.
2. `app/build.gradle.kts`의 Edge URL과 필요 패키지를 조정합니다.
3. 빌드 머신에 Chaquopy가 사용할 Python 3.10이 설치되어 있어야 합니다.
4. 실험실 PC 파일 서버 주소를 앱 첫 화면에서 입력합니다.

## 현재 구현 범위
- 앱: 실제 화면/상태관리/파일복사/원격 다운로드/전처리 호출/보고서 요청/업로드/메일 발송 흐름
- Python: 샘플 전처리 로직
- 네트워크: 실제 연결 가능한 Retrofit 인터페이스

## 다음 연결 포인트
- Edge FastAPI 실서버와 API 스키마 맞추기
- 시험별 실무 규칙 반영
- PPT 미리보기/다운로드 UI 추가
- 로컬 DB(Room)로 작업 이력 저장


## Edge 서버 실행
```bash
cd server
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```
