# Python Edge 코드 USB 배포

기존 `/home/rist/ritas`의 Python Edge 앱을 검증된 Git 커밋으로 업데이트합니다.
**Spring Boot r14/r15 배포본이 아닙니다.** Spring JAR·SSO 연계 토큰·SFTP 설정은 포함하지 않습니다.

패키지는 커밋된 Git 전체 이력 bundle, 배포 커밋 메타데이터, 설치기, SHA-256 목록을 포함합니다.
동일 Git 이력의 이전 커밋이면 fast-forward로 적용합니다.
이번 현장 확인 기준은 `43f8bcbfb`, Python 3.12.3이며 추적 코드 변경은 없었습니다.
서버의 추적 파일 미커밋 변경, 다른 브랜치, 분기 이력 또는 Python 의존성 정의 변경은 자동 덮어쓰지 않습니다.
무관한 미추적 시험 스크립트·무시 파일은 그대로 두며, 배포 경로와 충돌할 때만 중단합니다.
버전 미상 파일들을 일부만 덮어쓰는 방식은 사용하지 않습니다.

## 현장 실행

진행 중인 보고서 편집 내용을 저장하고 업로드·분석이 끝난 뒤 실행하세요.
패키지 ZIP과 `.sha256`을 USB로 `/home/rist`에 옮기고, 제공된 정확한 파일명으로 해시를 확인합니다.

```bash
cd /home/rist
sha256sum -c RIST-EDGE-배포커밋-USB.zip.sha256
unpack=$(mktemp -d "$HOME/rist-edge-unpack.XXXXXXXX")
python3 -m zipfile -e RIST-EDGE-배포커밋-USB.zip "$unpack"
cd "$unpack"/RIST-EDGE-*
sha256sum -c SHA256SUMS
python3 install_edge_update.py
python3 install_edge_update.py --apply
```

설치기는 `sudo` 없이 서비스 계정 `rist`로 실행합니다. 서비스 중지/시작 때만 sudo를 사용합니다.
기본 실행은 검사만 합니다. `--apply`가 검사, 백업 기록, 코드 업데이트와 서비스 재시작을 수행합니다.
설정 파일을 수정하거나 pip/apt·외부망·LIMS를 호출하지 않습니다.
`edge.env`의 40초 LIMS 대기 설정, 기존 `.venv`, DB 데이터, 보고서, 인증서, known_hosts를 유지합니다.
활성 API와 존재하는 worker의 실행 계정·작업 경로·Python 경로를 확인하고,
적용 중 둘을 중지한 다음 원래 실행 중이던 서비스만 다시 시작합니다.
worker 시작은 기존 unit의 Wants 설정에 따라 vLLM도 시작할 수 있습니다.

정상 완료 시 `EDGE_UPDATE_OK commit=...`가 출력됩니다.
SSO 관리에 필요한 새 테이블은 앱 시작 시 기존 스키마 초기화 경로에서 생성됩니다.
기존 테이블의 삭제/ALTER 또는 운영 데이터 복원 작업은 포함하지 않습니다.
최종 `/health` 성공은 SSO/LIMS/메일의 실사용 성공을 의미하지 않습니다.

## 적용 후 화면 확인

1. 브라우저의 이전 편집 내용을 저장한 뒤 새로고침합니다.
2. 관리자 회원 관리에서 SSO 유효기간을 변경하고, 본인/관리자 초기화를 확인합니다.
3. 의뢰 조회 기본값은 현재 실험입니다. 전체 의뢰를 선택하면 실험 자동 제한 없이 조회합니다.
4. 선택한 실제 의뢰번호·시험방법코드를 유지하고 보고서를 새로 생성합니다.
5. 메일 제목·본문 입력/수정 후 미리보기와 저장 결과를 확인합니다.
6. XRD 상 동정 표의 상 이름을 누르면 해당 상 분류로 이동합니다. 화학식·카드번호는 셀을 직접 편집하고, 상 이름 표시 문구는 `선택한 셀 글자 직접 편집`으로 수정합니다.
7. 분류 기준 문구가 PPT 미리보기와 내려받은 파일에 반영되는지 확인합니다. 긴 문구는 분류 기준 페이지로 이어집니다.
8. 전송·메일 큐 활성화와 실제 SFTP/문서 적재/메일 수신은 별도 현장 절차로 검증합니다.

## 실패 및 복원

코드 적용 전에 `../rist-edge-code-backups/<시각-임의값>/state.json`과 Git 백업 참조를 만듭니다.
이 백업은 **코드 커밋과 서비스 상태 복구 기록**이며 DB/OS 전체 백업이 아닙니다.
실패하면 설치한 커밋이 그대로이고 추가 파일 변경이 없는 경우에만 이전 코드를 자동 복원합니다.
사용자가 그 사이 코드를 바꾸거나 설정을 변경했다면 덮어쓰지 않고 중단합니다.
새로 생성된 SSO 설정 테이블은 이전 코드에서도 무해하므로 삭제하지 않습니다.

별도 수동 복원은 출력된 정확한 기록 경로로 실행합니다.

```bash
python3 install_edge_update.py --rollback /home/rist/rist-edge-code-backups/출력된-백업폴더
```

복원은 이후 작성한 보고서·DB 데이터·설정 변경을 되돌리지 않습니다.
SIGKILL·정전·디스크 고장까지 자동 복원을 보장하지 않으며, 복원 실패 시 기록을 보존해 확인합니다.

## 준비 PC에서 생성

관련 회귀 테스트와 `edge_api_server/browser_compat` 빌드를 마치고 변경을 커밋한 뒤:

```bash
python3 edge_api_server/deploy/package_edge_update.py /배포폴더/RIST-EDGE-배포커밋-USB.zip
```

생성기는 작업 폴더의 비추적 설정/데이터 대신 커밋된 소스만 사용합니다.
라이브 `.env`, 개인키, 가상환경 같은 금지 경로가 Git 이력에 있으면 패키징을 중단합니다.
SHA-256은 전송 손상 검사용이며 배포자 서명을 대신하지 않습니다.
자동 테스트와 Mac Chromium/Chrome 49 UA 검증은 현장 Windows/Supermium 실기 검증과 구분합니다.
