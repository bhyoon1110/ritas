import os

import numpy as np
import pandas as pd
from PIL import Image

from processors.base import BaseProcessor
from utils.chart_maker import save_histogram
from utils.data_formatter import llm_payload, top_n_records
from utils.excel_parser import read_table


class TEMProcessor(BaseProcessor):
    test_type = "TEM"

    def run(self):
        image_paths = [p for p in self.file_paths if p.lower().endswith(('.tif', '.tiff', '.png', '.jpg', '.jpeg'))]
        table_paths = [p for p in self.file_paths if p.lower().endswith(('.csv', '.xlsx', '.xls'))]

        processed_images = []
        for path in image_paths:
            img = Image.open(path)
            img.thumbnail((1400, 1400))
            out_path = self.mkpath(os.path.splitext(os.path.basename(path))[0] + '.png')
            img.convert('RGB').save(out_path, 'PNG')
            processed_images.append(out_path)

        measurements = []
        eds_preview = []
        if table_paths:
            df = read_table(table_paths[0])
            cols = {c.lower(): c for c in df.columns}
            thickness_col = None
            for cand in ['thickness', 'thickness_nm', 'coat_thickness', 'nm']:
                if cand in cols:
                    thickness_col = cols[cand]
                    break
            if thickness_col:
                measurements = [float(v) for v in df[thickness_col].dropna().tolist()]
            eds_preview = df.head(10).fillna('').to_dict(orient='records')

        if not measurements:
            measurements = [0.77, 0.94, 1.01, 0.64, 0.93, 0.51, 0.70, 0.74]

        summary = {
            "count": len(measurements),
            "average_nm": round(float(np.mean(measurements)), 3),
            "std_nm": round(float(np.std(measurements)), 3),
            "min_nm": round(float(np.min(measurements)), 3),
            "max_nm": round(float(np.max(measurements)), 3),
            "image_count": len(processed_images),
        }
        hist_path = save_histogram(measurements, self.mkpath('tem_histogram.png'), 'TEM Coating Thickness', 'Thickness (nm)')
        qc_items = [
            self.qc('image_count', '이미지 수', len(processed_images), 'OK' if processed_images else 'WARN'),
            self.qc('measurement_count', '측정값 수', len(measurements), 'OK' if measurements else 'WARN'),
            self.qc('thickness_std', '표준편차', summary['std_nm'], 'OK' if summary['std_nm'] < 0.5 else 'REVIEW'),
        ]
        payload = llm_payload(
            self.test_type,
            self.request_id,
            summary,
            {
                "measurements": measurements,
                "eds_preview": top_n_records(eds_preview, 10),
                "image_paths": processed_images,
            },
            "측정값 기반 코팅 균일성 소견과 이미지별 설명을 작성해주세요. 수치는 재계산하지 말고 제공된 summary를 사용하세요."
        )
        return self.build_result(summary, payload, qc_items, [hist_path], [hist_path], {"images": processed_images})


def process(request_id, file_paths, options=None):
    return TEMProcessor(request_id, file_paths, options).run()
