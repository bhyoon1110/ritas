"""Emit build inputs without a DB or live service. Run with the RIST Python env."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.path_bootstrap import add_project_package_paths
add_project_package_paths()
from app.auth import AuthContext, _account_page, _admin_page, _login_page, _signup_page
from app.config import Settings
from app.ftir_web import build_ftir_page, plotly_asset_path
from app.ahn_web import build_ahn_page
from app.raman_web import build_raman_page
from app.xrd_web import build_xrd_page
from app.preview_web import build_workspace_index
from app.voc_web import build_voc_page
from app.error_archive import _operations_console_html, _error_feedback_page
from app.report_management import report_management_console
from app.report_delivery import _generated_report_page


def pages():
    context = AuthContext(
        user_id="compat-build", login_id="compat-build", email=None, display_name="Build fixture",
        status="ACTIVE", session_id="build", session_expires_at=datetime.now() + timedelta(hours=1),
        sso_authenticated_at=None, projects=frozenset({"FTIR", "TEM", "RAMAN", "XRD"}),
        roles=frozenset({"ADMIN"}), sso_identity=None,
    )
    return {
        "index": build_workspace_index(), "ftir": build_ftir_page(), "tem": build_ahn_page(),
        "raman": build_raman_page(), "xrd": build_xrd_page(),
        "login": _login_page("/"), "signup": _signup_page(), "admin": _admin_page(),
        "account": _account_page(context, Settings(storage_root=Path("/tmp/rist-compat-build-unused"), edge_public_base_url="https://build.invalid", sso_mode="posco")),
        "voc": build_voc_page(context, "FTIR"),
        "operations": _operations_console_html("usage"), "errors": _operations_console_html("errors"),
        "reports": report_management_console().body.decode(),
        "report-review": _generated_report_page("build-report", "TEM", {
            "canQueue":True,"transferStatus":"NOT_QUEUED","requestNumber":"BUILD",
            "experimentCode":"TEM","equipmentCode":"BUILD","generatedAt":"2026-10-09",
        }).body.decode(),
        "error-feedback": _error_feedback_page("build-error", {"project":"FTIR","code":"TEST","message":"build"}).body.decode(),
    }


if __name__ == "__main__":
    print(json.dumps({"pages": pages(), "plotly": str(plotly_asset_path())}, ensure_ascii=True))
