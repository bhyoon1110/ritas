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


DEFAULT_CSV = Path(__file__).resolve().parents[1] / "ftir" / "resources" / "func_groups.csv"


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
    assert [item["id"] for item in initial] == [DEFAULT_LIBRARY_ID]
    assert initial[0]["defaultSelected"] is True
    assert len(store.get(DEFAULT_LIBRARY_ID).detail()["assignments"]) == 47

    saved = store.save("melamine.json", library_json("Melamine", "N-H marker"))
    assert saved.library_id == "melamine"
    assert {item["id"] for item in store.summaries()} == {
        DEFAULT_LIBRARY_ID,
        "melamine",
    }

    store.delete("melamine")
    assert [item["id"] for item in store.summaries()] == [DEFAULT_LIBRARY_ID]


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
