import json
import re

import pytest

from app.report_condition_options import condition_options
from app.report_editor import editor_page


@pytest.mark.parametrize("project,kind", [("FT-IR", "ftir"), ("RAMAN", "raman")])
def test_editor_and_analysis_use_identical_catalog_and_existing_storage_key(project, kind):
    from app import ftir_web, raman_web

    page = editor_page(f"/api/v1/{kind}/report/jobs", "test")
    config = json.loads(re.search(r'<script id="editor-config"[^>]*>(.*?)</script>', page).group(1))
    catalog = condition_options(project)
    assert config["conditionOptions"] == catalog
    assert catalog["storageKey"] == f"rist-{kind}-report-condition-options-v{'1' if kind == 'ftir' else '2'}"
    script = (ftir_web if kind == "ftir" else raman_web)._UPLOAD_SCRIPT
    fields = json.loads(re.search(r"var REPORT_OPTION_FIELDS = (.*?);", script).group(1))
    assert fields == catalog["fields"]
    assert "__REPORT_OPTION_FIELDS__" not in script
    assert len({f["field"] for f in fields}) == len(fields)
    from app.report.builders import _FTIR_CONDITION_ORDER, _RAMAN_CONDITION_ORDER
    expected = _FTIR_CONDITION_ORDER if kind == "ftir" else [label for label in _RAMAN_CONDITION_ORDER if label != "기타"]
    assert [f["label"] for f in fields] == expected  # 기타 remains free-form.
    assert "window.RistReportConditionOptions =" in page


def test_tem_does_not_get_spectroscopy_conditions_or_storage():
    page = editor_page("/api/v1/tem/jobs", "test", project="TEM")
    config = json.loads(re.search(r'<script id="editor-config"[^>]*>(.*?)</script>', page).group(1))
    assert config["conditionOptions"] == {"storageKey": "", "fields": []}


def test_instrument_model_has_no_invented_default():
    field = condition_options("FT-IR")["fields"][0]
    assert field["field"] == "equipmentModel"
    assert field["defaults"] == []
