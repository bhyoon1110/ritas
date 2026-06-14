import os

import numpy as np

from processors.base import BaseProcessor
from utils.chart_maker import save_line_chart
from utils.data_formatter import llm_payload
from utils.excel_parser import read_table


class XRDProcessor(BaseProcessor):
    test_type = "XRD"

    def run(self):
        table_paths = [p for p in self.file_paths if p.lower().endswith(('.csv', '.xlsx', '.xls'))]
        chart_paths = []
        peaks = []
        for path in table_paths:
            df = read_table(path)
            if len(df.columns) < 2:
                continue
            x = [float(v) for v in df.iloc[:, 0].tolist()]
            y = [float(v) for v in df.iloc[:, 1].tolist()]
            out = self.mkpath(os.path.splitext(os.path.basename(path))[0] + '_xrd.png')
            chart_paths.append(save_line_chart(x, y, out, 'XRD Pattern', '2θ', 'Intensity'))
            arr = np.array(y)
            idxs = [i for i in range(1, len(arr)-1) if arr[i] > arr[i-1] and arr[i] > arr[i+1]]
            peaks.extend([{"two_theta": round(x[i], 3), "intensity": round(y[i], 3)} for i in sorted(idxs, key=lambda i: arr[i], reverse=True)[:5]])

        summary = {"file_count": len(table_paths), "peak_count": len(peaks), "chart_count": len(chart_paths)}
        qc_items = [self.qc('peak_count', '대표 피크 수', summary['peak_count'], 'OK' if summary['peak_count'] else 'WARN')]
        payload = llm_payload(self.test_type, self.request_id, summary, {"peaks": peaks}, "상 주요 피크를 바탕으로 가능한 상(phase) 후보를 설명해주세요.")
        return self.build_result(summary, payload, qc_items, chart_paths, chart_paths, {"images": []})


def process(request_id, file_paths, options=None):
    return XRDProcessor(request_id, file_paths, options).run()
