"""Bounded page rasterization using the PDFium dependency already shipped with pdfplumber."""
import io
import threading

import pdfplumber

# PDFium is not thread-safe. Source review and exports must share this lock.
PDF_RENDER_LOCK = threading.RLock()


def page_images(data, *, max_edge=2000, image_format='PNG'):
    with PDF_RENDER_LOCK, pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            width, height = float(page.width), float(page.height)
            if min(width, height) <= 0:
                raise ValueError('PDF 페이지 크기가 올바르지 않습니다.')
            dpi = min(200, max_edge * 72 / max(width, height))
            image = page.to_image(resolution=dpi, antialias=True).original
            try:
                output = io.BytesIO()
                image.save(output, format=image_format, **({'quality':88} if image_format == 'JPEG' else {}))
                yield output.getvalue()
            finally:
                image.close()
                page.close()
