from __future__ import annotations

import pytest
from pydantic import ValidationError

from graphapi.profile_contract import (
    ThemeEditableFieldsV1,
    ThemeVariableV1,
    compile_theme_render_css,
)


def test_compile_theme_render_css_emits_three_lines_per_variable_in_sorted_order() -> None:
    css = compile_theme_render_css(
        ".node > rect { fill: var(--background-color); }\n",
        {
            "z-index-color": {"lightValue": "white", "darkValue": "black"},
            "background-color": {"lightValue": "#fff", "darkValue": "#000"},
        },
    )
    expected = """\
:root {
  color-scheme: light dark;
  --light-background-color: #fff;
  --dark-background-color: #000;
  --background-color: light-dark(var(--light-background-color), var(--dark-background-color));
  --light-z-index-color: white;
  --dark-z-index-color: black;
  --z-index-color: light-dark(var(--light-z-index-color), var(--dark-z-index-color));
}

.node > rect { fill: var(--background-color); }
"""
    assert css == expected


def test_theme_variable_key_is_normalized_without_leading_dashes() -> None:
    editable = ThemeEditableFieldsV1.model_validate(
        {
            "name": "Theme",
            "cssBody": ".node { fill: var(--background-color); }",
            "variables": {
                "--Background_Color": {
                    "valueType": "color",
                    "lightValue": "white",
                    "darkValue": "black",
                }
            },
        }
    )
    assert list(editable.variables.keys()) == ["background-color"]


def test_theme_css_body_rejects_managed_variable_shadowing() -> None:
    with pytest.raises(ValidationError, match="cssBody must not declare managed theme variable"):
        ThemeEditableFieldsV1.model_validate(
            {
                "name": "Theme",
                "cssBody": ":root { --background-color: red; }",
                "variables": {
                    "background-color": {
                        "valueType": "color",
                        "lightValue": "white",
                        "darkValue": "black",
                    }
                },
            }
        )


def test_theme_float_values_must_be_parseable_numbers() -> None:
    with pytest.raises(ValidationError, match="parseable numbers"):
        ThemeVariableV1.model_validate(
            {
                "valueType": "float",
                "lightValue": "not-a-number",
                "darkValue": "2.3",
            }
        )
