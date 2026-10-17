import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeDraftChanges } from '../../app/xrd_workbench/web/report/report_merge.mjs';
test('unsaved prose survives analysis refresh while latest identities and re-review stay authoritative', () => {
  const base = {
    metadata: { title: 'saved', client: 'old client' },
    summary: '',
    llm: {},
    revision: 1,
    samples: [
      {
        sample_id: 's',
        manual_note: 'saved',
        notes: [
          {
            id: 'old',
            kind: 'quantities',
            text: 'old fact',
            selected: true,
            edited: false,
            evidence: [],
          },
        ],
        retained_notes: [],
      },
    ],
  };
  const current = structuredClone(base);
  current.metadata.title = 'unsaved';
  current.samples[0].notes[0].text = 'author prose';
  const latest = structuredClone(base);
  latest.revision = 2;
  latest.metadata.client = 'other saved client';
  latest.samples[0].analysis_review_required = true;
  latest.samples[0].reviewed = false;
  latest.samples[0].notes[0].id = 'new';
  latest.samples[0].notes[0].text = 'new fact';
  latest.samples[0].manual_note = 'other saved change';
  const value = mergeDraftChanges(latest, current, base);
  assert.equal(value.metadata.title, 'unsaved');
  assert.equal(value.samples[0].notes[0].text, 'author prose');
  assert.equal(value.samples[0].notes[0].id, 'new');
  assert.equal(value.samples[0].manual_note, 'other saved change');
  assert.equal(value.samples[0].analysis_review_required, true);
  latest.samples[0].notes = [];
  assert.equal(
    mergeDraftChanges(latest, current, base).samples[0].retained_notes[0].text,
    'author prose',
  );
  assert.equal(value.metadata.client, 'other saved client');
});
