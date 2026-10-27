"""Sample identity colours stay consistent across report grouping and browser export."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from app.xrd_workbench.core.figure import build_plan
from app.xrd_workbench.core.sample_colors import sample_color
from app.xrd_workbench.render.viewer_state import prepare_state, prepare_views
from .figure_fixtures import make_document, make_project, make_sample


def test_report_groups_and_reordering_keep_all_24_sample_colours():
    p = make_project([make_sample(f's{i}', f'시료 {i + 1}') for i in range(24)])
    original = json.dumps(p)
    doc = make_document(p)
    first = build_plan(p, doc)['panels'][0]['curves']
    colors = {c['sample_id']: c['color'] for c in first}
    assert len(colors) == len(set(colors.values())) == 24
    doc['samples'].reverse()
    doc['samples'][3]['included'] = False
    doc['figure']['groups'] = [{'title': '두 번째 묶음', 'samples': [f's{i}' for i in range(12, 24)]}]
    for panel in build_plan(p, doc)['panels']:
        for curve in panel['curves']:
            assert curve['color'] == colors[curve['sample_id']]
    # A single-sample detail page keeps the same colour as the overview.
    for entry in doc['samples']:
        entry['included'] = entry['sample_id'] == 's23'
    assert build_plan(p, doc)['panels'][0]['curves'][0]['color'] == colors['s23']
    assert json.dumps(p) == original


def test_browser_and_report_palette_agree_for_256_samples():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is a test-time dependency for browser/report colour parity')
    module = Path(__file__).resolve().parents[2] / 'app/xrd_workbench/web/viewer/sample_colors.mjs'
    program = f'import {{sampleColor}} from {json.dumps(module.as_uri())}; console.log(JSON.stringify(Array.from({{length:256}},(_,i)=>sampleColor(i))));'
    result = subprocess.run([node, '--input-type=module', '-e', program], check=True, capture_output=True, text=True)
    expected = [sample_color(i) for i in range(256)]
    assert json.loads(result.stdout) == expected
    assert len(set(expected)) == 256


@pytest.mark.parametrize('saved', [None, {}, {'version': 1, 'projectId': 'fig-project', 'colors': {'s23': '#123456'}}])
def test_filtered_delivery_keeps_original_default_colours_and_custom_overrides(saved):
    p = make_project([make_sample(f's{i}', f'시료 {i + 1}') for i in range(24)])
    target = make_project([p['samples'][23], p['samples'][7]])
    target['id'] = 'delivered'
    before = json.dumps(saved)
    result = prepare_state(p, saved, target)
    assert result['projectId'] == 'delivered'
    assert result['colors'] == {'s7': sample_color(7), 's23': '#123456' if saved else sample_color(23)}
    if saved:
        assert prepare_views(p, [{'name': '내 보기', 'state': saved}], target)[0]['state']['colors'] == result['colors']
    assert json.dumps(saved) == before
