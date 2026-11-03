#!/usr/bin/env python3
"""Isolated Python/MariaDB/Spring/HTTPS-LIMS/SFTP integration verification.

Uses an ephemeral Docker MariaDB, synthetic data and temporary local services.
Never accepts operational DB/API credentials. The unresolved recipient lookup is
represented by an explicit test-only DB fixture after Python's normal enqueue.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4
from zipfile import ZipFile

import httpx
import paramiko
import pymysql
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


EDGE = Path(__file__).resolve().parents[2]
for item in (EDGE, EDGE.parent, EDGE.parent / "common", EDGE.parent / "sune", EDGE.parent / "rin", EDGE.parent / "lim", EDGE.parent / "ahn"):
    sys.path.insert(0, str(item))

from app.config import Settings
from app.lims_requests import NUMBER_FIELDS, TEXT_FIELDS
from app.lims_sso import LimsSsoClient
from app.main import create_app
from app.report_management import cleanup_expired_reports
from app.report_queue import enqueue_report_package


TOKEN = "integration-only-external-token"
INTERNAL_KEY = "integration-only-internal-key-32-characters"
SFTP_PASSWORD = "integration-only-sftp-password"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def await_condition(description, check, timeout=45):
    until = time.monotonic() + timeout
    last = None
    while time.monotonic() < until:
        try:
            result = check()
            if result:
                return result
        except (OSError, httpx.HTTPError, pymysql.MySQLError) as error:
            last = type(error).__name__
        time.sleep(0.15)
    raise AssertionError("Timed out: " + description + (" (" + last + ")" if last else ""))


def certificates(folder, java):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "RITAS local integration fixture")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=2))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = folder / "lims-ca.pem", folder / "lims-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_file.chmod(0o600)
    trust = folder / "lims-truststore.p12"
    subprocess.run([str(java.parent / "keytool"), "-importcert", "-noprompt", "-alias", "test-lims",
                    "-file", str(cert_file), "-keystore", str(trust), "-storepass", "changeit"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert_file, key_file, trust


class LimsFixture:
    def __init__(self, remote_root):
        self.remote_root = remote_root
        self.documents = {}
        self.mails = {}
        self.document_status = "PENDING"
        self.mail_status = "PENDING"
        self.mail_posts = 0
        self.retry_posts = 0
        self.duplicate_posts = 0
        self.auth_checks = 0
        self.request_queries = []
        self.drop_first_accept = True
        self.package_hash = None
        self.failures = []
        self.lock = threading.RLock()

    def handler(self):
        state = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def reply(self, body, status=200):
                data = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=UTF-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                self.handle_api()
            def do_POST(self):
                self.handle_api()
            def handle_api(self):
                try:
                    with state.lock:
                        assert self.headers.get("X-Authorization") == "Bearer " + TOKEN
                        parsed = urlsplit(self.path)
                        path = unquote(parsed.path).removeprefix("/RIST/api/ax")
                        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                        body = json.loads(raw) if raw else None
                        if path == "/auth/verify":
                            assert self.command == "POST"
                            user = base64.b64decode(body["userId"], validate=True).decode("utf-8")
                            password = base64.b64decode(body["password"], validate=True).decode("utf-8")
                            state.auth_checks += 1
                            return self.reply({"result": "SUCCESS"} if (user, password) == ("tester", "test-password-한글") else {"result": "FAIL", "errorCode": "INVALID_CREDENTIAL"})
                        if path == "/requests":
                            assert self.command == "GET"
                            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                            state.request_queries.append(query)
                            row = {k: None for k in NUMBER_FIELDS | TEXT_FIELDS}
                            row.update(reqResultNo=1, reqNumber="2026R02236", reqDate="2026-10-06", testMtdCode="A23142", testMtdName="XRD test", testChargerName="Test User")
                            return self.reply({"page": int(query["page"]), "size": int(query["size"]), "totalCount": 1, "items": [row]})
                        if path == "/documents" and self.command == "POST":
                            assert body["registerType"] == "INITIAL"
                            assert body["reqNumber"] == "2026R02236" and body["testMtdCode"] == "A23142"
                            file = state.remote_root / body["fileDir"] / body["fileName"]
                            assert file.is_file() and file.stat().st_size == body["fileSize"]
                            assert hashlib.sha256(file.read_bytes()).hexdigest() == state.package_hash
                            key = body["idempotencyKey"]
                            duplicate = key in state.documents
                            state.documents.setdefault(key, {**body, "axDocNum": 1001, "regenSeq": 0, "fileNo": 55001})
                            return self.reply({"axDocNum": 1001, "regenSeq": 0, "duplicate": duplicate})
                        if path == "/documents/1001":
                            doc = next(iter(state.documents.values()))
                            return self.reply({"axDocNum": 1001, "status": state.document_status,
                                "files": [{k: doc[k] for k in ("regenSeq", "fileNo", "fileName", "fileSize")}],
                                "errorMessage": "Synthetic mapping delay" if state.document_status == "FAILED" else None})
                        if path == "/mails" and self.command == "POST":
                            state.mail_posts += 1
                            request_id = body["requestId"]
                            assert body["to"] == "recipient@example.test"
                            assert body["cc"] == "review@rist.re.kr" and body["bcc"] == "hidden@rist.re.kr"
                            assert body["subject"] == "Integration report" and "Integration body" in body["htmlBody"]
                            assert body["attachFileNos"] == "55001"
                            if request_id in state.mails:
                                state.duplicate_posts += 1
                                return self.reply({**self.mail_result(request_id), "status": "DUPLICATE"})
                            state.mails[request_id] = body
                            if state.drop_first_accept:
                                state.drop_first_accept = False
                                self.close_connection = True
                                self.connection.shutdown(socket.SHUT_RDWR)
                                return
                            return self.reply({**self.mail_result(request_id), "status": "ACCEPTED"})
                        if path.startswith("/mails/"):
                            request_id = path[len("/mails/"):].removesuffix("/retry")
                            if request_id not in state.mails:
                                return self.reply({}, 404)
                            if path.endswith("/retry"):
                                assert self.command == "POST" and raw == b""
                                state.retry_posts += 1
                                if state.mail_status != "SENT":
                                    state.mail_status = "PENDING"
                            return self.reply(self.mail_result(request_id))
                        return self.reply({}, 404)
                except Exception as error:
                    state.failures.append(type(error).__name__ + ": " + str(error))
                    with suppress(OSError):
                        self.reply({"errorCode": "FIXTURE_ASSERTION"}, 500)
            def mail_result(self, request_id):
                return {"requestId": request_id, "alarmResultNo": 19250, "status": state.mail_status,
                        "tryCount": 5 if state.mail_status == "FAILED" else 0,
                        "acceptedAt": "2026-11-04 08:00:00", "sentAt": None,
                        "errorCode": "TEMPORARY_ERROR" if state.mail_status == "FAILED" else None,
                        "errorMessage": "Synthetic SMTP outage" if state.mail_status == "FAILED" else None}
        return Handler


class SftpFixture:
    def __init__(self, folder):
        self.root = folder / "sftp-files"
        self.root.mkdir()
        # ECDSA is supported by both the bundled JSch and modern SSH servers;
        # do not re-enable obsolete ssh-rsa/SHA-1 to accommodate a test fixture.
        self.key = paramiko.ECDSAKey.generate(bits=256)
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.socket.settimeout(0.2)
        self.port = self.socket.getsockname()[1]
        self.transports = []
        self.stopped = threading.Event()
        self.uploads = 0
        self.known_hosts = folder / "known_hosts"
        self.known_hosts.write_text(f"[127.0.0.1]:{self.port} {self.key.get_name()} {self.key.get_base64()}\n")
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        fixture = self
        class Server(paramiko.ServerInterface):
            def check_auth_password(self, username, password):
                return paramiko.AUTH_SUCCESSFUL if (username, password) == ("integration", SFTP_PASSWORD) else paramiko.AUTH_FAILED
            def get_allowed_auths(self, username):
                return "password"
            def check_channel_request(self, kind, channel_id):
                return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        class Files(paramiko.SFTPServerInterface):
            def resolve(self, path):
                resolved = (fixture.root / path.lstrip("/")).resolve()
                if resolved != fixture.root and fixture.root not in resolved.parents:
                    raise PermissionError()
                return resolved
            def stat(self, path):
                try:
                    return paramiko.SFTPAttributes.from_stat(self.resolve(path).stat())
                except OSError as error:
                    return paramiko.SFTPServer.convert_errno(error.errno)
            lstat = stat
            def mkdir(self, path, attr):
                try:
                    self.resolve(path).mkdir()
                    return paramiko.SFTP_OK
                except OSError as error:
                    return paramiko.SFTPServer.convert_errno(error.errno)
            def open(self, path, flags, attr):
                try:
                    target = self.resolve(path)
                    file = os.fdopen(os.open(target, flags, 0o600), "r+b" if flags & os.O_RDWR else "wb" if flags & os.O_WRONLY else "rb")
                    handle = paramiko.SFTPHandle(flags)
                    handle.readfile = file
                    handle.writefile = file
                    return handle
                except OSError as error:
                    return paramiko.SFTPServer.convert_errno(error.errno)
            def rename(self, old, new):
                try:
                    self.resolve(old).rename(self.resolve(new))
                    fixture.uploads += 1
                    return paramiko.SFTP_OK
                except OSError as error:
                    return paramiko.SFTPServer.convert_errno(error.errno)
            def remove(self, path):
                try:
                    self.resolve(path).unlink()
                    return paramiko.SFTP_OK
                except OSError as error:
                    return paramiko.SFTPServer.convert_errno(error.errno)
        while not self.stopped.is_set():
            try:
                connection, _ = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = paramiko.Transport(connection)
            transport.add_server_key(self.key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, Files)
            self.transports.append(transport)
            try:
                transport.start_server(server=Server())
            except (EOFError, paramiko.SSHException):
                transport.close()

    def close(self):
        self.stopped.set()
        self.socket.close()
        for transport in self.transports:
            transport.close()


def run(args):
    work = (Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="ritasax-v4-integration-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.chmod(work, 0o700)
    java, jar = Path(args.java).resolve(), Path(args.spring_jar).resolve()
    assert java.is_file() and jar.is_file()
    container = "ritasax-v4-test-" + uuid4().hex[:12]
    password = secrets.token_hex(16)
    spring = None
    app = None
    web = None
    lims_http = None
    sftp = None
    handles = []
    print("WORK_DIR=" + str(work), flush=True)
    try:
        subprocess.run(["docker", "run", "--detach", "--rm", "--name", container, "--publish", "127.0.0.1::3306",
                        "--env", "MARIADB_ROOT_PASSWORD=" + password, args.mariadb_image], check=True, stdout=subprocess.DEVNULL)
        mapped = subprocess.check_output(["docker", "port", container, "3306/tcp"], text=True).strip()
        assert mapped.startswith("127.0.0.1:")
        db_port = int(mapped.split(":")[-1])
        def connect(database=None):
            return pymysql.connect(host="127.0.0.1", port=db_port, user="root", password=password,
                                   database=database, autocommit=True, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
        def ready_db():
            connection = connect()
            connection.close()
            return True
        await_condition("isolated MariaDB", ready_db, 60)
        cert, key, trust = certificates(work, java)
        sftp = SftpFixture(work)
        fixture = LimsFixture(sftp.root)
        lims_http = ThreadingHTTPServer(("127.0.0.1", 0), fixture.handler())
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        lims_http.socket = tls.wrap_socket(lims_http.socket, server_side=True)
        threading.Thread(target=lims_http.serve_forever, daemon=True).start()
        spring_port, python_port = free_port(), free_port()
        spring_url = f"http://127.0.0.1:{spring_port}"
        python_url = f"http://127.0.0.1:{python_port}"
        config = Settings(storage_root=work / "storage", db_host="127.0.0.1", db_port=db_port, db_name="ritasax_v4_test",
                          db_user="root", db_password=password, auth_enabled=False, lims_requests_enabled=True,
                          lims_requests_url=spring_url + "/api/lims/requests", sso_lims_internal_key=INTERNAL_KEY,
                          sso_lims_allow_loopback_http=True, report_completed_retention_hours=24, report_trash_retention_hours=0)
        app = create_app(config)
        database = app.state.database
        def sql(statement, params=(), *, one=False):
            with connect(config.db_name) as connection, connection.cursor() as cursor:
                cursor.execute(statement, params)
                return cursor.fetchone() if one else cursor.fetchall()
        with ZipFile(jar) as archive:
            schema = archive.read("BOOT-INF/classes/schema/schema-local.sql").decode()
        for statement in schema.split(";"):
            if statement.strip():
                sql(statement)
        sql("CREATE TABLE lims_req_ax_search (req_result_no BIGINT,req_number VARCHAR(50),req_date VARCHAR(10),req_state INT,req_state_name VARCHAR(50),req_type_no BIGINT,req_type_code VARCHAR(50),req_type_name VARCHAR(100),project_code VARCHAR(50),cust_req_name VARCHAR(100),customer_no BIGINT,customer_name VARCHAR(100),req_user_no BIGINT,req_user_name VARCHAR(100),smp_result_no BIGINT,smp_result_name VARCHAR(100),smp_result_state INT,test_mtd_result_no BIGINT,test_mtd_no BIGINT,test_mtd_code VARCHAR(50),test_mtd_name VARCHAR(100),test_state INT,test_charger_name VARCHAR(100),output_order INT,synced_at DATETIME)")
        sql("INSERT INTO lims_req_ax_search(req_result_no,req_number,req_date,test_mtd_code,test_mtd_name,req_state_name,synced_at) VALUES(1,'CACHED-UNCHANGED','2026-10-01','A23142','XRD','진행',NOW())")
        env = {k: v for k, v in os.environ.items() if not (k.startswith("RITASAX_") or k.startswith("SPRING_") or k in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS"})}
        env.update({
            "SPRING_PROFILES_ACTIVE": "prod", "SERVER_ADDRESS": "127.0.0.1", "SERVER_PORT": str(spring_port),
            "RITASAX_EDGE_DB_URL": f"jdbc:mariadb://127.0.0.1:{db_port}/{config.db_name}",
            "RITASAX_EDGE_DB_USERNAME": "root", "RITASAX_EDGE_DB_PASSWORD": password,
            "RITASAX_SFTP_HOST": "127.0.0.1", "RITASAX_SFTP_PORT": str(sftp.port),
            "RITASAX_SFTP_USER": "integration", "RITASAX_SFTP_PASSWORD": SFTP_PASSWORD,
            "RITASAX_SFTP_REMOTE_BASE_PATH": "/", "RITASAX_SFTP_KNOWN_HOSTS": str(sftp.known_hosts),
            "RITASAX_SFTP_STRICT_HOST_KEY_CHECKING": "true", "RITASAX_SERVLET_API_BASE_URL": python_url,
            "RITASAX_LIMS_API_BASE_URL": f"https://127.0.0.1:{lims_http.server_port}/RIST/api/ax",
            "RITASAX_LIMS_API_KEY": TOKEN, "RITASAX_LIMS_API_AUTH_HEADER": "X-Authorization",
            "RITASAX_REPORT_STORAGE_RIST_REPORTS": str(config.storage_root),
            "RITASAX_LIMS_SSO_ENABLED": "true", "RITASAX_LIMS_SSO_INTERNAL_KEY": INTERNAL_KEY,
            "RITASAX_LIMS_SSO_SEND_API_KEY": "true", "RITASAX_LIMS_REQUEST_PROXY_ENABLED": "true",
            "RITASAX_LIMS_MAIL_WORKFLOW_ENABLED": "true", "RITASAX_REQ_AX_SEARCH_SYNC_ENABLED": "false",
            "RITASAX_REQ_AX_SEARCH_SYNC_RUN_ON_STARTUP": "false", "RITASAX_REPORT_TRANSFER_FIXED_DELAY_MS": "200",
            "RITASAX_REPORT_TRANSFER_LEASE_SECONDS": "30", "RITASAX_REPORT_TRANSFER_HEARTBEAT_SECONDS": "5",
            "RITASAX_LIMS_POLL_INTERVAL_SECONDS": "60", "RITASAX_LIMS_POLL_REVIEW_AFTER": "10",
            "RITASAX_LIMS_REVIEW_POLL_INTERVAL_SECONDS": "600", "RITASAX_LOG_LEVEL": "INFO",
        })
        starts = 0
        def start_spring(enabled):
            nonlocal spring, starts
            starts += 1
            env["RITASAX_REPORT_TRANSFER_ENABLED"] = str(enabled).lower()
            log = (work / f"spring-{starts}.log").open("wb")
            handles.append(log)
            spring = subprocess.Popen([str(java), "-Djavax.net.ssl.trustStore=" + str(trust),
                                       "-Djavax.net.ssl.trustStorePassword=changeit", "-jar", str(jar)],
                                      cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT)
            def ready():
                if spring.poll() is not None:
                    raise AssertionError("Spring exited; see " + str(work / f"spring-{starts}.log"))
                return httpx.get(spring_url + "/", timeout=1).status_code == 200
            await_condition("Spring startup", ready, 60)
        def stop_spring():
            nonlocal spring
            if spring:
                spring.terminate()
                try:
                    spring.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    spring.kill()
                    spring.wait(timeout=5)
                spring = None
        start_spring(False)
        web = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=python_port, log_level="error", lifespan="off"))
        threading.Thread(target=web.run, daemon=True).start()
        await_condition("Python HTTP startup", lambda: httpx.get(python_url + "/health", timeout=1).status_code == 200)
        sso = LimsSsoClient(spring_url + "/api/lims/auth/verify", INTERNAL_KEY, allow_loopback_http=True)
        assert sso.validate("tester", "test-password-한글") is True
        assert sso.validate("tester", "wrong") is False
        assert fixture.auth_checks == 2
        print("PASS Python -> Spring -> verified HTTPS LIMS Base64/Bearer SSO", flush=True)
        client = httpx.Client(base_url=python_url, timeout=15, trust_env=False)
        headers = {"X-Request-Id": "integration-requests"}
        response = client.get("/api/v1/requests", params={"source": "lims", "page": 1, "pageSize": 500,
                    "reqNumber": "2026R02236", "testMtdCode": "A23142", "fromDate": "2026-10-01",
                    "toDate": "2026-10-31", "testChargerId": "tester", "testChargerNo": 7}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["totalCount"] == 1 and response.json()["items"][0]["requestState"] is None
        assert fixture.request_queries[-1]["testChargerId"] == "tester"
        assert sql("SELECT req_number FROM lims_req_ax_search", one=True)["req_number"] == "CACHED-UNCHANGED"
        cached = client.get("/api/v1/requests", params={"source": "cache", "testMtdCode": "A23142", "fromDate": "2026-10-01", "toDate": "2026-10-01"}, headers=headers)
        assert cached.status_code == 200 and cached.json()["totalCount"] == 1
        print("PASS filtered live lookup, nullable fields, cache preservation and inclusive local dates", flush=True)
        created = client.post("/api/v1/jobs", json={"pk": {"requestNumber": "2026R02236", "experimentCode": "A23142", "equipmentCode": "XRD-TEST", "operatorId": "tester"}, "sourcePc": {"hostName": "integration", "declaredIpAddress": "127.0.0.1", "clientVersion": "fixture"}}, headers={**headers, "Idempotency-Key": "job-fixture"})
        assert created.status_code == 201, created.text
        job_id = created.json()["jobId"]
        raw = b"synthetic raw data\n"
        uploaded = client.post(f"/api/v1/jobs/{job_id}/files", files={"file": ("source.txt", raw)},
                               data={"relativePath": "raw/source.txt", "sizeBytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
                               headers={**headers, "Idempotency-Key": "raw-fixture"})
        assert uploaded.status_code == 201, uploaded.text
        raw_path = config.storage_root / database.fetch_job(job_id)["root_relative_path"] / "input/raw/source.txt"
        assert raw_path.is_file()
        package = config.storage_root / "reports" / "integration" / "report.zip"
        package.parent.mkdir(parents=True)
        with ZipFile(package, "w") as archive:
            archive.writestr("report.txt", "Synthetic report integration fixture")
        fixture.package_hash = hashlib.sha256(package.read_bytes()).hexdigest()
        report_id = "integration-report"
        email = {"subject": "Integration report", "bodyHtml": "<p>Integration body</p>", "cc": ["review@rist.re.kr"], "bcc": ["hidden@rist.re.kr"]}
        queue_args = dict(settings=config, database=database, report_id=report_id, package_path=package, source_job_id=job_id,
                          request_number="2026R02236", experiment_code="A23142", equipment_code="XRD-TEST", operator_id="tester", report_options={"email": email})
        queued = enqueue_report_package(**queue_args)
        again = enqueue_report_package(**queue_args)
        assert queued["transferId"] == again["transferId"]
        assert sql("SELECT COUNT(*) AS n FROM report_transfers", one=True)["n"] == 1
        # TEST FIXTURE ONLY: production recipient lookup remains unresolved by v4.
        row = sql("SELECT report_options_json FROM report_runs WHERE report_id=%s", (report_id,), one=True)
        options = json.loads(row["report_options_json"])
        assert options["email"]["to"] == []
        options["email"]["to"] = ["recipient@example.test"]
        sql("UPDATE report_runs SET report_options_json=%s WHERE report_id=%s", (json.dumps(options), report_id))
        assert cleanup_expired_reports(settings=config, database=database) == []
        assert package.is_file() and raw_path.is_file()
        stop_spring()
        start_spring(True)
        def transfer():
            record = sql("SELECT * FROM report_transfers WHERE transfer_id=%s", (queued["transferId"],), one=True)
            record["state"] = json.loads(record["delivery_state_json"] or "{}")
            return record
        def settled(check):
            def ready():
                record = transfer()
                if fixture.failures:
                    raise AssertionError("Mock LIMS failed: " + repr(fixture.failures))
                if record["status"] != "PROCESSING" and check(record):
                    return record
                if record["status"] == "FAILED":
                    raise AssertionError("Unexpected transfer failure: " + str(record["last_error_code"]))
                if record["status"] == "RETRY_WAIT" and record.get("last_error_code") and not record["state"]:
                    raise AssertionError("Initial transfer failed: " + str(record["last_error_code"]))
                return None
            return await_condition("persisted transfer checkpoint", ready)
        def advance(check):
            sql("UPDATE report_transfers SET next_retry_at=NOW(6) WHERE transfer_id=%s AND status='RETRY_WAIT'", (queued["transferId"],))
            return settled(check)
        settled(lambda row: row["state"].get("phase") == "DOCUMENT_PENDING")
        for poll in range(1, 11):
            record = advance(lambda row: row["state"].get("documentPollCount", 0) >= poll)
        assert record["state"]["needsReview"] is True and record["state"]["nextPollSeconds"] == 600
        assert record["failure_count"] == 0
        fixture.document_status = "FAILED"
        record = advance(lambda row: row["state"].get("documentStatus") == "FAILED")
        assert record["status"] == "RETRY_WAIT" and record["state"]["documentErrorMessage"]
        fixture.document_status = "COMPLETED"
        record = advance(lambda row: row["state"].get("phase") == "MAIL_READY")
        assert record["document_completed_at"] is not None and record["state"]["fileNo"] == "55001"
        assert sftp.uploads == 1 and len(fixture.documents) == 1
        print("PASS strict SFTP upload, durable document checkpoint, 10-poll backoff and FAILED recovery", flush=True)
        record = advance(lambda row: row["state"].get("phase") == "MAIL_PENDING"
                         or row["state"].get("phase") == "MAIL_SUBMITTING" and row["failure_count"] >= 1)
        if record["state"].get("phase") != "MAIL_PENDING":
            record = advance(lambda row: row["state"].get("phase") == "MAIL_PENDING")
        assert fixture.mail_posts == 2 and fixture.duplicate_posts == 1 and len(fixture.mails) == 1
        failures_before_polling = record["failure_count"]
        for poll in range(1, 11):
            record = advance(lambda row: row["state"].get("mailPollCount", 0) >= poll)
        assert record["state"]["needsReview"] is True and record["state"]["nextPollSeconds"] == 600
        assert record["failure_count"] == failures_before_polling
        for path in ("/monitor/transfers", "/monitor/transfers/" + queued["transferId"]):
            monitor = httpx.get(spring_url + path, timeout=10)
            assert monitor.status_code == 200, "Spring monitor failed: " + path
            assert "hidden@rist.re.kr" not in monitor.text and "submittedMail" not in monitor.text
        print("PASS Spring monitor views render delivery checkpoints without mail-envelope disclosure", flush=True)
        stop_spring()
        sql("UPDATE report_transfers SET document_completed_at=DATE_SUB(NOW(6),INTERVAL 25 HOUR) WHERE transfer_id=%s", (queued["transferId"],))
        cleaned = cleanup_expired_reports(settings=config, database=database)
        assert cleaned and not package.exists() and not raw_path.exists()
        assert not any((config.storage_root / ".report-trash").rglob("*.zip"))
        assert transfer()["mail_status"] == "PENDING"
        print("PASS lost mail-accept response reconciles DUPLICATE; confirmed files physically removed after 24h while mail pending", flush=True)
        fixture.mail_status = "FAILED"
        start_spring(True)
        record = advance(lambda row: row["mail_status"] == "FAILED")
        assert record["status"] == "FAILED"
        retry = client.post(f"/api/v1/report-management/{report_id}/retry", json={}, headers=headers)
        assert retry.status_code in (200, 202), retry.text
        record = settled(lambda row: row["state"].get("phase") == "MAIL_PENDING" and fixture.retry_posts == 1)
        assert fixture.retry_posts == 1 and sftp.uploads == 1 and len(fixture.documents) == 1
        fixture.mail_status = "SENT"
        record = advance(lambda row: row["status"] == "COMPLETED")
        assert record["mail_status"] == "SENT" and record["state"]["phase"] == "DONE"
        assert not package.exists() and fixture.retry_posts == 1 and fixture.mail_posts == 2
        assert not fixture.failures
        print("PASS restart, explicit mail-only retry after local deletion, SENT completion without re-upload", flush=True)
        report = {"result": "PASS", "ssoChecks": fixture.auth_checks, "documentRegistrations": len(fixture.documents),
                  "sftpUploads": sftp.uploads, "mailSubmitCalls": fixture.mail_posts, "uniqueMails": len(fixture.mails),
                  "duplicateResponses": fixture.duplicate_posts, "mailRetryCalls": fixture.retry_posts,
                  "scope": "isolated synthetic integration; not actual LIMS or Edge verification",
                  "recipientFixture": "Synthetic To injected after enqueue; production recipient lookup remains pending"}
        (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report), flush=True)
        client.close()
    finally:
        if spring:
            spring.terminate()
            with suppress(subprocess.TimeoutExpired):
                spring.wait(timeout=10)
            if spring.poll() is None:
                spring.kill()
        if web:
            web.should_exit = True
        if lims_http:
            lims_http.shutdown()
            lims_http.server_close()
        if sftp:
            sftp.close()
        if app:
            app.state.database.close()
        for handle in handles:
            handle.close()
        subprocess.run(["docker", "rm", "--force", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spring-jar", required=True)
    parser.add_argument("--java", required=True, help="Java 17 executable; sibling keytool is required")
    parser.add_argument("--mariadb-image", default="mariadb:11.4")
    parser.add_argument("--work-dir")
    run(parser.parse_args())
