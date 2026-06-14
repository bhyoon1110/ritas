# Environment Configuration

공통 환경은 `RIST_ENV` 값으로 선택한다.

```bash
export RIST_ENV=development
# 또는
export RIST_ENV=production
```

프로파일 파일:

- `environments/development.env`
- `environments/production.env`

운영체제 환경 변수는 프로파일 파일보다 우선한다. 예를 들어 운영 서버에서
LLM 모델명만 바꾸려면 다음처럼 설정한다.

```bash
export RIST_ENV=production
export LOCAL_LLM_MODEL=gemma4-e4b
```

현재 로컬 LLM 기본값은 `http://127.0.0.1:8001`, 모델
`gemma4-e4b`, 컨텍스트 길이 `8192`, 출력 `max_tokens=1200`,
`temperature=0.1`이다. `/v1/models`에서 모델 존재 여부를 확인하고
`processed` 폴더의 분석 이미지를 최대 3개까지 vision 입력으로 사용할 수
있다. 관련 값은 각 프로파일의 `LOCAL_LLM_*` 항목에서 관리한다.

기본 설정 디렉터리는 프로젝트 루트의 `config/environments`이다. 배포 위치가
다르면 `RIST_CONFIG_DIR`에 프로파일 파일이 있는 디렉터리를 지정한다.

```bash
export RIST_CONFIG_DIR=/opt/rist/config/environments
```
