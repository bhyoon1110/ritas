# LibreOffice·한글 글꼴: 보고서 미리보기 폐쇄망 설치

## 대상과 포함 범위

MSI IPC EdgeXpert GB10 / DGX Spark의 **Ubuntu 24.04 기반 DGX OS, ARM64** 서버용이다.
실제 `/etc/os-release`와 `dpkg --print-architecture`가 맞지 않으면 중단한다.
Windows XP/7/10/11 실험 PC나 Supermium에는 설치하지 않는다.
시스템 Python 3.12(`/usr/bin/python3` 또는 `/usr/bin/python3.12`), APT/dpkg,
서비스 실행 계정(기본 `rist`)은 이미 있어야 한다. 기존 Edge 서버 복구용이며 OS 신규 설치용이 아니다.

- LibreOffice Impress **4:24.2.7-0ubuntu0.24.04.7** 및 PPT 변환 의존 패키지.
- 한글 **Noto Sans CJK KR·NanumGothic**, 라틴 문자 Liberation, fontconfig.
- PDF 검사 도구 `poppler-utils`(24.02.0-1ubuntu9.10).
- `.deb` **201개, 205,117,848 bytes(약 196 MiB)**. 필요한 패키지만 APT가 선택한다.
  빌드 시 비어 있는 dpkg 상태로 Depends/Pre-Depends 전체를 수집해, 준비 PC에
  이미 설치된 라이브러리가 패키지에서 빠지지 않도록 했다. 권장/제안 패키지는 제외한다.
- Java, OCR, GPU 드라이버, vLLM/모델, Python 앱 의존성, XRD matplotlib 복구는 포함 범위가 아니다.
- Microsoft 맑은 고딕 등 상용 폰트를 재배포하지 않는다. 없는 글꼴은 한글 대체 글꼴을 사용하므로
  Microsoft PowerPoint와 줄바꿈·글자 폭이 완전히 같다고 보장하지 않는다. 최종 보고서를 확인한다.

`offline/report-preview-noble-arm64/manifest.json`에 모든 패키지 버전·URL·크기·SHA-256이 있다.
다운로드 시 [Ubuntu 공식 저장소](https://ports.ubuntu.com/ubuntu-ports/)의 서명된 APT 색인과
SHA-256을 검증했으며 `provenance/`에 서명된 InRelease를 보존했다.
원본 deb를 수정하지 않았으며 라이선스/저작권 고지는 설치 후 `/usr/share/doc/<패키지>/copyright`에 있다.
관련 소스는 Ubuntu 소스 저장소 및 [Ubuntu 패키지 정보](https://packages.ubuntu.com/noble-updates/libreoffice-impress)에서 확인한다.
외부 재배포 시 각 패키지의 소스 제공 등 라이선스 의무도 확인한다.
로컬 SHA-256은 전송 손상 검사용이며 배포자 서명을 대신하지 않는다. 신뢰하는 저장소/배포자에게 받은 파일만 사용한다.

## USB 반입 및 설치

### 1. 패키지 옮기기와 계획 확인 (아직 설치하지 않음)

단독 USB 배포 파일은 `rist-report-preview-offline-ubuntu24.04-arm64.tar.gz`와 `.sha256`이다.
서버의 빈 작업 폴더에 두 파일을 복사하고 다음을 실행한다. 임의의 기존 폴더에 덮어 풀지 않는다.

```bash
sha256sum -c rist-report-preview-offline-ubuntu24.04-arm64.tar.gz.sha256
tar -xzf rist-report-preview-offline-ubuntu24.04-arm64.tar.gz
cd rist-report-preview-offline
bash deploy/install_offline_report_preview.sh plan
```

단독 패키지는 설치기·deb·검증용 PPT·이 안내서만 포함한다. **앱 코드/Git 이력·설정·DB·사용자 파일은 포함하지 않는다.**
최신 앱 코드는 기존 Git bundle 배포 절차로 별도 반입한다.
저장소에 이미 이 패키지를 반입했다면 `/home/rist/ritas`에서 다음 경로로 실행해도 된다.
이후 예제의 `deploy/`를 `edge_api_server/deploy/`로 바꾼다.

```bash
cd /home/rist/ritas
bash edge_api_server/deploy/install_offline_report_preview.sh plan
```

`plan`은 임시 APT 색인만 만들고 제거하며 OS 패키지·서비스·DB·가상환경을 변경하지 않는다.
전체 파일 무결성, OS/아키텍처, 기존 dpkg 미완료 여부, 새로 설치/변경될 패키지를 확인한다.
별도 인터넷 접근 없이 번들의 `file:` 저장소만 사용한다. 시스템 APT 저장소/설정은 수정하지 않는다.

### 2. 유지보수 시간에 서비스 중지

진행 중인 업로드·분석·보고서 전송이 끝났는지 먼저 확인한다. 서버 스냅샷/복구 백업을 준비한다.
현재 서비스 상태를 기록하고 API/worker를 중지한다. 서비스 이름/실행 계정이 다르면 실제 값으로 바꾼다.

```bash
systemctl show rist-edge-api.service rist-edge-worker.service -p User -p ExecStart -p ActiveState
sudo systemctl stop rist-edge-api.service rist-edge-worker.service
systemctl is-active rist-edge-api.service rist-edge-worker.service
```

두 서비스가 `inactive`인지 확인한다(정상 중지되어도 `is-active` 종료코드는 0이 아니다).
별도로 수동 실행한 API/worker나 Office 변환 작업도 종료해야 한다. 설치기가 대신 중지하지 않는다.
Spring·MariaDB·LLM을 직접 중지/재시작하는 스크립트는 아니다.

### 3. 설치와 한글 변환 검증

```bash
sudo bash deploy/install_offline_report_preview.sh install --confirm-services-stopped
sudo bash deploy/install_offline_report_preview.sh verify --service-user rist
```

- 설치 시 root 권한이 필요하다. **Python 가상환경에 pip로 설치하는 패키지가 아니다.**
- 기본 `rist`가 아닌 실행 계정이면 install과 verify 모두 `--service-user 실제계정`을 지정한다.
- 기존 패키지 버전 변경은 기본 차단한다. `plan`을 검토하고 필요한 변경만 승인할 때
  `install --confirm-services-stopped --allow-version-changes`로 실행한다.
- APT/dpkg, libc, systemd/udev 계열, NVIDIA/CUDA/커널 핵심 패키지의 기존 버전 변경은
  승인 옵션으로도 우회하지 않는다. 패키지 삭제·강제 다운그레이드·의존성 무시도 하지 않는다.
- 일반 OS 환경 차이 때문에 의존성이 충돌하면 중단하고 별도 검토한다. GPU 드라이버/OS를
  이 패키지에 맞춰 강제로 낮추지 않는다.
- 설치 전 최소 약 1.6 GiB 여유 공간을 확인한다. USB 압축 파일/압축 해제본 보관 공간은 별도다.
- 시스템 APT 설정 대신 임시 로컬 색인을 사용하고 실제 설치는 `--no-download --no-remove`로 실행한다.
  OS가 설치/변경하는 파일과 폰트 캐시는 업데이트되지만 앱 `.env`, DB, 보고서, `.venv`는 건드리지 않는다.
- 검증은 서비스 계정으로 Noto/나눔/맑은 고딕 대체 글꼴이 포함된 PPT를 PDF로 변환하고
  한글·숫자 추출과 실제 한글 폰트 임베딩을 확인한다. 검증용 프로필/파일은 임시 폴더에만 생성한다.
- 이미 충족된 패키지는 재설치하지 않고 변환 검증을 다시 수행한다.

### 4. 검증 성공 후 서비스 재개 및 실제 보고서 확인

```bash
sudo systemctl start rist-edge-api.service rist-edge-worker.service
systemctl status rist-edge-api.service rist-edge-worker.service --no-pager
```

유지보수 전 실행 중이었던 서비스만 다시 시작한다. 기존 worker의 `Wants=rist-vllm.service` 설정으로
worker 시작 시 vLLM도 기동될 수 있다. 설치기는 서비스 설정/자동 시작을 변경하지 않는다.
브라우저에서 TEM 보고서를 새로 생성하여 페이지별 미리보기, 한글, 표/이미지, 수정 저장,
PDF/PPT 다운로드를 확인한다. 기존 실패 보고서는 화면의 재시도 기능으로 다시 검증한다.
FTIR/Raman도 동일한 서버 변환 도구를 쓰므로 표본 보고서를 확인한다.
SSO/LIMS 실제 전송, DB 연계, Supermium 144의 OS별 실기 검증은 별도다.

`RIST_PPTX_TO_PDF_CONVERTER`에 기존의 잘못된 경로가 명시되어 있다면 설치만으로 해결되지 않는다.
이번 패키지는 `/usr/bin/soffice`를 제공한다. 실제 서비스 환경의 변환기 경로,
`FONTCONFIG_FILE`/`FONTCONFIG_PATH`/`RIST_PDF_FONT_PATH` 같은 사용자 지정 글꼴 설정을 확인한다.
설치기는 환경설정을 자동 덮어쓰지 않는다. 계정/서비스 제한 및 저장소 쓰기 권한도 별도 확인한다.

## 실패 시

설치 직전 dpkg 상태, 계획, manifest 및 설치/실패 로그를
`/var/backups/rist-report-preview/install-*/`(root 전용)에 보존한다.
이 기록은 **전체 OS 백업 또는 자동 롤백 파일이 아니다**. 설치 도중 정전/디스크 오류/패키지 스크립트 실패로
부분 설치 상태가 될 수 있다. 실패하면 서비스를 재개하지 말고 로그와 `sudo dpkg --audit`를 확인한다.
임의의 `apt --fix-broken`, 강제 dpkg, 패키지 전체 삭제로 우회하지 않는다.
필요하면 사전에 확보한 서버 스냅샷 또는 관리자의 패키지 복구 절차를 사용한다.

## 검증 범위와 재현 (개발자용)

인터넷을 차단한 Ubuntu 24.04 ARM64 컨테이너에서 Office/한글 글꼴이 없는 상태의
`plan → install → verify → 재설치`, 비-root 서비스 계정 변환, 계획 단계의 dpkg 상태 불변,
서비스 중지 확인 없이 설치 차단, dpkg audit를 검증했다.
별도로 합성 TEM 4페이지 PPT를 변환하여 한글·표·POSCO 배경·RIST 로고를 확인한다.
**운영 EdgeXpert에 실제 설치한 것은 아니며**, 서버별 기존 패키지/권한/환경설정 차이는 현장 확인 대상이다.

```bash
# 준비 PC에서 이미지 만들 때만 인터넷 사용 (운영 서버에서 실행하지 않음)
docker build --platform linux/arm64 -t rist-preview-offline-check \
  -f edge_api_server/deploy/tests/Dockerfile.offline-report-preview .
mkdir -p /tmp/rist-preview-test-results
docker run --rm --platform linux/arm64 --network none \
  --mount type=bind,src="$PWD/edge_api_server/deploy",dst=/src,readonly \
  --mount type=bind,src="$PWD/edge_api_server/deploy/offline/report-preview-noble-arm64",dst=/bundle,readonly \
  --mount type=bind,src=/tmp/rist-preview-test-results,dst=/out \
  rist-preview-offline-check /usr/bin/python3.12 -I /src/tests/check_offline_report_preview.py
```

새 버전 준비는 위 이미지의 **새 일회용 컨테이너에서만** 인터넷을 허용하고
`/usr/bin/python3.12 -I /src/build_report_preview_bundle.py /out/new-bundle`을 실행한다.
원본 Ubuntu 색인 서명·패키지 해시 확인 후 반드시 네트워크 차단 설치 테스트를 다시 수행한다.
기존 번들을 덮어쓰지 않으며 버전 변경 시 manifest와 검증 기록을 함께 갱신한다.

단독 USB 아카이브 재생성 (기존 출력 파일 덮어쓰지 않음):

```bash
mkdir -p documents/generated
python3.12 edge_api_server/deploy/package_report_preview.py \
  documents/generated/rist-report-preview-offline-ubuntu24.04-arm64.tar.gz
```

아카이브 생성 후 `.sha256`도 같이 전달한다. Git에는 설치기와 deb 원본을 포함하고,
중복 압축 배포본은 `documents/generated/`에 생성하여 Git에서 제외한다.
