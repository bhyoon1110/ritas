"""Invented projects for figure tests: no measured data, only the shapes the code reads."""

from app.xrd_workbench.core.report import new_document


def make_sample(
    sid,
    name,
    phases=(),
    peaks=(),
    *,
    unit='cps',
    axis_kind='two_theta',
    points=101,
    height=1000,
    references=None,
):
    """phases: [(name, card, db)], peaks: [(no, x, [phase names])], references: {name: [(x, I)]}."""
    xs = [10 + 0.5 * i for i in range(points)]
    ys = [height * (1 + (i % 7)) / 7 for i in range(points)]
    phase_list = [
        dict(name=n, name_raw=n, card=c, db=d, page=1, id=f'{sid}-p{i}', source='r.pdf')
        for i, (n, c, d) in enumerate(phases)
    ]
    observed, assignments = [], []
    for no, x, names in peaks:
        observed.append(dict(no=no, x=x, x_raw=str(x), page=1, height_raw=str(height)))
        assignments.append(
            dict(
                no=no,
                x=x,
                x_raw=str(x),
                page=1,
                matches=[dict(phase=n, hkl='1 0 0') for n in names],
            )
        )
    reference_peaks = [
        dict(phase=n, no=i, x=x, x_raw=str(x), intensity=inten, intensity_raw=str(inten), page=1)
        for n, rows in (references or {}).items()
        for i, (x, inten) in enumerate(rows, 1)
    ]
    return dict(
        id=sid,
        name=name,
        measurement=dict(
            x=xs,
            y=ys,
            line_numbers=list(range(1, points + 1)),
            intensity_unit=unit,
            axis_kind=axis_kind,
            axis_unit='deg' if axis_kind else None,
        ),
        phases=phase_list,
        observed_peaks=observed,
        assignments=assignments,
        reference_peaks=reference_peaks,
        sources=[],
        warnings=[],
        quantities=[],
        reports={},
    )


def make_project(samples):
    return dict(schema_version=1, id='fig-project', samples=samples, sources=[], warnings=[])


def make_document(project, table=False, **figure):
    """A new document with the given figure settings; the quantity table is off unless `table`."""
    document = new_document(project)
    document['figure'].update(figure)
    document['quantity_table']['include'] = table
    return document


def make_trace_sample(
    sid='t1',
    heights=(1000.0, 20.0, 10.0),
    refs=None,
    phase=('PhaseA', '00-001', 'DB1'),
):
    """One phase whose reference rows are (x, Norm. I., hkl). Each reference row has an assigned
    observed peak (0.1 degree higher) carrying the height (cps) at the same position in `heights`."""
    refs = refs or [(25.0, 100.0, '1 0 0'), (35.0, 40.0, '1 1 0'), (45.0, 10.0, '2 0 0')]
    sample = make_sample(sid, sid, [phase], [])
    sample['reference_peaks'] = [
        dict(
            phase=phase[0],
            no=i,
            x=x,
            x_raw=str(x),
            intensity=inten,
            intensity_raw=str(inten),
            hkl_raw=hkl,
            page=1,
        )
        for i, (x, inten, hkl) in enumerate(refs, 1)
    ]
    for i, ((x, _inten, hkl), height) in enumerate(zip(refs, heights), 1):
        observed_x = round(x + 0.1, 6)
        sample['observed_peaks'].append(
            dict(no=i, x=observed_x, x_raw=str(observed_x), page=1, height_raw=str(height))
        )
        sample['assignments'].append(
            dict(
                no=i,
                x=observed_x,
                x_raw=str(observed_x),
                page=1,
                matches=[dict(phase=phase[0], hkl=hkl)],
            )
        )
    return sample
