from __future__ import annotations

import pytest
from pydantic import ValidationError

from graphapi.profile_contract import (
    ThemeEditableFieldsV1,
    ThemeVariableV1,
    compile_theme_render_css,
)


def test_compile_theme_render_css_emits_type_specific_lines_per_variable_in_sorted_order() -> None:
    css = compile_theme_render_css(
        ".node > rect { fill: var(--background-color); }\n",
        {
            "z-index-color": {
                "valueType": "color",
                "lightValue": "white",
                "darkValue": "black",
                "value": None,
            },
            "node-gap": {
                "valueType": "length",
                "value": "12px",
                "lightValue": None,
                "darkValue": None,
            },
            "background-color": {
                "valueType": "color",
                "lightValue": "#fff",
                "darkValue": "#000",
                "value": None,
            },
        },
    )
    expected = """\
:root {
  color-scheme: light dark;
  --light-background-color: #fff;
  --dark-background-color: #000;
  --background-color: light-dark(var(--light-background-color), var(--dark-background-color));
  --node-gap: 12px;
  --light-z-index-color: white;
  --dark-z-index-color: black;
  --z-index-color: light-dark(var(--light-z-index-color), var(--dark-z-index-color));
}

.node > rect { fill: var(--background-color); }
"""
    assert css == expected


def test_compile_theme_render_css_without_color_variables_emits_single_value_lines() -> None:
    css = compile_theme_render_css(
        ".edge { stroke-width: var(--edge-width); }\n",
        {
            "edge-width": {
                "valueType": "length",
                "value": "2px",
                "lightValue": None,
                "darkValue": None,
            }
        },
    )
    expected = """\
:root {
  --edge-width: 2px;
}

.edge { stroke-width: var(--edge-width); }
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
                "value": "not-a-number",
            }
        )


def test_theme_color_values_require_light_and_dark_value_pair() -> None:
    with pytest.raises(ValidationError, match="must define both lightValue and darkValue"):
        ThemeVariableV1.model_validate(
            {
                "valueType": "color",
                "lightValue": "white",
            }
        )


def test_theme_non_color_values_require_single_value() -> None:
    with pytest.raises(ValidationError, match="must define value"):
        ThemeVariableV1.model_validate(
            {
                "valueType": "length",
                "lightValue": "1px",
                "darkValue": "2px",
            }
        )
