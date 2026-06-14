import json
import os
from datetime import datetime


class BaseProcessor(object):
    test_type = "BASE"

    def __init__(self, request_id, file_paths, options=None):
        self.request_id = request_id
        self.file_paths = file_paths
        self.options = options or {}
        self.work_dir = self.options.get("work_dir") or self._guess_work_dir()
        if not os.path.exists(self.work_dir):
            os.makedirs(self.work_dir)

    def _guess_work_dir(self):
        first = self.file_paths[0] if self.file_paths else os.getcwd()
        return os.path.join(os.path.dirname(first), "processed")

    def mkpath(self, name):
        return os.path.join(self.work_dir, name)

    def qc(self, key, label, value, status):
        return {"key": key, "label": label, "value": str(value), "status": status}

    def build_result(self, summary, llm_payload, qc_items, chart_paths=None, output_files=None, artifacts=None):
        return {
            "testType": self.test_type,
            "requestId": self.request_id,
            "summary": summary,
            "chartPaths": chart_paths or [],
            "outputFiles": output_files or [],
            "llmPayload": json.dumps(llm_payload, ensure_ascii=False),
            "qcItems": qc_items,
            "artifacts": artifacts or {},
            "generatedAt": datetime.utcnow().isoformat() + "Z"
        }
