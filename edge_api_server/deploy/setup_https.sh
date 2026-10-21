#!/usr/bin/env bash
# Offline Nginx HTTP/HTTPS provisioning for an existing Ubuntu/systemd RIST Edge.
# No package download, firewall mutation, SSO call, or client trust installation.
set -Eeuo pipefail
umask 077

usage() {
    cat <<'USAGE'
RIST Edge Nginx HTTP/HTTPS 설정 (Ubuntu, TLS 사용 시 OpenSSL 1.1.1 이상)

  bash setup_https.sh plan [옵션]             읽기 전용 계획 (기본 동작)
  sudo bash setup_https.sh csr [옵션]         Edge 개인키 + CSR 생성
  bash setup_https.sh init-ca --ca-dir DIR    별도 관리 PC에서 암호화된 내부 CA 생성
  bash setup_https.sh sign --ca-dir DIR --csr FILE --out FILE [옵션]
  sudo bash setup_https.sh apply --cert FILE --ca-cert FILE [옵션]
  sudo bash setup_https.sh apply --mode http [옵션]  인증서 없이 Nginx HTTP 적용
  sudo bash setup_https.sh apply --mode both --cert FILE --ca-cert FILE [옵션]
  sudo bash setup_https.sh check [옵션]       지정 모드의 Nginx/프록시 검사
  sudo bash setup_https.sh rollback --backup DIR

공통 옵션:
  --host HOST             IPv4 또는 DNS 이름 (기본 10.10.1.31)
  --project-root DIR      기존 저장소 (기본 /home/rist/ritas)
  --tls-dir DIR           서버 키/인증서 (기본 /etc/rist/tls)
  --nginx-config FILE     http 블록에서 include되는 파일
                         (기본 /etc/nginx/conf.d/rist-edge-https.conf)
  --backup-root DIR       비밀값 포함 백업 (기본 /var/backups/rist-https)
  --mode MODE             http, https, both (기본 https)
  --http-port PORT        Nginx 외부 HTTP 포트 (기본 80)
  --https-port PORT       기본 443
  --api-port PORT         서버 내부 HTTP 포트 (기본 8000)
  --key FILE             apply에서 가져올 비암호화 서버 키 (기본 TLS_DIR/edge.key)
  --ca-pass-file FILE     CA 암호 파일 (권한 600/400). 생략하면 OpenSSL이 직접 질문
  --cert-days DAYS        sign 인증서 유효기간 (기본 365, 31~825)
  --backup DIR           rollback 대상 (스크립트가 생성한 백업만 허용)
  --help

주의:
  apply는 API를 재시작하므로 작업이 없는 시간에 실행하세요. worker/Spring/DB는
  재시작하지 않습니다. 기존 edge.env는 실행(source)하지 않고 관리 키만 변경합니다.
  CA 개인키는 Edge에 복사하지 마세요. PC에는 client-ca.cer 공개 인증서만 배포합니다.
  init-ca/csr/sign은 기존 키/CSR/인증서를 덮어쓰지 않습니다.
USAGE
}

log() { printf '%s\n' "$*"; }
die() { printf '오류: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "필수 명령 없음: $1 (사내 패키지/USB로 설치하세요)"; }
require_root() { [[ $EUID -eq 0 ]] || die '이 동작은 sudo/root 권한이 필요합니다.'; }

reset_options() {
    R_ACTION=plan R_HOST=10.10.1.31 R_PROJECT=/home/rist/ritas
    R_TLS=/etc/rist/tls R_NGINX=/etc/nginx/conf.d/rist-edge-https.conf
    R_BACKUPS=/var/backups/rist-https R_HTTPS_PORT=443 R_API_PORT=8000
    R_MODE=https R_HTTP_PORT=80
    R_KEY='' R_CERT='' R_CA_CERT='' R_CA_DIR='' R_PASS_FILE=''
    R_CSR='' R_OUT='' R_DAYS=365 R_BACKUP='' R_TRANSACTION=0
}

parse_options() {
    if [[ $# -gt 0 && $1 != --* ]]; then R_ACTION=$1; shift; fi
    while [[ $# -gt 0 ]]; do
        case $1 in
            --help) usage; exit 0 ;;
            --host|--project-root|--tls-dir|--nginx-config|--backup-root|--mode|--http-port|--https-port|--api-port|--key|--cert|--ca-cert|--ca-dir|--ca-pass-file|--csr|--out|--cert-days|--backup)
                [[ $# -ge 2 && -n $2 && $2 != --* ]] || die "$1 값이 필요합니다."
                case $1 in
                    --host) R_HOST=$2 ;; --project-root) R_PROJECT=$2 ;;
                    --tls-dir) R_TLS=$2 ;; --nginx-config) R_NGINX=$2 ;;
                    --backup-root) R_BACKUPS=$2 ;; --https-port) R_HTTPS_PORT=$2 ;;
                    --mode) R_MODE=$2 ;; --http-port) R_HTTP_PORT=$2 ;;
                    --api-port) R_API_PORT=$2 ;; --key) R_KEY=$2 ;;
                    --cert) R_CERT=$2 ;; --ca-cert) R_CA_CERT=$2 ;;
                    --ca-dir) R_CA_DIR=$2 ;; --ca-pass-file) R_PASS_FILE=$2 ;;
                    --csr) R_CSR=$2 ;; --out) R_OUT=$2 ;;
                    --cert-days) R_DAYS=$2 ;; --backup) R_BACKUP=$2 ;;
                esac
                shift 2 ;;
            *) die "알 수 없는 옵션: $1" ;;
        esac
    done
    case $R_ACTION in plan|csr|init-ca|sign|apply|check|rollback) ;; *) die "알 수 없는 동작: $R_ACTION" ;; esac
}

# Keep all generated config values literal and reject broad or symlink targets.
safe_path() {
    local target=$1 part
    [[ $target =~ ^/[A-Za-z0-9_./-]+$ && $target != */ && $target != *//* && $target != */../* && $target != */./* && $target != */.. && $target != */. ]] || die "안전한 절대 경로가 아닙니다: $target"
    case $target in /|/etc|/var|/var/backups|/tmp|/home|/root|/usr|/opt|/etc/nginx|/etc/nginx/conf.d|/etc/ssl|/etc/ssl/certs|/etc/ssl/private|/etc/pki|/usr/local/share/ca-certificates) die "너무 넓은 대상 경로: $target" ;; esac
    part=$target
    while [[ $part != / ]]; do
        [[ ! -L $part ]] || die "심볼릭 링크 경로는 변경하지 않습니다: $part"
        part=$(dirname -- "$part")
    done
}

outside_git() {
    local part=$1
    [[ $part != "$R_PROJECT" && $part != "$R_PROJECT/"* ]] || die "개인키/백업은 프로젝트 밖에 보관하세요: $part"
    while [[ $part != / ]]; do
        [[ ! -e $part/.git ]] || die "개인키/백업은 Git 저장소 밖에 보관하세요: $1"
        part=$(dirname -- "$part")
    done
}

validate_options() {
    local octet label port target
    case $R_MODE in http|https|both) ;; *) die '--mode는 http, https, both 중 하나여야 합니다.' ;; esac
    [[ $R_HOST =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && R_HOST_KIND=ip || R_HOST_KIND=dns
    if [[ $R_HOST_KIND == ip ]]; then
        local IFS=.
        for octet in $R_HOST; do
            [[ ${#octet} -le 3 && ( $octet == 0 || $octet != 0* ) ]] || die 'IPv4 표기를 확인하세요.'
            (( 10#$octet <= 255 )) || die 'IPv4 범위를 확인하세요.'
        done
    else
        [[ ${#R_HOST} -le 253 && $R_HOST =~ ^[A-Za-z0-9.-]+$ && $R_HOST != *..* && $R_HOST != *\. ]] || die '호스트는 IPv4 또는 DNS 이름만 허용합니다.'
        local IFS=.
        for label in $R_HOST; do
            [[ ${#label} -le 63 && $label =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$ ]] || die 'DNS 이름을 확인하세요.'
        done
    fi
    for port in "$R_HTTP_PORT" "$R_HTTPS_PORT" "$R_API_PORT"; do
        if [[ ! $port =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then die '포트는 1~65535입니다.'; fi
    done
    [[ $R_MODE == http || $R_API_PORT != "$R_HTTPS_PORT" ]] || die 'HTTPS와 내부 HTTP 포트는 달라야 합니다.'
    [[ $R_MODE == https || $R_API_PORT != "$R_HTTP_PORT" ]] || die '외부 HTTP와 내부 API 포트는 달라야 합니다.'
    [[ $R_MODE != both || $R_HTTP_PORT != "$R_HTTPS_PORT" ]] || die 'HTTP와 HTTPS 포트는 달라야 합니다.'
    if [[ ! $R_DAYS =~ ^[1-9][0-9]{1,2}$ ]] || (( R_DAYS < 31 || R_DAYS > 825 )); then die '인증서 유효기간은 31~825일입니다.'; fi
    for target in "$R_PROJECT" "$R_TLS" "$R_NGINX" "$R_BACKUPS"; do safe_path "$target"; done
    for target in "$R_KEY" "$R_CERT" "$R_CA_CERT" "$R_CA_DIR" "$R_PASS_FILE" "$R_CSR" "$R_OUT" "$R_BACKUP"; do
        [[ -z $target ]] || safe_path "$target"
    done
    R_ENV=$R_PROJECT/edge.env
    [[ -n $R_KEY ]] || R_KEY=$R_TLS/edge.key
    R_URL=https://$R_HOST
    [[ $R_HTTPS_PORT == 443 ]] || R_URL+=:$R_HTTPS_PORT
    R_HTTP_URL=http://$R_HOST
    [[ $R_HTTP_PORT == 80 ]] || R_HTTP_URL+=:$R_HTTP_PORT
    [[ $R_MODE != http ]] || R_URL=$R_HTTP_URL
    R_SAN=DNS:$R_HOST
    [[ $R_HOST_KIND != ip ]] || R_SAN=IP:$R_HOST
}

crypto_prerequisites() {
    need openssl
    openssl req -help 2>&1 | grep -- '-addext' >/dev/null || die 'OpenSSL 1.1.1 이상이 필요합니다 (LibreSSL 제외).'
    if [[ -n $R_PASS_FILE ]]; then
        [[ -f $R_PASS_FILE && -r $R_PASS_FILE ]] || die 'CA 암호 파일을 읽을 수 없습니다.'
        local perms
        perms=$(stat -c '%a' "$R_PASS_FILE")
        [[ $perms == 600 || $perms == 400 ]] || die 'CA 암호 파일 권한은 600 또는 400이어야 합니다.'
    fi
}

private_directory_ok() {
    [[ ! -e $1 || ( -d $1 && $(stat -c '%u:%a' "$1") == "$EUID:700" ) ]] || die "기존 보관 폴더는 현재 사용자 소유·권한 700이어야 합니다 (자동 chmod하지 않음): $1"
}

new_directory() {
    safe_path "$1"; outside_git "$1"
    private_directory_ok "$1"
    [[ -d $1 ]] || install -d -m 700 -- "$1"
}

init_ca() {
    [[ -n $R_CA_DIR ]] || die '--ca-dir이 필요합니다.'
    crypto_prerequisites
    new_directory "$R_CA_DIR"
    safe_path "$R_CA_DIR/.rist-https.lock"
    exec 9>"$R_CA_DIR/.rist-https.lock"
    flock -n 9 || die '동일 CA에 대한 다른 작업이 실행 중입니다.'
    safe_path "$R_CA_DIR/root-ca.key"; safe_path "$R_CA_DIR/root-ca.crt"
    [[ ! -e $R_CA_DIR/root-ca.key && ! -e $R_CA_DIR/root-ca.crt ]] || die '기존 CA는 덮어쓰지 않습니다.'
    local stage
    stage=$(mktemp -d "$R_CA_DIR/.new-ca.XXXXXXXX")
    local -a pass_args=()
    [[ -z $R_PASS_FILE ]] || pass_args=(-passout "file:$R_PASS_FILE")
    openssl req -new -x509 -newkey rsa:3072 -sha256 -days 3650 \
        -subj '/CN=RIST Internal Root CA' \
        -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -keyout "$stage/root-ca.key" -out "$stage/root-ca.crt" "${pass_args[@]}"
    install -m 600 "$stage/root-ca.key" "$R_CA_DIR/root-ca.key"
    install -m 644 "$stage/root-ca.crt" "$R_CA_DIR/root-ca.crt"
    log "내부 CA 생성: $R_CA_DIR (CA 개인키는 이 관리 PC에만 보관)"
    openssl x509 -in "$R_CA_DIR/root-ca.crt" -noout -fingerprint -sha256
}

create_csr() {
    crypto_prerequisites
    new_directory "$R_TLS"
    safe_path "$R_TLS/.rist-https.lock"
    exec 9>"$R_TLS/.rist-https.lock"
    flock -n 9 || die '동일 인증서에 대한 다른 작업이 실행 중입니다.'
    safe_path "$R_TLS/edge.key"; safe_path "$R_TLS/edge.csr"
    [[ ! -e $R_TLS/edge.key && ! -e $R_TLS/edge.csr ]] || die '기존 개인키/CSR은 덮어쓰지 않습니다. 갱신은 새 TLS 디렉터리를 사용하세요.'
    local stage
    stage=$(mktemp -d "$R_TLS/.new-csr.XXXXXXXX")
    openssl req -new -newkey rsa:3072 -nodes -sha256 \
        -subj "/CN=$R_HOST" -addext "subjectAltName=$R_SAN" \
        -addext 'basicConstraints=critical,CA:FALSE' \
        -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
        -addext 'extendedKeyUsage=serverAuth' \
        -keyout "$stage/edge.key" -out "$stage/edge.csr"
    install -m 600 "$stage/edge.key" "$R_TLS/edge.key"
    install -m 644 "$stage/edge.csr" "$R_TLS/edge.csr"
    log "CSR 생성: $R_TLS/edge.csr"
    log 'CSR만 발급기관에 전달하세요. edge.key는 Edge 밖으로 전달하지 마세요.'
}

sign_csr() {
    [[ -n $R_CA_DIR && -n $R_CSR && -n $R_OUT ]] || die '--ca-dir, --csr, --out이 필요합니다.'
    crypto_prerequisites
    outside_git "$R_CA_DIR"
    safe_path "$R_CA_DIR/root-ca.crt"; safe_path "$R_CA_DIR/root-ca.key"
    [[ -f $R_CA_DIR/root-ca.crt && -f $R_CA_DIR/root-ca.key && -f $R_CSR ]] || die 'CA 또는 CSR 파일이 없습니다.'
    [[ $(stat -c '%u:%a' "$R_CA_DIR") == "$EUID:700" ]] || die 'CA 디렉터리는 현재 사용자 소유·권한 700이어야 합니다.'
    [[ $(stat -c '%u:%a' "$R_CA_DIR/root-ca.key") == "$EUID:600" ]] || die 'CA 개인키는 현재 사용자 소유·권한 600이어야 합니다.'
    if openssl pkey -in "$R_CA_DIR/root-ca.key" -passin pass: -noout >/dev/null 2>&1; then die '암호화된 CA 개인키만 사용합니다.'; fi
    openssl x509 -in "$R_CA_DIR/root-ca.crt" -noout -checkend "$((R_DAYS * 86400))" >/dev/null || die 'CA 유효기간이 발급할 서버 인증서보다 짧습니다.'
    [[ ! -e $R_OUT ]] || die '기존 인증서는 덮어쓰지 않습니다.'
    safe_path "$R_CA_DIR/.rist-https.lock"
    exec 9>"$R_CA_DIR/.rist-https.lock"
    flock -n 9 || die '동일 CA에 대한 다른 작업이 실행 중입니다.'
    local stage serial
    stage=$(mktemp -d "$R_CA_DIR/.sign.XXXXXXXX")
    openssl req -in "$R_CSR" -noout -verify
    printf '%s\n' 'basicConstraints=critical,CA:FALSE' \
        'keyUsage=critical,digitalSignature,keyEncipherment' \
        'extendedKeyUsage=serverAuth' "subjectAltName=$R_SAN" >"$stage/extensions.cnf"
    serial=$(openssl rand -hex 16)
    local -a pass_args=()
    [[ -z $R_PASS_FILE ]] || pass_args=(-passin "file:$R_PASS_FILE")
    openssl x509 -req -in "$R_CSR" -CA "$R_CA_DIR/root-ca.crt" \
        -CAkey "$R_CA_DIR/root-ca.key" "${pass_args[@]}" -set_serial "0x$serial" \
        -days "$R_DAYS" -sha256 -extfile "$stage/extensions.cnf" -out "$stage/edge.crt"
    install -m 644 "$stage/edge.crt" "$R_OUT"
    log "서버 인증서 발급: $R_OUT (대상 $R_HOST, $R_DAYS 일)"
    log 'Edge에는 서버 인증서와 root-ca.crt만 전달하세요. CA 개인키는 전달하지 마세요.'
}

validate_certificate() {
    local cert=$1 key=$2 ca=$3 stage=$4
    [[ -f $cert && -f $key && -f $ca ]] || die '서버 인증서, 서버 개인키, CA 인증서가 모두 필요합니다.'
    [[ $(awk '/-----BEGIN CERTIFICATE-----/ { n++ } END { print n+0 }' "$ca") == 1 ]] || die '--ca-cert에는 배포할 루트 CA 인증서 한 개만 지정하세요.'
    openssl x509 -in "$ca" -noout -text | grep 'CA:TRUE' >/dev/null || die 'CA 인증서에 CA:TRUE가 없습니다.'
    openssl verify -check_ss_sig -CAfile "$ca" "$ca" >/dev/null || die '루트 CA의 서명/기간 검증에 실패했습니다.'
    if openssl x509 -in "$cert" -noout -text | grep 'CA:TRUE' >/dev/null; then die '서버 인증서로 CA 인증서를 사용할 수 없습니다.'; fi
    openssl x509 -in "$cert" -noout -checkend 2592000 >/dev/null || die '서버 인증서가 만료되었거나 유효기간이 30일 미만입니다.'
    local san
    san=$(openssl x509 -in "$cert" -noout -ext subjectAltName)
    if [[ $R_HOST_KIND == ip ]]; then
        [[ $san == *'IP Address:'* ]] || die '인증서에 IP SAN이 없습니다.'
    else
        [[ $san == *'DNS:'* ]] || die '인증서에 DNS SAN이 없습니다.'
    fi
    local -a host_args=(-verify_hostname "$R_HOST")
    [[ $R_HOST_KIND != ip ]] || host_args=(-verify_ip "$R_HOST")
    openssl verify -purpose sslserver -CAfile "$ca" -untrusted "$cert" \
        "${host_args[@]}" "$cert" >/dev/null || die '인증서의 주소/기간/서버 용도/CA 체인 검증에 실패했습니다.'
    openssl pkey -in "$key" -passin pass: -pubout -out "$stage/key.pub" 2>/dev/null || die '자동 기동용 비암호화 서버 개인키가 필요합니다.'
    openssl x509 -in "$cert" -noout -pubkey >"$stage/cert.pub"
    cmp -s "$stage/key.pub" "$stage/cert.pub" || die '서버 인증서와 개인키가 일치하지 않습니다.'
}

managed_targets() {
    R_TARGETS=("$R_ENV" "$R_NGINX" "$R_TLS/edge.key" "$R_TLS/edge-chain.pem" "$R_TLS/ca.pem" "$R_TLS/client-ca.cer")
    local target
    for target in "${R_TARGETS[@]}"; do
        safe_path "$target"
        [[ ! -e $target || -f $target ]] || die "일반 파일이 아닌 대상: $target"
    done
}

server_prerequisites() {
    require_root
    local command
    for command in nginx curl systemctl ss flock awk install stat mktemp cmp; do need "$command"; done
    if [[ $R_MODE != http ]]; then crypto_prerequisites; fi
    [[ -f $R_ENV ]] || die "기존 환경 파일이 없습니다: $R_ENV (먼저 Edge를 설치하세요)"
    outside_git "$R_TLS"; outside_git "$R_BACKUPS"
    private_directory_ok "$R_TLS"; private_directory_ok "$R_BACKUPS"
    [[ $R_TLS != "$R_PROJECT" && $R_TLS != "$R_PROJECT/"* && $R_BACKUPS != "$R_PROJECT" && $R_BACKUPS != "$R_PROJECT/"* ]] || die '개인키/백업은 프로젝트 밖에 보관하세요.'
    managed_targets
    local env_files
    env_files=$(systemctl show rist-edge-api.service -p EnvironmentFiles --value)
    [[ $env_files == *"$R_ENV "* || $env_files == "$R_ENV" ]] || die 'rist-edge-api.service가 지정한 edge.env를 읽도록 구성되어 있지 않습니다.'
}

check_port_conflicts() {
    local listeners dump port
    local -a ports=()
    [[ $R_MODE == https ]] || ports+=("$R_HTTP_PORT")
    [[ $R_MODE == http ]] || ports+=("$R_HTTPS_PORT")
    local listener
    dump=$(nginx -T 2>/dev/null) || die '기존 Nginx 설정 검사 실패. 기존 설정을 먼저 확인하세요.'
    for port in "${ports[@]}"; do
        listeners=$(ss -H -ltnp "sport = :$port")
        while IFS= read -r listener; do
            [[ -z $listener || $listener == *'"nginx"'* ]] || die "외부 포트 $port 를 다른 프로세스가 사용 중입니다."
        done <<<"$listeners"
        if printf '%s\n' "$dump" | awk -v ours="$R_NGINX:" -v port="$port" '
            /^# configuration file / { file=$4 }
            $1 == "listen" { address=$2; sub(/;$/, "", address)
                if (file != ours && (address == port || address ~ (":" port "$"))) bad=1
            }
            END { exit !bad }
        '; then die "다른 Nginx 설정이 포트 $port 를 사용합니다. 자동으로 덮어쓰지 않습니다."; fi
    done
}

snapshot() {
    new_directory "$R_BACKUPS"
    R_BACKUP=$(mktemp -d "$R_BACKUPS/backup.XXXXXXXX")
    printf '%s\n' 'RIST_HTTPS_BACKUP_V2' "$R_PROJECT" "$R_TLS" "$R_NGINX" \
        "$R_HTTPS_PORT" "$R_API_PORT" "$R_HOST" "$R_MODE" "$R_HTTP_PORT" >"$R_BACKUP/metadata"
    if systemctl is-active --quiet nginx; then printf '1\n'; else printf '0\n'; fi >"$R_BACKUP/nginx-active"
    if systemctl is-enabled --quiet nginx; then printf '1\n'; else printf '0\n'; fi >"$R_BACKUP/nginx-enabled"
    local i
    for i in "${!R_TARGETS[@]}"; do
        if [[ -e ${R_TARGETS[$i]} ]]; then cp -a -- "${R_TARGETS[$i]}" "$R_BACKUP/$i.before";
        else : >"$R_BACKUP/$i.absent"; fi
    done
}

atomic_install() {
    local source=$1 target=$2 mode=$3 staged parent
    parent=$(dirname -- "$target")
    # GNU install -d also chmods EXISTING directories to its default 755.
    # Never relax the private TLS directory or change the project directory.
    [[ -d $parent ]] || install -d -- "$parent"
    staged=$(mktemp "$parent/.rist-https.XXXXXXXX")
    install -m "$mode" -- "$source" "$staged"
    if [[ $target == "$R_ENV" ]]; then
        chmod --reference="$target" "$staged"
        chown --reference="$target" "$staged"
    fi
    mv -f -- "$staged" "$target"
}

write_env() {
    # Do NOT source an operator-owned EnvironmentFile or expose its contents.
    local secure=true allow_http=false
    if [[ $R_MODE != https ]]; then secure=false; allow_http=true; fi
    awk -v url="$R_URL" -v port="$R_API_PORT" -v secure="$secure" -v allow_http="$allow_http" '
        BEGIN {
            value["RIST_EDGE_PUBLIC_BASE_URL"]=url
            value["RIST_AUTH_COOKIE_SECURE"]=secure
            value["RIST_SSO_ALLOW_HTTP"]=allow_http
            value["RIST_AUTH_ENABLED"]="true"
            value["RIST_EDGE_BIND_HOST"]="127.0.0.1"
            value["RIST_EDGE_API_PORT"]=port
            value["FORWARDED_ALLOW_IPS"]="127.0.0.1"
        }
        {
            key=$0; sub(/^[ \t]*/, "", key); sub(/[ \t]*=.*/, "", key)
            if (key in value) { if (!seen[key]++) print key "=" value[key]; next }
            print
        }
        END { for (key in value) if (!seen[key]) print key "=" value[key] }
    ' "$R_ENV" >"$R_BACKUP/edge.env.new"
}

write_nginx() {
    cat >"$R_BACKUP/nginx.new" <<NGINX
# Managed by RIST deploy/setup_https.sh. Other sites are never overwritten.
server {
NGINX
    if [[ $R_MODE != https ]]; then
        printf '    listen %s;\n' "$R_HTTP_PORT" >>"$R_BACKUP/nginx.new"
    fi
    if [[ $R_MODE != http ]]; then
        cat >>"$R_BACKUP/nginx.new" <<NGINX
    listen $R_HTTPS_PORT ssl;
    ssl_certificate "$R_TLS/edge-chain.pem";
    ssl_certificate_key "$R_TLS/edge.key";
    ssl_protocols TLSv1.2 TLSv1.3;
NGINX
    fi
    cat >>"$R_BACKUP/nginx.new" <<NGINX
    server_name $R_HOST;
    client_max_body_size 2100m;
    client_body_timeout 900s;

    # Same-host Spring calls 127.0.0.1:8000, not this public proxy.
    location ~ ^/api/v1/reports/[^/]+/regenerate/?\$ { return 403; }

    location / {
        proxy_pass http://127.0.0.1:$R_API_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$http_host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_request_buffering off;
        proxy_send_timeout 900s;
        proxy_read_timeout 900s;
    }
}
NGINX
}

restore_files() {
    local backup=$1 i failed=0
    for i in "${!R_TARGETS[@]}"; do
        if [[ -f $backup/$i.before ]]; then
            cp -a -- "$backup/$i.before" "${R_TARGETS[$i]}" || failed=1
        elif [[ -f $backup/$i.absent ]]; then
            # Exact files recorded as absent before this transaction, never directories.
            if [[ -f ${R_TARGETS[$i]} && ! -L ${R_TARGETS[$i]} ]]; then rm -f -- "${R_TARGETS[$i]}" || failed=1; fi
        else failed=1; fi
    done
    systemctl restart rist-edge-api.service || failed=1
    if [[ $(<"$backup/nginx-active") == 1 ]]; then
        if nginx -t; then
            if systemctl is-active --quiet nginx; then systemctl reload nginx || failed=1;
            else systemctl start nginx || failed=1; fi
        else failed=1; fi
    else systemctl stop nginx || failed=1; fi
    if [[ $(<"$backup/nginx-enabled") == 0 ]]; then systemctl disable nginx || failed=1;
    else systemctl enable nginx || failed=1; fi
    return "$failed"
}

on_exit() {
    local result=$?
    trap - EXIT INT TERM
    if [[ $R_TRANSACTION == 1 ]]; then
        log "적용 실패: 이전 설정으로 복원합니다. 백업: $R_BACKUP" >&2
        if ! restore_files "$R_BACKUP"; then
            log "자동 복원 일부 실패. 백업을 보존했습니다: $R_BACKUP" >&2
        else log '이전 파일/서비스 상태 복원 완료. 신규 설정 파일은 제거되었습니다.' >&2; fi
    fi
    exit "$result"
}

verify_frontend() {
    local url=$1 port=$2 ca=$3 _attempt ready=0 code
    local -a options=(--resolve "$R_HOST:$port:127.0.0.1")
    [[ $url != https://* ]] || options+=(--cacert "$ca")
    for _attempt in {1..15}; do
        if curl --fail --silent --show-error --noproxy '*' --connect-timeout 2 --max-time 5 \
            "${options[@]}" "$url/health" >/dev/null 2>&1; then
            ready=1; break
        fi
        sleep 1
    done
    [[ $ready == 1 ]] || die "실제 Nginx/서버 주소/health 검사에 실패했습니다: $url"
    # Invalid, unauthenticated payload cannot trigger a real SSO call. 426 means
    # proxy scheme or HTTP permission is wrong; do not accept health alone.
    code=$(curl --silent --show-error --noproxy '*' --connect-timeout 2 --max-time 10 \
        "${options[@]}" \
        -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
        --data '{}' "$url/api/v1/auth/sso/verify")
    case $code in 401|403|422) ;; *) die "SSO 프록시 전달 검사 실패: $url (HTTP $code)." ;; esac
}

verify_live() {
    local ca=$1 listeners
    if [[ $R_MODE != https ]]; then verify_frontend "$R_HTTP_URL" "$R_HTTP_PORT" "$ca"; fi
    if [[ $R_MODE != http ]]; then verify_frontend "$R_URL" "$R_HTTPS_PORT" "$ca"; fi
    listeners=$(ss -H -ltn "sport = :$R_API_PORT")
    printf '%s\n' "$listeners" | awk -v address="127.0.0.1:$R_API_PORT" '
        NF { count++; if ($4 != address) bad=1 } END { exit !(count && !bad) }
    ' || die 'Python HTTP 포트가 127.0.0.1에만 바인딩되지 않았습니다.'
}

apply_https() {
    [[ $R_MODE == http || ( -n $R_CERT && -n $R_CA_CERT ) ]] || die 'HTTPS/both apply에는 --cert와 --ca-cert가 필요합니다.'
    server_prerequisites
    systemctl is-active --quiet rist-edge-api.service || die '기존 Edge API가 실행 중이어야 합니다.'
    new_directory "$R_BACKUPS"
    safe_path "$R_BACKUPS/.rist-https.lock"
    exec 9>"$R_BACKUPS/.rist-https.lock"
    flock -n 9 || die '다른 HTTPS 설정 작업이 실행 중입니다.'
    if [[ -f $R_NGINX ]]; then
        grep -Fx '# Managed by RIST deploy/setup_https.sh. Other sites are never overwritten.' "$R_NGINX" >/dev/null || die '기존 Nginx 파일은 이 스크립트가 관리하는 파일이 아닙니다. 다른 --nginx-config 경로를 지정하세요.'
    fi
    check_port_conflicts
    local validation
    if [[ $R_MODE != http ]]; then
        validation=$(mktemp -d "$R_BACKUPS/validate.XXXXXXXX")
        validate_certificate "$R_CERT" "$R_KEY" "$R_CA_CERT" "$validation"
    fi
    snapshot
    log "백업: $R_BACKUP (DB 비밀번호 등 비밀값 포함, 외부에 공유하지 마세요)"
    R_TRANSACTION=1
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    if [[ $R_MODE != http ]]; then
        new_directory "$R_TLS"
        # Stage sources first: --key/--cert may already name managed destination files.
        install -m 600 "$R_KEY" "$R_BACKUP/key.new"
        install -m 644 "$R_CERT" "$R_BACKUP/chain.new"
        install -m 644 "$R_CA_CERT" "$R_BACKUP/ca.new"
        openssl x509 -in "$R_BACKUP/ca.new" -outform DER -out "$R_BACKUP/client-ca.cer.new"
        atomic_install "$R_BACKUP/key.new" "$R_TLS/edge.key" 600
        atomic_install "$R_BACKUP/chain.new" "$R_TLS/edge-chain.pem" 644
        atomic_install "$R_BACKUP/ca.new" "$R_TLS/ca.pem" 644
        atomic_install "$R_BACKUP/client-ca.cer.new" "$R_TLS/client-ca.cer" 644
    fi
    write_env; write_nginx
    atomic_install "$R_BACKUP/edge.env.new" "$R_ENV" 640
    atomic_install "$R_BACKUP/nginx.new" "$R_NGINX" 644
    nginx -t
    local dump
    dump=$(nginx -T 2>/dev/null)
    [[ $dump == *"# configuration file $R_NGINX:"* ]] || die '생성한 파일이 nginx.conf에서 include되지 않습니다.'
    systemctl restart rist-edge-api.service
    if systemctl is-active --quiet nginx; then systemctl reload nginx;
    else systemctl start nginx; fi
    verify_live "$R_TLS/ca.pem"
    systemctl enable nginx
    R_TRANSACTION=0
    trap - EXIT INT TERM
    log "Nginx $R_MODE 설정/검사 완료: $R_URL/ftir"
    if [[ $R_MODE == both ]]; then log "HTTP 접속도 허용: $R_HTTP_URL/ftir"; fi
    if [[ $R_MODE != http ]]; then
        log "PC 배포용 공개 CA: $R_TLS/client-ca.cer (개인키/edge.env/백업은 배포 금지)"
        openssl x509 -in "$R_TLS/ca.pem" -noout -fingerprint -sha256
    fi
    printf '복원: sudo bash %q rollback --backup %q\n' "${BASH_SOURCE[0]}" "$R_BACKUP"
    log '별도 확인: 선택한 Nginx 외부 포트 허용, HTTPS 사용 시 PC CA 신뢰 등록, 실제 SSO/LIMS 및 대용량 업로드.'
    log '실제 SSO/LIMS 접속·인증과 실험 PC 브라우저 동작은 이 스크립트에서 검증하지 않았습니다.'
}

load_backup() {
    [[ -n $R_BACKUP && -d $R_BACKUP && -f $R_BACKUP/metadata && ! -L $R_BACKUP/metadata ]] || die '유효한 --backup 디렉터리가 필요합니다.'
    [[ $(stat -c '%u:%a' "$R_BACKUP") == '0:700' ]] || die 'root 소유·권한 700인 백업만 복원합니다.'
    local -a meta=()
    mapfile -t meta <"$R_BACKUP/metadata"
    if [[ ${#meta[@]} == 7 && ${meta[0]} == RIST_HTTPS_BACKUP_V1 ]]; then
        R_MODE=https R_HTTP_PORT=80
    elif [[ ${#meta[@]} == 9 && ${meta[0]} == RIST_HTTPS_BACKUP_V2 ]]; then
        R_MODE=${meta[7]} R_HTTP_PORT=${meta[8]}
    else die '지원하는 백업 형식이 아닙니다.'; fi
    R_PROJECT=${meta[1]} R_TLS=${meta[2]} R_NGINX=${meta[3]}
    R_HTTPS_PORT=${meta[4]} R_API_PORT=${meta[5]} R_HOST=${meta[6]}
    validate_options
    managed_targets
    local i state
    for i in "${!R_TARGETS[@]}"; do
        [[ ! -L $R_BACKUP/$i.before && ! -L $R_BACKUP/$i.absent ]] || die '심볼릭 링크 백업은 복원하지 않습니다.'
        [[ -f $R_BACKUP/$i.before || -f $R_BACKUP/$i.absent ]] || die '백업 파일이 누락되었습니다.'
    done
    for state in nginx-active nginx-enabled; do
        [[ -f $R_BACKUP/$state && ! -L $R_BACKUP/$state ]] || die '서비스 상태 백업이 없습니다.'
        [[ $(<"$R_BACKUP/$state") =~ ^[01]$ ]] || die '서비스 상태 백업이 올바르지 않습니다.'
    done
}

rollback_https() {
    require_root
    need flock
    load_backup
    local original=$R_BACKUP
    # Preserve the current files as well; even manual rollback is recoverable.
    R_BACKUPS=$(dirname -- "$R_BACKUP")
    safe_path "$R_BACKUPS/.rist-https.lock"
    exec 9>"$R_BACKUPS/.rist-https.lock"
    flock -n 9 || die '다른 HTTPS 설정 작업이 실행 중입니다.'
    snapshot
    log "복원 직전 상태 백업: $R_BACKUP"
    restore_files "$original" || die "복원 일부 실패. 원본 백업을 보존했습니다: $original"
    log '이전 설정 복원 완료. 최초 설치에서 새로 만든 관리 파일만 제거되며 현재 상태도 백업되어 있습니다.'
}

main() {
    reset_options
    parse_options "$@"
    validate_options
    if [[ $R_ACTION == plan ]]; then
        log "계획만 출력합니다. 파일/서비스 변경 없음. 접속: $R_URL"
        log "Nginx 모드: $R_MODE (http=$R_HTTP_PORT, https=$R_HTTPS_PORT)"
        if [[ $R_MODE == both ]]; then log "HTTP 접속도 허용: $R_HTTP_URL"; fi
        log "기존 환경: $R_ENV / 내부 API: 127.0.0.1:$R_API_PORT"
        log "Nginx: $R_NGINX / 인증서: $R_TLS / 백업: $R_BACKUPS"
        log '사전 준비: Ubuntu, 실행 중인 rist-edge-api.service, nginx/curl/ss/flock.'
        if [[ $R_MODE == http ]]; then
            log 'HTTP 모드에는 서버 인증서가 필요하지 않습니다. PC는 Nginx HTTP 주소를 사용합니다.'
        else
            log 'HTTPS 사용 시 OpenSSL, 서버 인증서+키+CA가 필요합니다.'
            log '사내 CA가 없으면 별도 관리 PC에서 init-ca → Edge에서 csr → 관리 PC에서 sign → Edge에서 apply.'
        fi
        return
    fi
    [[ $(uname -s) == Linux && ${BASH_VERSINFO[0]} -ge 4 ]] || die 'Linux의 Bash 4 이상에서 실행하세요.'
    case $R_ACTION in
        init-ca) need flock; init_ca ;; csr) need flock; create_csr ;;
        sign) need flock; sign_csr ;; apply) apply_https ;;
        check)
            server_prerequisites
            nginx -t
            verify_live "$R_TLS/ca.pem"
            log "Nginx $R_MODE/프록시 검사 통과: $R_URL (실제 SSO/LIMS 인증은 별도)" ;;
        rollback) rollback_https ;;
    esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
