"""
E1.3 (docs/ARCHITECTURE_SCORE_10_PLAN.md): `extract` and `paginate` must call
the SAME `ast_to_html` function object, and `design-compile` and paginate's
CSS fallback must call the SAME `emit_css` function object. Two copies of
either could silently drift -- pagination would then no longer render
exactly what the text-integrity gate verified against `extract`'s output.

This asserts identity, not merely equal output: importing the same name from
the same module twice is not proof by itself if a caller had instead kept a
local, private re-implementation.
"""

from __future__ import annotations

import stages.design_compile_stage as design_compile_stage
import stages.extract_stage as extract_stage
import stages.idml_stage as idml_stage
import stages.paginate_stage as paginate_stage
import stages.rendering as rendering
import stages.typst_stages as typst_stages


def test_extract_and_rendering_share_one_ast_to_html_object():
    assert extract_stage.ast_to_html is rendering.ast_to_html


def test_design_compile_and_rendering_share_one_emit_css_object():
    assert design_compile_stage.emit_css is rendering.emit_css


def test_paginate_imports_ast_to_html_and_emit_css_from_rendering():
    """paginate_stage imports both lazily inside its stage function (to avoid
    a module-load-time dependency on design_compile_stage); assert the import
    lines themselves name stages.rendering, not a private sibling path."""
    import inspect

    source = inspect.getsource(paginate_stage)
    assert "from stages.rendering import ast_to_html" in source
    assert "from stages.rendering import emit_css" in source
    assert "from stages.extract_stage import" not in source
    assert "_emit_css" not in source


def test_idml_and_typst_import_ast_to_html_from_rendering():
    """Both call it via a lazy import inside their stage function; assert the
    import line itself names stages.rendering, not a private sibling path."""
    import inspect

    idml_source = inspect.getsource(idml_stage)
    typst_source = inspect.getsource(typst_stages)
    assert "from stages.rendering import ast_to_html" in idml_source
    assert "from stages.rendering import ast_to_html" in typst_source
    assert "from stages.extract_stage import" not in idml_source
    assert "from stages.extract_stage import" not in typst_source
