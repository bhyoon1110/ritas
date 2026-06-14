import os

from processors.base import BaseProcessor
from utils.data_formatter import llm_payload
from utils.excel_parser import read_table


class XPSProcessor(BaseProcessor):
    test_type = "XPS"

    def run(self):
        table_paths = [p for p in self.file_paths if p.lower().endswith(('.csv', '.xlsx', '.xls'))]
        previews = {}
        for path in table_paths:
            df = read_table(path).fillna('')
            previews[os.path.basename(path)] = df.head(15).to_dict(orient='records')
        summary = {"file_count": len(table_paths), "preview_tables": len(previews)}
        qc_items = [self.qc('file_count', '입력 파일 수', summary['file_count'], 'OK' if summary['file_count'] else 'WARN')]
        payload = llm_payload(self.test_type, self.request_id, summary, {"tables": previews}, "결합에너지 피크와 원소 상태를 요약해주세요.")
        return self.build_result(summary, payload, qc_items, [], [], {"images": []})


def process(request_id, file_paths, options=None):
    return XPSProcessor(request_id, file_paths, options).run()
