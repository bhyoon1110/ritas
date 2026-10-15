from __future__ import annotations

import base64
from pathlib import Path

import pytest
from lxml import html as lxml_html

from app.xrd_portable_html import make_xrd_html_portable


def test_make_xrd_html_portable_embeds_plotly_without_raw_script_terminator(
    tmp_path: Path,
) -> None:
    asset = tmp_path / "plotly.min.js"
    asset_bytes = b"window.Plotly={ready:true};/* </script> */"
    asset.write_bytes(asset_bytes)
    source = (
        '<html><head><script charset="utf-8" src="/xrd/assets/plotly.min.js">'
        "</script></head><body><script>Plotly.newPlot('graph', [], {});</script></body></html>"
    )

    result = make_xrd_html_portable(source, asset_path=asset)

    assert 'data-xrd-embedded-plotly="true"' in result
    assert 'src="/xrd/assets/plotly.min.js"' not in result
    assert asset_bytes.decode("ascii") not in result
    assert base64.b64encode(asset_bytes).decode("ascii") in result
    assert "Plotly.newPlot('graph'" in result


def test_make_xrd_html_portable_is_idempotent(tmp_path: Path) -> None:
    asset = tmp_path / "plotly.min.js"
    asset.write_text("window.Plotly={};", encoding="utf-8")
    source = '<script src="/xrd/assets/plotly.min.js"></script>'

    once = make_xrd_html_portable(source, asset_path=asset)

    assert make_xrd_html_portable(once, asset_path=asset) == once


def test_portable_peak_list_keeps_uploaded_values_identical_to_preview(tmp_path: Path) -> None:
    from lim.xrd_plot import build_xrd_html

    raw = tmp_path / "sample.txt"
    raw.write_text("10 1\n25.309 100\n30 2\n", encoding="utf-8")
    cards = tmp_path / "cards"
    cards.mkdir()
    source = tmp_path / "Peak list.csv"
    source.write_text(
        "No.,2theta," + "e.s.d.," * 19 + 'Phase Name,Chemical Formula,Card No,Norm. I.,"Size, Å"\n'
        + "1,25.309," + "0.001," * 19 + '"Anatase, syn: 1 0 1",Ti O2,00-064-0863,4.0500,468.3250\n'
        + "2,25.310," + "0.001," * 19 + ",,00-064-0863,0,0\n"
        + "3,25.311," + "0.001," * 19 + ",,00-064-0863,,\n",
        encoding="utf-8",
    )
    preview = build_xrd_html([(str(raw), str(cards))], table_files=[str(source)])["html"]
    asset = tmp_path / "plotly.min.js"
    asset.write_text("window.Plotly={};", encoding="utf-8")
    saved = make_xrd_html_portable(preview, asset_path=asset)
    expected = [
        ["No.", "2theta", "Phase Name", "Chemical Formula", "Card No", "Norm. I.", "Size, Å"],
        ["1", "25.309", "Anatase, syn: 1 0 1", "Ti O2", "00-064-0863", "4.0500", "468.3250"],
        ["2", "25.310", "", "", "00-064-0863", "0", "0"],
        ["3", "25.311", "", "", "00-064-0863", "", ""],
    ]
    for report in (preview, saved):
        table = lxml_html.fromstring(report).find('.//table[@class="xrd-report-table xrd-peak-list-display"]')
        assert table is not None
        assert [[cell.text_content() for cell in row] for row in table.findall(".//tr")] == expected
        assert not table.findall(".//input")


def test_major_phase_excel_highlights_without_raw_match_in_preview_and_saved_report(tmp_path: Path) -> None:
    import plotly.graph_objects as go
    from lim.xrd_plot import build_report_html, parse_peak_list_table

    items = [
        {"label": f"Phase {category}", "color": "#e41a1c", "trace_idx": index,
         "category": category,
         "metadata": {"phase_name": f"Phase {category}", "formula": "Ti O2",
                      "card_no": "00-064-0863", "quality_mark": "S"},
         "match": {"score": 83.5, "matched_count": 4, "important_count": 5},
         "peaks": [{"no": "1", "two_theta": 25.309, "d": "3.516", "norm": 100.0, "hkl": "1 0 1"},
                   {"no": "2", "two_theta": 37.876, "d": "2.373", "norm": 21.7, "hkl": "0 0 4"}]}
        for index, category in enumerate(["major", "minor", "uncertain"], start=1)
    ]
    fig = go.Figure(go.Scatter(x=[10, 25.309, 30], y=[1, 100, 2], mode="lines", name="Sample"))
    source = tmp_path / "Peak list.csv"
    source.write_text("No.,2theta,Phase Name\n1,25.309,Phase major: 1 0 1\n2,37.876,Phase minor: 0 0 4\n", encoding="utf-8")
    preview = build_report_html(
        fig, sample_name="Sample", groups=[("Sample", "#000", items)],
        group_map={"Sample": [0]}, warnings=[], origin=False, first_stem="Sample",
        raw_line_indices=[0], highlight_groups={0: [0]},
        table_files=[str(source)], peak_tables=[parse_peak_list_table(str(source))],
    )
    asset = tmp_path / "plotly.min.js"
    asset.write_text("window.Plotly={};", encoding="utf-8")
    saved = make_xrd_html_portable(preview, asset_path=asset)
    for report in (preview, saved):
        phases = lxml_html.fromstring(report).get_element_by_id("xrd-phase-info")
        for index in (1, 2, 3):
            card = phases.find(f'.//article[@data-trace="{index}"]')
            assert card is not None
            assert ("raw 피크 대응" in card.text_content()) is (index != 1)
            assert ("83.5% (4/5)" in card.text_content()) is (index != 1)
            assert "PDF Card" in card.text_content()
            assert "QM" in card.text_content()
            assert "25.309" in card.text_content()
            highlighted = card.findall('.//tr[@class="xrd-phase-overlap-row"]')
            assert [row.find('td').text for row in highlighted] == (["1"] if index == 1 else [])


@pytest.mark.parametrize("loader", [
    '<script src="/xrd/assets/plotly.min.js"></script>',
    '<script data-xrd-embedded-plotly="true">window.Plotly={};</script>',
    '',
])
def test_portable_analysis_is_readonly_with_formatting_and_graph_controls_preserved(tmp_path, loader):
    asset = tmp_path / "plotly.js"
    asset.write_text("window.Plotly={};", encoding="utf-8")
    content = '<p><strong>최종 검토</strong> 결과 &amp; 주의사항</p><div contenteditable="true">수정한 문안</div>'
    source = '<html><head>' + loader + '</head><body>' + (
        '<div id="xrd-analysis-result" contenteditable="true" aria-label="분석결과 편집">'
        + content + '</div><button id="xrd-report-pdf-export">PDF Export</button>'
        '<div id="xrd-plot"></div><script>window.graphReady = true;</script></body></html>'
    )
    published = make_xrd_html_portable(source, asset_path=asset)
    root = lxml_html.fromstring(published)
    result = root.get_element_by_id("xrd-analysis-result")
    assert result.get("contenteditable") == "false"
    assert result.get("aria-readonly") == "true"
    assert result.get("aria-label") == "분석결과 (읽기 전용)"
    assert result.find('p/strong').text == '최종 검토'
    assert result.find('div').get('contenteditable') == 'false'
    assert result.text_content() == '최종 검토 결과 & 주의사항수정한 문안'
    assert root.get_element_by_id('xrd-report-pdf-export').text == 'PDF Export'
    assert 'window.graphReady = true;' in published
    assert 'window.readOnlyReport=true' not in published  # Do not change graph modes.
    assert make_xrd_html_portable(published, asset_path=asset) == published
    assert 'contenteditable="true"' in source  # Preview input is not modified.
