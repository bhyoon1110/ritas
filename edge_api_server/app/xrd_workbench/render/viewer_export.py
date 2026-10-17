"""One-file client viewer using the same UI, model, and local Plotly bundle."""

import hashlib
import json
import re

from plotly.offline import get_plotlyjs

from ..config import ROOT

WEB = ROOT / 'web'
# Inlined in this order into the offline viewer; each module's imports must come earlier.
VIEWER_BUNDLE = ('shared/dom.mjs', 'shared/api.mjs', 'shared/file_hash.mjs', 'shared/resumable_upload.mjs', 'viewer/model.mjs', 'viewer/upload_summary.mjs', 'viewer/raw_diagnostics.mjs', 'viewer/viewer.mjs')


def safe_inline(source):
    return re.sub(r'</script', r'<\\/script', source, flags=re.I)


_IMPORT = re.compile(r"^import\s[^;]*?\sfrom\s+['\"][^'\"]+['\"];?[ \t]*\n", re.M | re.S)
_EXPORT = re.compile(r'^export ', re.M)


def strip_module_syntax(source: str) -> str:
    """Make ES module source inline-able: drop import statements (also multi-line) and export keywords."""
    return _EXPORT.sub('', _IMPORT.sub('', source))


def export_html(project: dict, state: dict, views: list | None = None) -> str:
    """The one-file viewer. `views` are already-validated named views (see prepare_views); when
    there are none the output is byte-identical to a viewer without the feature."""
    template = (WEB / 'index.html').read_text(encoding='utf-8')
    css = (WEB / 'viewer' / 'style.css').read_text(encoding='utf-8')
    script = '\n'.join(
        strip_module_syntax((WEB / name).read_text(encoding='utf-8')) for name in VIEWER_BUNDLE
    )
    identity_fields = {
        'project_id': project['id'],
        'analysis_revision': project.get('analysis_revision', 0),
        'state': state,
    }
    if views:
        identity_fields['views'] = views
    identity = json.dumps(
        identity_fields,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )
    snapshot_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    embedded = {'project': project, 'state': state, 'snapshotId': snapshot_id}
    if views:
        embedded['views'] = views
    payload = json.dumps(
        embedded,
        ensure_ascii=False,
        allow_nan=False,
    )
    for char, escaped in [
        ('&', '\\u0026'),
        ('<', '\\u003c'),
        ('>', '\\u003e'),
        ('\u2028', '\\u2028'),
        ('\u2029', '\\u2029'),
    ]:
        payload = payload.replace(char, escaped)
    # The offline viewer is a delivered file, not a step of the app: drop the app shell.
    for shell_part in (
        '<link rel="stylesheet" href="/shared/shell.css">',
        '<script type="module" src="/shared/shell.mjs"></script>',
        ' data-step="viewer"',
    ):
        template = template.replace(shell_part, '')
    template = template.replace(
        '<link rel="stylesheet" href="/viewer/style.css">', f'<style>{css}</style>'
    )
    template = template.replace(
        '<script src="/vendor/plotly.min.js"></script>',
        f'<script>{safe_inline(get_plotlyjs())}</script>',
    )
    template = template.replace(
        '<script type="module" src="/viewer/viewer.mjs"></script>',
        f'<script id="xrd-data" type="application/json">{payload}</script>\n'
        '<script>window.XRD_EMBEDDED=JSON.parse(document.getElementById("xrd-data").textContent);</script>\n'
        f'<script type="module">{safe_inline(script)}</script>',
    )
    # The primary path is native modules on Supermium 144. Offline recipients
    # using Chrome 49 need the same prebuilt, fully local ES5 fallback as the app.
    compat = ROOT.parent / 'static' / 'chrome49'
    manifest = json.loads((compat / 'manifest.json').read_text())
    runtime = (compat / manifest['runtime']).read_text()
    legacy = (WEB / 'viewer/viewer.legacy.js').read_text()
    legacy_css = (WEB / 'viewer/style.legacy.css').read_text()
    fallback_css = 'var s=document.createElement("style");s.textContent=' + json.dumps(legacy_css) + ';document.head.appendChild(s);'
    template = template.replace('</body>',
        '<script nomodule>' + safe_inline(runtime) + '</script>'
        '<script nomodule>' + safe_inline(fallback_css) + '</script>'
        '<script nomodule>' + safe_inline(legacy) + '</script></body>')
    return template
