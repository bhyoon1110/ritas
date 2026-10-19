#!/usr/bin/env bash
# Standalone orchestrator for the two verified USB archives. No network downloads.
# Run as the Edge service/venv owner, NOT with sudo. Only OS installation uses sudo.
set -Eeuo pipefail
umask 077

usage() {
    printf '%s\n' \
      'RIST XRD + PPT 미리보기 오프라인 통합 설치' \
      '사용: bash install_offline_reports.sh /압축파일/폴더 [옵션]' \
      '  기본: 무결성·환경·Git·설치 계획 검사만 (코드/패키지/서비스 변경 없음)' \
      '  --install                       확인 문구 입력 후 설치' \
      '  --project-root DIR              기본 /home/rist/ritas' \
      '  --allow-python-version-changes  검토한 Python 패키지 버전 변경 허용' \
      '  --allow-os-version-changes      검토한 일반 OS 패키지 버전 변경 허용' \
      '필요 파일: rist-xrd-offline-41d3c3d.zip' \
      '           rist-report-preview-offline-ubuntu24.04-arm64.tar.gz' \
      'Ubuntu 24.04 ARM64 / 기존 Python 3.12 venv / systemd 전용.' \
      '최신 앱 코드는 유지합니다. 포함 bundle은 41d3c3d까지만 업데이트합니다.' \
      '서비스: rist-edge-api.service, rist-edge-worker.service(있을 경우).' \
      '별도 실행 API/worker/Office 작업은 직접 종료하고 OS 백업을 확보하세요.' \
      '로그·압축 해제본은 출력된 임시 작업 폴더에 보존합니다. 자동 OS 롤백은 없습니다.'
}

fail() { printf '중단: %s\n' "$*" >&2; exit 1; }
note() { printf '\n%s\n' "$*"; }

reset_options() {
    R_PACKAGE_DIR='' R_PROJECT=/home/rist/ritas R_INSTALL=0
    R_PY_CHANGES=0 R_OS_CHANGES=0 R_STAGE='' R_PHASE=검사 R_TOUCHED=0
    R_SERVICES=() R_STATES=() R_START=()
    R_BUNDLE_COMMIT=41d3c3db3cf11fecd0a57765682ef02cef8d2ea8
}

parse_options() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h) usage; exit 0 ;;
            --install) R_INSTALL=1; shift ;;
            --allow-python-version-changes) R_PY_CHANGES=1; shift ;;
            --allow-os-version-changes) R_OS_CHANGES=1; shift ;;
            --project-root)
                [[ $# -ge 2 && -n $2 && $2 != --* ]] || fail '--project-root 경로가 필요합니다.'
                R_PROJECT=$2; shift 2 ;;
            --*) fail "알 수 없는 옵션: $1" ;;
            *) [[ -z $R_PACKAGE_DIR ]] || fail '압축파일 폴더는 하나만 지정하세요.'
                R_PACKAGE_DIR=$1; shift ;;
        esac
    done
    [[ -n $R_PACKAGE_DIR ]] || fail '압축파일 폴더를 지정하세요. --help 참조.'
}

on_exit() {
    local result=$?
    trap - EXIT
    if [[ $result -ne 0 ]]; then
        printf '\n실패 단계: %s (종료 코드 %s)\n' "$R_PHASE" "$result" >&2
        if [[ $R_TOUCHED -eq 1 ]]; then
            printf '%s\n' '서비스 상태를 직접 확인하세요. 실패한 설치를 무시하고 시작하지 마세요.' \
              '자동으로 코드를 되돌리거나 서비스를 재시작하지 않습니다. 재시작 단계 실패 시 일부만 실행 중일 수 있습니다.' >&2
        fi
    fi
    [[ -z $R_STAGE ]] || printf '로그·작업 파일 보존: %s\n' "$R_STAGE"
    exit "$result"
}

check_host() {
    [[ $EUID -ne 0 ]] || fail 'sudo 없이 서비스 계정(기본 rist)으로 실행하세요.'
    local tool
    for tool in git unzip tar sha256sum systemctl sudo flock tee mktemp id; do
        command -v "$tool" >/dev/null || fail "필수 명령이 없습니다: $tool"
    done
    /usr/bin/python3 -I -c 'import platform, subprocess, sys
r=platform.freedesktop_os_release()
assert r.get("ID")=="ubuntu" and r.get("VERSION_ID")=="24.04", "Ubuntu 24.04 전용입니다."
assert platform.machine()=="aarch64", "ARM64(aarch64) 전용입니다."
assert subprocess.check_output(["/usr/bin/dpkg","--print-architecture"],text=True).strip()=="arm64"
print("OS 확인: Ubuntu 24.04 ARM64")'
    [[ -d /run/systemd/system ]] || fail 'systemd 운영 서버에서 실행하세요.'
    R_PROJECT=$(cd -- "$R_PROJECT" && pwd -P)
    R_PACKAGE_DIR=$(cd -- "$R_PACKAGE_DIR" && pwd -P)
    R_PYTHON="$R_PROJECT/.venv/bin/python"
    [[ -x $R_PYTHON && -O $R_PROJECT/.venv/pyvenv.cfg ]] || fail '서비스 가상환경 또는 소유자가 다릅니다.'
    "$R_PYTHON" -I -c 'import sys; assert sys.prefix!=sys.base_prefix and sys.version_info[:2]==(3,12), "서비스 Python 3.12 venv가 필요합니다."; print("서비스 Python:",sys.executable)'
}

check_repository() {
    [[ $(git -C "$R_PROJECT" rev-parse --show-toplevel) == "$R_PROJECT" ]] || fail '저장소 루트를 지정하세요.'
    [[ $(git -C "$R_PROJECT" branch --show-current) == main ]] || fail 'main 브랜치가 아닙니다.'
    [[ -z $(git -C "$R_PROJECT" status --porcelain --untracked-files=normal) ]] || fail '커밋되지 않은 파일이 있습니다. 보존·검토 후 다시 실행하세요.'
}

service_property() { systemctl show "$1" --property="$2" --value; }

inspect_services() {
    local unit load state owner executable
    R_SERVICES=() R_STATES=() R_START=()
    R_USER=$(id -un)
    for unit in rist-edge-api.service rist-edge-worker.service; do
        load=$(service_property "$unit" LoadState)
        if [[ $unit == rist-edge-worker.service && $load == not-found ]]; then
            note 'worker 서비스 없음: API 서비스만 관리합니다.'; continue
        fi
        [[ $load == loaded ]] || fail "$unit 로드 상태: $load"
        state=$(service_property "$unit" ActiveState)
        [[ $state == active || $state == inactive ]] || fail "$unit 상태가 $state 입니다. 먼저 점검하세요."
        owner=$(service_property "$unit" User)
        [[ $owner == "$R_USER" ]] || fail "$unit 실행 계정($owner)과 현재 계정($R_USER)이 다릅니다."
        executable=$(service_property "$unit" ExecStart)
        [[ $executable == *"path=$R_PYTHON ;"* ]] || fail "$unit ExecStart가 $R_PYTHON 직접 실행 방식이 아닙니다. 실제 경로를 점검하세요."
        R_SERVICES+=("$unit"); R_STATES+=("$state")
        [[ $state != active ]] || R_START+=("$unit")
        printf '%s: %s / 계정 %s / %s\n' "$unit" "$state" "$owner" "$R_PYTHON"
    done
}

check_sha() {
    local file=$1 expected=$2 actual
    [[ -f $file && ! -L $file ]] || fail "압축파일이 없거나 링크입니다: $file"
    actual=$(sha256sum -- "$file"); actual=${actual%% *}
    [[ $actual == "$expected" ]] || fail "배포본 SHA-256 불일치: $file (다른 버전 또는 손상 파일)"
}

prepare_archives() {
    local xrd="$R_PACKAGE_DIR/rist-xrd-offline-41d3c3d.zip"
    local preview="$R_PACKAGE_DIR/rist-report-preview-offline-ubuntu24.04-arm64.tar.gz"
    # Pinned hashes of the exact archives distributed to the user. Not a signature.
    # Verify BOTH archives before extracting or running anything from either one.
    check_sha "$xrd" e2f34f5781c9d66987f8c0a45ebdaef464227fd82c0364c7ce51bd96e136ba70
    check_sha "$preview" 701cfc21c27d806611b2255aedbe41db5fa4d3ac2ba04b82737e0352bd384a0b
    unzip -q "$xrd" -d "$R_STAGE/xrd"
    tar --no-same-owner --no-same-permissions -xzf "$preview" -C "$R_STAGE"
    (cd "$R_STAGE/xrd" && sha256sum -c SHA256SUMS)
    R_XRD="$R_STAGE/xrd/deploy/install_offline_xrd.sh"
    R_PREVIEW="$R_STAGE/rist-report-preview-offline/deploy/install_offline_report_preview.sh"
    R_BUNDLE="$R_STAGE/xrd/rist-main.bundle"
    [[ -f $R_XRD && -f $R_PREVIEW ]] || fail '설치기가 없습니다.'
}

plan_code() {
    R_ORIGINAL_HEAD=$(git -C "$R_PROJECT" rev-parse HEAD)
    git -C "$R_PROJECT" bundle verify "$R_BUNDLE"
    [[ $(git bundle list-heads "$R_BUNDLE" refs/heads/main) == "$R_BUNDLE_COMMIT refs/heads/main" ]] || fail '예상한 bundle 커밋이 아닙니다.'
    # Resolve ancestry in temporary storage: never fetch/reset the live repo in plan mode.
    git clone --quiet --bare --shared "$R_PROJECT" "$R_STAGE/compare.git"
    git --git-dir="$R_STAGE/compare.git" fetch --quiet --no-tags "$R_BUNDLE" main:refs/heads/rist-offline-bundle
    if git --git-dir="$R_STAGE/compare.git" merge-base --is-ancestor "$R_BUNDLE_COMMIT" "$R_ORIGINAL_HEAD"; then
        R_UPDATE=0; note "현재 코드 유지: $R_ORIGINAL_HEAD (bundle보다 같거나 최신)"
    elif git --git-dir="$R_STAGE/compare.git" merge-base --is-ancestor "$R_ORIGINAL_HEAD" "$R_BUNDLE_COMMIT"; then
        R_UPDATE=1; note "코드 업데이트 예정: $R_ORIGINAL_HEAD → $R_BUNDLE_COMMIT"
    else
        fail 'Git 이력이 분기되었습니다. 자동 병합/강제 초기화하지 않습니다.'
    fi
}

xrd() { RIST_OFFLINE_PYTHON="$R_PYTHON" bash "$R_XRD" "$@" --project-root "$R_PROJECT"; }
preview_plan() { bash "$R_PREVIEW" plan --service-user "$R_USER"; }
preview_install() {
    # This packaged helper creates a smoke PPT as root, then reads it as the
    # service user. Do not pass our private-log umask 077 to that child process.
    sudo bash -c 'umask 022; exec bash "$@"' rist-preview "$R_PREVIEW" "$@" --service-user "$R_USER"
}

apply_changes() {
    local confirmation i unit
    local py_options=(install --confirm-services-stopped) os_options=(install --confirm-services-stopped)
    [[ $R_PY_CHANGES -eq 0 ]] || py_options+=(--allow-version-changes)
    [[ $R_OS_CHANGES -eq 0 ]] || os_options+=(--allow-version-changes)
    note '업로드·분석·전송이 끝났고 수동 API/worker/Office도 중지했는지 확인하세요.'
    note 'OS 백업을 확보해야 합니다. LibreOffice 설치 실패는 자동 OS 롤백되지 않습니다.'
    read -r -p '설치하려면 INSTALL 입력 (그 외 취소): ' confirmation || fail '확인 입력이 없습니다.'
    [[ $confirmation == INSTALL ]] || fail '설치를 취소했습니다. 서비스·코드는 변경하지 않았습니다.'
    sudo -v
    check_repository
    [[ $(git -C "$R_PROJECT" rev-parse HEAD) == "$R_ORIGINAL_HEAD" ]] || fail '검사 후 코드가 변경되었습니다. 다시 검사하세요.'
    for ((i=0; i<${#R_SERVICES[@]}; i++)); do
        [[ $(service_property "${R_SERVICES[$i]}" ActiveState) == "${R_STATES[$i]}" ]] || fail '검사 후 서비스 상태가 바뀌었습니다. 다시 검사하세요.'
    done
    R_PHASE=서비스중지 R_TOUCHED=1
    sudo systemctl stop "${R_SERVICES[@]}"
    for unit in "${R_SERVICES[@]}"; do
        [[ $(service_property "$unit" ActiveState) == inactive ]] || fail "$unit 중지를 확인하지 못했습니다."
    done
    R_PHASE=코드적용
    if [[ $R_UPDATE -eq 1 ]]; then
        git -C "$R_PROJECT" pull --ff-only "$R_BUNDLE" main
        [[ $(git -C "$R_PROJECT" rev-parse HEAD) == "$R_BUNDLE_COMMIT" ]] || fail '적용 커밋이 예상과 다릅니다.'
    fi
    R_PHASE=XRD설치
    xrd check
    xrd "${py_options[@]}"
    xrd verify
    R_PHASE=LibreOffice설치
    preview_install "${os_options[@]}"
    R_PHASE=최종검증
    preview_install verify
    xrd verify
    R_PHASE=서비스재시작
    if [[ ${#R_START[@]} -gt 0 ]]; then
        sudo systemctl start "${R_START[@]}"
        for unit in "${R_START[@]}"; do
            [[ $(service_property "$unit" ActiveState) == active ]] || fail "$unit 재시작 실패. journalctl 로그를 확인하세요."
        done
    fi
    R_PHASE=완료
    note '설치·검증 완료. 원래 실행 중이던 서비스만 재시작했습니다.'
    git -C "$R_PROJECT" log -1 --oneline
    systemctl show "${R_SERVICES[@]}" --property=Id,ActiveState,SubState
    note '브라우저에서 XRD/TEM 미리보기·한글·PPT/PDF와 FTIR/Raman 표본 보고서를 확인하세요.'
}

main() {
    reset_options; parse_options "$@"
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    check_host
    check_repository
    local git_dir
    git_dir=$(git -C "$R_PROJECT" rev-parse --absolute-git-dir)
    [[ ! -L $git_dir/rist-offline-reports.lock ]] || fail '안전하지 않은 설치 잠금 파일입니다.'
    exec 9>"$git_dir/rist-offline-reports.lock"
    flock -n 9 || fail '이 저장소의 다른 통합 설치가 진행 중입니다.'
    R_STAGE=$(mktemp -d "${TMPDIR:-/tmp}/rist-offline-reports.XXXXXX")
    exec > >(tee -a "$R_STAGE/install.log") 2>&1
    note "작업 폴더: $R_STAGE"
    inspect_services
    prepare_archives
    plan_code
    if [[ -f $R_PROJECT/edge_api_server/app/xrd_workbench/preflight.py ]]; then
        xrd check
    else
        [[ $R_UPDATE -eq 1 ]] || fail 'preflight.py가 없습니다. 코드 상태를 확인하세요.'
        note '기존 코드에 preflight.py 없음: bundle 코드 적용 직후 XRD 검사를 수행합니다.'
    fi
    preview_plan
    if [[ $R_INSTALL -eq 0 ]]; then
        note '검사 완료. 코드·OS 패키지·가상환경·서비스는 변경하지 않았습니다.'
        note '계획을 검토한 뒤 같은 명령에 --install을 붙여 실행하세요.'
        return
    fi
    apply_changes
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
