"""Page items shared by the layout modules. All dimensions in mm."""

import hashlib
from urllib.parse import quote, urlencode

from ..render import theme
from ..render.fonts import wrap_lines


def leading_of(size):
    """Line height (mm) of body text at `size` pt."""
    return size * 1.55 / 2.83464567


def link_target(item, link_base=None, storage_id=None):
    """Where a source link points. Inside the delivery package it is the relative path of the
    packed original; a PDF or PPTX saved on its own has no such folder beside it, so its links
    point at the running app's file route instead."""
    if link_base and storage_id and item.get('source_id'):
        return f"{link_base.rstrip('/')}/api/files/{quote(storage_id, safe='')}/{quote(item['source_id'], safe='')}"
    return item['href']


def sample_viewer_path(sample_id):
    """Portable package entry filename; display names never identify or name files."""
    key = hashlib.sha256(sample_id.encode('utf-8')).hexdigest()[:24]
    return f'Viewer/sample-{key}.html'


def viewer_target(sample_id=None, link_base=None, storage_id=None):
    """Open the exact sample from a report, including local PowerPoint hyperlinks.

    Local sample links name a small entry file so Office need not preserve a URL
    fragment. Online links also carry selection in the query. Legacy fragments
    remain supported by the viewer, and the summary opens the full comparison.
    """
    selection = {'view': 'multi'} if sample_id is None else {'view': 'single', 'sample': sample_id}
    if link_base and storage_id:
        params = {'project': storage_id, 'step': 'viewer', **selection}
        target = f"{link_base.rstrip('/')}/?{urlencode(params)}"
    elif sample_id is not None:
        return sample_viewer_path(sample_id)
    else:
        target = '뷰어.html'
    return target + '#' + urlencode(selection)


def text_item(text, x, y, w, size=10, bold=False, color=theme.TEXT, leading=None, align='left'):
    lines = wrap_lines(text, w, size, bold)
    leading = leading or leading_of(size)
    return dict(
        type='text',
        text=text,
        lines=lines,
        x=x,
        y=y,
        w=w,
        h=len(lines) * leading,
        size=size,
        bold=bold,
        color=color,
        leading=leading,
        align=align,
    )
