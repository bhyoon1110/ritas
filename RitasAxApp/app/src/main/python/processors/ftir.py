import os

import numpy as np

from processors.base import BaseProcessor
from utils.chart_maker import save_line_chart
from utils.data_formatter import llm_payload
from utils.excel_parser import iter_dpt


class FTIRProcessor(BaseProcessor):
    test_type = "FT-IR"

    def run(self):
        dpt_files = [p for p in self.file_paths if p.lower().endswith('.dpt')]
        spectra = {}
        chart_paths = []
        peaks = {}
        for path in dpt_files:
            name = os.path.splitext(os.path.basename(path))[0]
            pts = iter_dpt(path)
            if not pts:
                continue
            wavenumbers, absorbances = zip(*pts)
            spectra[name] = {
                "point_count": len(pts),
                "max_absorbance": round(float(max(absorbances)), 4),
            }
            chart_paths.append(save_line_chart(wavenumbers, absorbances, self.mkpath('ftir_{}.png'.format(name)), 'FT-IR {}'.format(name), 'Wavenumber', 'Absorbance', invert_x=True))
            arr = np.array(absorbances)
            peaks_idx = [i for i in range(1, len(arr) - 1) if arr[i] > arr[i - 1] and arr[i] > arr[i + 1] and arr[i] > 0.05]
            top = sorted(peaks_idx, key=lambda i: arr[i], reverse=True)[:5]
            peaks[name] = [{"wavenumber": round(wavenumbers[i], 2), "absorbance": round(absorbances[i], 4)} for i in top]

        summary = {
            "sample_count": len(spectra),
            "chart_count": len(chart_paths),
        }
        qc_items = [
            self.qc('sample_count', '스펙트럼 수', summary['sample_count'], 'OK' if summary['sample_count'] else 'WARN'),
            self.qc('chart_count', '생성 그래프 수', summary['chart_count'], 'OK' if summary['chart_count'] else 'WARN'),
        ]
        payload = llm_payload(self.test_type, self.request_id, summary, {"spectra": spectra, "peaks": peaks}, "주요 피크를 해석하고 시료별 차이를 설명해주세요.")
        return self.build_result(summary, payload, qc_items, chart_paths, chart_paths, {"images": []})


def process(request_id, file_paths, options=None):
    return FTIRProcessor(request_id, file_paths, options).run()
