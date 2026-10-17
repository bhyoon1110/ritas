# XRD 미리보기 matplotlib 누락: 폐쇄망 복구 패키지

## 범위 및 대상

이 패키지는 **이미 운영 중인 Edge 서버의 matplotlib 누락 복구용**이다.
`matplotlib==3.11.2`와 모든 전이 의존 wheel **11개, 38,775,980 bytes(약 37 MiB)**를 포함한다.
Python, pip, FastAPI, PDF/PPT 라이브러리, OCR, GPU 드라이버, 모델, OS 패키지의
전체 신규 설치용이 아니다. 기존 XRD PDF/PPT 라이브러리도 없으면 검증에서 중단한다.

- 대상: MSI IPC EdgeXpert GB10 / DGX Spark 계열의 **Linux aarch64, CPython 3.12, glibc 2.28 이상**.
- 하드웨어 이름만으로 OS/Python 버전을 확정하지 않는다. 설치 스크립트가 실제 환경을 검사한다.
- Windows 실험 PC/Supermium에는 설치하지 않는다. 서버의 실제 서비스 가상환경에만 설치한다.
- x86_64, macOS, Python 3.11/3.13, musl, 시스템 Python에서는 중단한다.
- `offline/xrd-cp312-linux-aarch64/manifest.json`에 공식 PyPI 파일 URL·크기·SHA-256을 기록했다.
  `requirements.lock`은 모든 버전과 해시를 고정한다. SHA-256은 전송 손상을 검출하는 수단이며
  서명이 아니므로 신뢰하는 저장소/배포자가 제공한 패키지만 사용한다.
- wheel 내부의 원본 라이선스/NOTICE를 그대로 보존했다. wheel을 재빌드하거나 수정하지 않았다.

포함 버전: matplotlib 3.11.2, numpy 2.2.6, contourpy 1.4.0, cycler 0.12.1,
fonttools 4.67.0, kiwisolver 1.5.1, packaging 26.3, pillow 13.0.0,
pyparsing 3.3.3, python-dateutil 2.9.0.post0, six 1.17.0.
NumPy는 기존 FTIR/Raman 프로젝트의 2.2.6 고정 버전을 유지한다.

## USB 배포 순서

아래는 사용자 `rist`, 저장소 `/home/rist/ritas`, 가상환경 `.venv` 기준이다.
서비스의 `ExecStart`가 다르면 실제 경로/서비스 이름으로 변경한다.
**`pip`/설치 스크립트에는 sudo를 사용하지 말고 가상환경 소유자로 실행한다.**

1. USB 패키지를 `/home/rist/rist-xrd-offline`에 풀었다고 가정한다.
   배포 ZIP에는 `rist-main.bundle`(main 전체 이력), 이 안내서, `deploy/`(설치기와 wheel),
   `COMMIT.txt`, `SHA256SUMS`가 있다. 먼저 손상 여부와 현재 배포 상태를 확인한다.

```bash
cd /home/rist/rist-xrd-offline
sha256sum -c SHA256SUMS
cd /home/rist/ritas
git status --short
git branch --show-current
git log -1 --oneline
git bundle verify /home/rist/rist-xrd-offline/rist-main.bundle
systemctl show rist-edge-api.service -p ExecStart
.venv/bin/python -c 'import platform,sys; print(platform.system(),platform.machine(),sys.version); print(sys.executable)'
```

`git status`에 수정/삭제/충돌이 있거나 main이 아니면 자동 덮어쓰기하지 않고 먼저 확인한다.
패키지 전체 이력이 있어 서버의 이전 커밋을 몰라도 적용할 수 있지만, 분기된 이력은
`--ff-only`가 중단한다. `reset --hard`로 우회하지 않는다.

2. 진행 중인 분석·업로드·전송이 끝난 유지보수 시간에 API/worker를 중지하고 코드를 적용한다.
   수동 실행된 API/worker가 있다면 그것도 종료해야 한다. 중지/시작 명령의 각 성공을 확인한다.

```bash
sudo systemctl stop rist-edge-api.service rist-edge-worker.service
systemctl is-active rist-edge-api.service rist-edge-worker.service
git pull --ff-only /home/rist/rist-xrd-offline/rist-main.bundle main
git log -1 --oneline
bash edge_api_server/deploy/install_offline_xrd.sh check
```

`is-active`는 두 서비스 모두 `inactive`여야 한다(정상 중지 시 명령 종료코드도 0이 아니다).
bundle에 `HEAD` 참조가 없으므로 **pull 명령 끝의 `main`을 생략하지 않는다**.
적용된 커밋이 `COMMIT.txt`와 일치하는지 확인한다. Git으로 wheel 파일도 옮겨지지만
가상환경에 설치되지는 않는다. 반드시 다음 단계를 실행한다.

3. `check`는 설치/서비스/DB 변경 없이 파일 무결성, 환경, 현재 `pip check`, 설치 계획을 검사한다.
   모두 성공하면 설치한다.

```bash
bash edge_api_server/deploy/install_offline_xrd.sh install --confirm-services-stopped
```

기존 패키지 버전 변경은 기본 차단한다. 출력된 `기존 -> 번들 버전` 목록을 검토하여
이 복구에 필요한 변경을 승인하는 경우에만 다음과 같이 재실행한다.

```bash
bash edge_api_server/deploy/install_offline_xrd.sh install --confirm-services-stopped --allow-version-changes
```

기존 의존성 충돌, 환경 불일치, 누락/손상 파일은 우회하지 말고 오류를 확인한다.
`--no-deps`, TLS 검증 해제, 시스템 pip로 대체 설치하지 않는다.
이 설치기는 네트워크를 사용하지 않으며 `--no-index --require-hashes`로 로컬 wheel만 사용한다.

4. 설치 성공 후 검증하고 **성공했을 때만** 서비스를 다시 시작한다.

```bash
bash edge_api_server/deploy/install_offline_xrd.sh verify
sudo systemctl start rist-edge-api.service rist-edge-worker.service
systemctl status rist-edge-api.service rist-edge-worker.service --no-pager
```

기존 worker 서비스의 `Wants=rist-vllm.service` 설정 때문에 worker 시작 시 vLLM도
기동될 수 있다. 설치기가 서비스 설정을 바꾸거나 Spring/DB/LLM을 직접 재시작하지는 않는다.
이후 기존 XRD 프로젝트의 보고서 편집을 열어 미리보기·PDF·PPTX·ZIP을 확인하고
FTIR/Raman/TEM의 기존 작업도 표본 확인한다. Python 라이브러리 검증만으로 현장 연계나
모든 브라우저의 실기 검증이 완료되는 것은 아니다.

## 다른 경로 / 단독 wheel 패키지

저장소에 최신 코드가 이미 있다면 USB의 `deploy/`에서도 설치기를 실행할 수 있다.
예시 경로를 실제 경로로 변경한다. 코드에 `app/xrd_workbench/preflight.py`가 없으면 먼저 코드를 업데이트한다.

```bash
RIST_OFFLINE_PYTHON=/home/rist/ritas/.venv/bin/python \
  bash /home/rist/rist-xrd-offline/deploy/install_offline_xrd.sh check \
  --project-root /home/rist/ritas
```

## 백업 / 실패 시 복원

- 실제 설치 전에 가상환경 전체를 같은 상위 폴더의 `.venv.xrd-backup-<임의값>/venv`에 복사한다.
  기존 패키지 버전 목록도 기록한다. 가상환경 크기의 2배+512 MiB 이상 여유 공간을 요구한다.
- 설치 또는 설치 후 `pip check`, PNG 생성, XRD preflight가 실패하면 실패 환경은
  `.venv.xrd-failed-<임의값>/venv`에 보존하고 원래 가상환경 경로로 백업을 복사해 복원한다.
- 백업이나 실패 환경을 자동 삭제하지 않는다. 복원도 실패했다면 서비스를 시작하지 말고
  출력된 백업 경로와 오류를 확인한다. 백업은 원래 `.venv` 경로로 복원해야 하며,
  백업 폴더의 Python을 운영 서비스 경로로 지정하지 않는다.
- 프로세스 강제 종료/SIGKILL·정전·디스크 장애까지 자동 복원을 보장할 수는 없다.
  그런 경우에도 백업을 유지하고 수동 복구 후 검증한다.
- 자동 복원은 **Python 가상환경만** 복원한다. 앞서 적용한 Git 코드를 되돌리지 않는다.
  서비스는 자동 시작하지 않으며 `.env`, DB, 사용자 데이터, 보고서, 인증/SSO/TLS 설정을 변경하지 않는다.
- 이미 번들 버전이 설치되어 있으면 재설치/추가 백업 없이 검증만 수행한다.
  모든 기능 확인 후 보존 정책에 따라 특정 백업을 별도로 정리한다.

## 검증 기록 및 재검증

인터넷이 차단된(`docker run --network none`) **Ubuntu 24.04 ARM64 / Python 3.12**에서:

- 빈 가상환경에 11개 wheel 전부 설치 + `pip check` + matplotlib import.
- matplotlib가 없는 기존 PDF/PPT 환경에서 `check → install → verify → 재실행` 성공.
- 실제 pip 설치 뒤 의도적으로 preflight를 실패시켜 원래 가상환경 복원/실패 환경 보존 확인.
- 안전장치 자동 테스트 **23 passed**: 손상·누락·환경 불일치·서비스 실행 중·버전 변경·공간 부족 등.
- 첨부 XRD 16개 파일/4시료로 HTML·PDF·PPTX·ZIP 생성 및 원본 TXT/PDF 10개의 바이트 보존 확인.

이는 서버 소프트웨어 환경의 검증이며, **운영 EdgeXpert 실기에서 설치/재시작한 결과는 아니다**.
브라우저 코드는 변경하지 않았다. DGX OS 전체, GPU/vLLM/OCR, 실제 SSO/LIMS/DB는 이 패키지의 검증 범위가 아니다.
별도로 기존 개발 Python 환경에서 XRD workbench 전체 Python 회귀 테스트 **494 passed**를 확인했다.

재현 가능한 격리 설치 테스트(준비 PC의 Docker 사용, 이미지 준비만 인터넷 필요):

```bash
docker build --platform linux/arm64 -t rist-xrd-offline-check \
  -f edge_api_server/deploy/tests/Dockerfile.offline-xrd .
docker run --rm --platform linux/arm64 --network none \
  --mount type=bind,src="$PWD",dst=/repo,readonly \
  rist-xrd-offline-check python3.12 /repo/edge_api_server/deploy/tests/check_offline_container.py
```

## wheel 재준비(개발자용)

같은 Linux ARM64/Python 3.12 준비 환경에서 해시 고정 목록으로 다운로드한다.
Mac 기본 Python으로 다운로드하면 Mac wheel이 섞일 수 있으므로 대상 환경을 사용한다.

```bash
python3.12 -m pip --isolated download --index-url https://pypi.org/simple \
  --only-binary=:all: --require-hashes \
  -r edge_api_server/deploy/offline/xrd-cp312-linux-aarch64/requirements.lock \
  --dest /tmp/rist-xrd-wheels-new
python3.12 edge_api_server/deploy/build_xrd_wheel_manifest.py \
  /tmp/rist-xrd-wheels-new /tmp/rist-xrd-bundle-new
```

생성기는 공식 PyPI 메타데이터와 각 wheel의 해시/크기를 대조하며 기존 출력 폴더를 덮어쓰지 않는다.
버전/대상을 바꾸면 설치기의 대상 검사·테스트·lock도 함께 검토하고 격리 설치 테스트를 다시 수행한다.
