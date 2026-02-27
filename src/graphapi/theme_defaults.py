from __future__ import annotations

import os
from pathlib import Path

from graphrender import default_theme_css

from .profile_contract import ThemeCreateRequestV1


def _workspace_default_css_path() -> Path:
    # graphapi/app.py => graphapi => src => GraphAPI => GraphRapids workspace root
    return Path(__file__).resolve().parents[3] / "default.css"


def load_default_render_css() -> str:
    override_path = os.getenv("GRAPHAPI_DEFAULT_RENDER_CSS_PATH", "").strip()
    candidates: list[Path] = []
    if override_path:
        candidates.append(Path(override_path).expanduser())
    candidates.append(_workspace_default_css_path())

    for path in candidates:
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if content.strip():
                    return content
        except OSError:
            continue

    return default_theme_css()


def default_theme_create_request() -> ThemeCreateRequestV1:
    return ThemeCreateRequestV1.model_validate(
        {
            "themeId": "default",
            "name": "Default Render Theme",
            "cssBody": load_default_render_css(),
            "variables": {},
        }
    )
