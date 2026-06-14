import os
import re

import numpy as np

from processors.base import BaseProcessor
from utils.data_formatter import llm_payload
from utils.excel_parser import read_table

DETECTION_LIMITS = {
    "default": 0.05,
    "F": 0.1,
    "S": 1.5,
    "Al": 0.01,
    "Ti": 0.01,
    "V": 0.01,
}
MATRIX_THRESHOLD_PCT = 1.0


class GDMSProcessor(BaseProcessor):
    test_type = "GD-MS"

    def _element_name(self, isotope):
        raw = isotope.split('(')[0]
        return ''.join(re.findall(r'[A-Za-z]+', raw))

    def run(self):
        table_paths = [p for p in self.file_paths if p.lower().endswith(('.csv', '.xlsx', '.xls'))]
        samples = {}
        total_flags = 0
        for path in table_paths:
            df = read_table(path).fillna('')
            cols = {c.lower(): c for c in df.columns}
            iso_col = cols.get('isotope') or cols.get('element') or list(df.columns)[0]
            conc_col = cols.get('concentration avg') or cols.get('conc') or cols.get('value') or list(df.columns)[-1]
            bucket = {}
            for _, row in df.iterrows():
                elem = self._element_name(str(row[iso_col]))
                try:
                    value = float(row[conc_col])
                except Exception:
                    value = 0.0
                bucket.setdefault(elem, []).append(value)
            processed = {}
            reference = max(np.mean(bucket.get('Si', [1.0])), 1.0)
            for elem, values in bucket.items():
                avg_val = float(np.mean([v for v in values if v > 0])) if any(v > 0 for v in values) else 0.0
                pct = (avg_val / reference) * 100.0
                if pct >= MATRIX_THRESHOLD_PCT:
                    processed[elem] = 'Matrix'
                elif elem in ('N', 'O'):
                    processed[elem] = '-'
                elif avg_val < DETECTION_LIMITS.get(elem, DETECTION_LIMITS['default']):
                    total_flags += 1
                    processed[elem] = '<{}'.format(DETECTION_LIMITS.get(elem, DETECTION_LIMITS['default']))
                else:
                    processed[elem] = round(avg_val, 4)
            samples[os.path.splitext(os.path.basename(path))[0]] = processed

        summary = {
            "sample_count": len(samples),
            "elements_per_sample": max([len(v) for v in samples.values()] or [0]),
            "below_detection_count": total_flags,
        }
        qc_items = [
            self.qc('sample_count', '시료 수', summary['sample_count'], 'OK' if summary['sample_count'] > 0 else 'WARN'),
            self.qc('bdl_count', '정량한계 이하 항목 수', total_flags, 'OK'),
        ]
        payload = llm_payload(
            self.test_type,
            self.request_id,
            summary,
            {"samples": samples},
            "시료 간 주요 불순물 차이와 Matrix 원소를 요약하고, PPT 결과표 하단 코멘트를 작성해주세요."
        )
        return self.build_result(summary, payload, qc_items, [], [], {"images": []})


def process(request_id, file_paths, options=None):
    return GDMSProcessor(request_id, file_paths, options).run()
