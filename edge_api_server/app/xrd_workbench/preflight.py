"""Check XRD renderer imports in the deployment interpreter; never install packages."""

import importlib
import sys


DEPENDENCIES = (
    ('matplotlib.font_manager', 'matplotlib'),
    ('matplotlib.backends.backend_agg', 'matplotlib'),
    ('reportlab.pdfgen.canvas', 'reportlab'),
    ('pdfplumber', 'pdfplumber'),
    ('pptx', 'python-pptx'),
    ('PIL.Image', 'Pillow'),
)


def check_runtime():
    failures = []
    for module, package in DEPENDENCIES:
        try:
            importlib.import_module(module)
        except (ImportError, OSError) as exc:
            failures.append(f'{package} ({module}): {type(exc).__name__}: {exc}')
    if not failures:
        try:
            for name in ('layout', 'html', 'pdf', 'posco_pptx', 'posco_preview', 'package'):
                importlib.import_module(f'app.xrd_workbench.render.{name}')
        except (ImportError, OSError) as exc:
            failures.append(f'XRD renderer: {type(exc).__name__}: {exc}')
    return failures


def main():
    print(f'Python: {sys.executable}')
    failures = check_runtime()
    if failures:
        print('XRD 런타임 점검 실패:', file=sys.stderr)
        for failure in failures:
            print(f'  - {failure}', file=sys.stderr)
        print('서비스가 사용하는 이 가상환경에 edge_api_server/requirements.txt를 설치하세요. '
              '폐쇄망 설치 방법: XRD_WORKBENCH_MIGRATION.md. Git pull/bundle만으로 패키지는 설치되지 않습니다.',
              file=sys.stderr)
        return 1
    print('XRD 렌더링 라이브러리 로딩 정상. 실제 시료의 미리보기/내보내기는 별도 확인하세요.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
