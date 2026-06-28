from __future__ import annotations

import json
from pathlib import Path

import pytest

from ftir.assignment_libraries import (
    AssignmentLibraryError,
    AssignmentLibraryStore,
    DEFAULT_LIBRARY_ID,
    parse_assignment_library,
)
from ftir.findings import assign_group_candidates
from ftir.plotting import _peak_assignment


DEFAULT_CSV = Path(__file__).resolve().parents[1] / "ftir" / "resources" / "func_groups.csv"
BUNDLED_DIR = DEFAULT_CSV.parent / "assignment_libraries"


def library_json(name: str, assignment_name: str, center: float = 1700) -> bytes:
    return json.dumps({
        "name": name,
        "description": "test library",
        "assignments": [{
            "centerWavenumber": center,
            "tolerance": 25,
            "name": assignment_name,
            "color": "#123456",
            "note": "test assignment",
        }],
    }).encode()


def test_store_seeds_lists_uploads_and_deletes_libraries(tmp_path: Path) -> None:
    store = AssignmentLibraryStore(tmp_path / "libraries", DEFAULT_CSV)

    initial = store.summaries()
    initial_ids = {item["id"] for item in initial}
    assert DEFAULT_LIBRARY_ID in initial_ids
    assert "engineering-plastic-commodity-peaks" in initial_ids
    assert "battery-electrolyte-peaks" in initial_ids
    assert len(initial_ids) == 1 + len(list(BUNDLED_DIR.glob("*.json")))
    assert [
        item["defaultSelected"]
        for item in initial
        if item["id"] == DEFAULT_LIBRARY_ID
    ] == [True]
    assert len(store.get(DEFAULT_LIBRARY_ID).detail()["assignments"]) == 47

    saved = store.save("melamine.json", library_json("Melamine", "N-H marker"))
    assert saved.library_id == "melamine"
    assert "melamine" in {item["id"] for item in store.summaries()}

    store.delete("melamine")
    assert "melamine" not in {item["id"] for item in store.summaries()}


def test_store_upgrades_legacy_seed_marker_once(tmp_path: Path) -> None:
    root = tmp_path / "libraries"
    root.mkdir()
    (root / f"{DEFAULT_LIBRARY_ID}.csv").write_bytes(DEFAULT_CSV.read_bytes())
    (root / ".initialized").write_text("1\n", encoding="ascii")
    store = AssignmentLibraryStore(root, DEFAULT_CSV)

    seeded_ids = {item["id"] for item in store.summaries()}

    assert DEFAULT_LIBRARY_ID in seeded_ids
    assert "engineering-plastic-engineering-peaks" in seeded_ids

    store.delete("engineering-plastic-engineering-peaks")
    after_delete = {item["id"] for item in store.summaries()}

    assert "engineering-plastic-engineering-peaks" not in after_delete


def test_store_creates_and_updates_editor_library(tmp_path: Path) -> None:
    store = AssignmentLibraryStore(tmp_path / "libraries", DEFAULT_CSV)
    created = store.write(
        "editor-test",
        {
            "name": "Editor Test",
            "description": "",
            "assignments": [{
                "centerWavenumber": 1700,
                "tolerance": 20,
                "name": "C=O",
                "color": "#123456",
                "note": "",
            }],
        },
        create_only=True,
    )
    assert created.name == "Editor Test"

    updated = store.write(
        "editor-test",
        {
            "name": "Editor Test Updated",
            "description": "changed",
            "assignments": [{
                "centerWavenumber": 1710,
                "tolerance": 15,
                "name": "C=O updated",
                "color": "#654321",
                "note": "",
            }],
        },
        create_only=False,
    )

    assert updated.name == "Editor Test Updated"
    assert updated.assignments[0].center_wn == 1710


def test_selected_libraries_keep_one_assignment_candidate_per_library() -> None:
    first = parse_assignment_library(
        "material-a.json",
        library_json("Material A", "Carbonyl A"),
    )
    second = parse_assignment_library(
        "material-b.json",
        library_json("Material B", "Carbonyl B", center=1710),
    )
    func_groups = first.as_func_groups() + second.as_func_groups()

    candidates = assign_group_candidates(1705, func_groups)

    assert [(item["library_id"], item["name"]) for item in candidates] == [
        ("material-a", "Carbonyl A"),
        ("material-b", "Carbonyl B"),
    ]


def test_bundled_generated_libraries_use_multiple_marker_colors() -> None:
    for path in sorted(BUNDLED_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        colors = {
            assignment["color"]
            for assignment in payload["assignments"]
        }

        assert len(colors) > 1, path.name


def test_peak_assignment_prefers_non_general_library_color() -> None:
    func_groups = [
        (1700, 25, "C=O stretch", "#64748b", "", "general-ftir", "General FTIR"),
        (1702, 25, "Material marker", "#dc2626", "", "material-a", "Material A"),
    ]

    assignment = _peak_assignment(1701, func_groups)

    assert assignment["color"] == "#dc2626"
    assert assignment["display_name"] == "C=O stretch<br>Material marker"


def test_library_rejects_invalid_color() -> None:
    content = json.dumps({
        "name": "Invalid",
        "assignments": [{
            "centerWavenumber": 1700,
            "tolerance": 20,
            "name": "C=O",
            "color": "red",
        }],
    }).encode()

    with pytest.raises(AssignmentLibraryError) as exc_info:
        parse_assignment_library("invalid.json", content)

    assert exc_info.value.code == "INVALID_ASSIGNMENT_LIBRARY"
