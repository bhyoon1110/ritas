import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSections, sectionHTML } from '../../app/xrd_workbench/web/analysis/sections.mjs';

const report = {
  phases: [
    { name: 'PhaseA', formula: 'Ti O2', card: '1', db: 'DB', fom_raw: '0.9', page: 1 },
    { name: 'PhaseB', formula: 'Ca C O3', card: '2', db: 'DB', fom_raw: '1.2', page: 1 },
  ],
  observed_peaks: [
    { no: 1, x: 21, x_raw: '21.00', height_raw: '500', page: 2 },
    { no: 2, x: 25, x_raw: '25.00', height_raw: '40', page: 2 },
    { no: 3, x: 31, x_raw: '31.00', height_raw: '90', page: 3 },
  ],
  assignments: [
    {
      no: 1,
      x: 21,
      page: 4,
      phase_raw: 'PhaseA: 1 0 0',
      matches: [{ phase: 'PhaseA', hkl: '1 0 0' }],
    },
    { no: 2, x: 25, page: 4, phase_raw: '', matches: [] },
    {
      no: 3,
      x: 31,
      page: 4,
      phase_raw: 'PhaseB: 1 1 0',
      matches: [{ phase: 'PhaseB', hkl: '1 1 0' }],
    },
  ],
  reference_peaks: [
    {
      phase: 'PhaseA',
      no: 1,
      x_raw: '21.0',
      d_raw: '4.2',
      hkl_raw: '1 0 0',
      intensity_raw: '100',
      page: 5,
    },
    {
      phase: 'PhaseA',
      no: 2,
      x_raw: '35.0',
      d_raw: '2.5',
      hkl_raw: '1 1 1',
      intensity_raw: '30',
      page: 6,
    },
    {
      phase: 'PhaseB',
      no: 1,
      x_raw: '31.0',
      d_raw: '2.9',
      hkl_raw: '1 1 0',
      intensity_raw: '100',
      page: 6,
    },
  ],
  quantities: [],
};

test('sections follow the report headings: phases, one merged Peak List, d-I List per phase', () => {
  const sections = buildSections(report);
  assert.deepEqual(
    sections.map((s) => s.title),
    ['상 목록', 'Peak List (통합)', 'd-I List · PhaseA', 'd-I List · PhaseB'],
  );
});

test('the Peak List merges every page into one table in peak order and shows the assignment', () => {
  const peaks = buildSections(report).find((s) => s.id === 'peaklist');
  assert.deepEqual(
    peaks.rows.map((r) => r.cells[0]),
    ['1', '2', '3'],
  );
  assert.deepEqual(peaks.pages, [2, 3]);
  assert.equal(peaks.rows[0].cells.at(-1), 'PhaseA: 1 0 0');
  assert.equal(peaks.rows[1].cells.at(-1), '배정 없음');
  assert.equal(peaks.rows[2].page, 3);
  assert.match(peaks.note, /2개 쪽\(p\.2, p\.3\)을 하나로/);
  assert.match(peaks.note, /배정 없는 피크 1개/);
});

test('each d-I List keeps only its own phase and the original tokens', () => {
  const a = buildSections(report).find((s) => s.title === 'd-I List · PhaseA');
  assert.deepEqual(
    a.rows.map((r) => r.cells),
    [
      ['1', '21.0', '4.2', '1 0 0', '100'],
      ['2', '35.0', '2.5', '1 1 1', '30'],
    ],
  );
  assert.deepEqual(a.pages, [5, 6]);
});

test('a phase listed without d-I rows says so instead of disappearing', () => {
  const lone = { ...report, phases: [...report.phases, { name: 'PhaseC', page: 1 }] };
  const c = buildSections(lone).find((s) => s.title === 'd-I List · PhaseC');
  assert.equal(c.rows.length, 0);
  assert.match(c.note, /d-I 행이 없습니다/);
});

test('quantities appear as their own section only when the report has them', () => {
  const wppf = {
    phases: [],
    observed_peaks: [],
    assignments: [],
    reference_peaks: [],
    quantities: [{ phase: 'PhaseA', raw: '12.3(4)', unit: 'wt%', page: 2 }],
  };
  assert.deepEqual(
    buildSections(wppf).map((s) => s.title),
    ['정량 (WPPF)'],
  );
  assert.deepEqual(buildSections(wppf)[0].rows[0].cells, ['PhaseA', '12.3(4)', 'wt%']);
});

test('the section table is escaped and rows carry their page for the source view', () => {
  const evil = { ...report, phases: [{ name: '<b>x</b>', page: 1 }] };
  const html = sectionHTML(buildSections(evil)[0]);
  assert.ok(!html.includes('<b>x</b>') && html.includes('&lt;b&gt;x&lt;/b&gt;'));
  assert.ok(html.includes('data-source-page="1"'));
});
