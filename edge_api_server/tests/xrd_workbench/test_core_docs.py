"""Every core module and public function/class explains itself (handoff readability, spec G5)."""

import ast

import pytest

from app.xrd_workbench.config import ROOT

CORE = sorted((ROOT / 'core').rglob('*.py'))


@pytest.mark.parametrize('path', CORE, ids=lambda p: str(p.relative_to(ROOT)))
def test_core_module_and_public_definitions_have_docstrings(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    missing = [] if ast.get_docstring(tree) else ['<module>']
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith('_'):
            if not ast.get_docstring(node):
                missing.append(node.name)
    assert not missing, f'{path.name}: add docstrings for {missing}'
