"""Save-time domain rules live in core so any storage backend applies them identically."""

import pytest

from app.xrd_workbench.core.errors import RevisionConflict
from app.xrd_workbench.core.report import carry_review_flags


def entry(sample_id, fingerprint, reviewed=False, required=False):
    return {
        'sample_id': sample_id,
        'analysis_fingerprint': fingerprint,
        'reviewed': reviewed,
        'analysis_review_required': required,
    }


def test_changed_fingerprint_requires_review_until_reviewed():
    current = {'samples': [entry('a', 'old'), entry('b', 'same')]}
    clean = {'samples': [entry('a', 'new'), entry('b', 'same')]}
    carry_review_flags(current, clean)
    assert clean['samples'][0]['analysis_review_required'] is True
    assert clean['samples'][1]['analysis_review_required'] is False
    clean = {'samples': [entry('a', 'new', reviewed=True), entry('b', 'same')]}
    carry_review_flags(current, clean)
    assert clean['samples'][0]['analysis_review_required'] is False


def test_pending_review_carries_over_and_first_save_sets_nothing():
    current = {'samples': [entry('a', 'same', required=True)]}
    clean = {'samples': [entry('a', 'same')]}
    carry_review_flags(current, clean)
    assert clean['samples'][0]['analysis_review_required'] is True
    first = {'samples': [entry('a', 'x')]}
    carry_review_flags(None, first)
    assert first['samples'][0]['analysis_review_required'] is False


def test_revision_conflict_is_a_value_error_for_existing_handlers():
    assert issubclass(RevisionConflict, ValueError)
    with pytest.raises(ValueError):
        raise RevisionConflict('x')
