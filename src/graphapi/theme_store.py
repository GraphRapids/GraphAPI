from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from .profile_contract import (
    ThemeBundleV1,
    ThemeCreateRequestV1,
    ThemeEditableFieldsV1,
    ThemeListResponseV1,
    ThemeRecordV1,
    ThemeSummaryV1,
    ThemeUpdateRequestV1,
    ThemeVariableUpsertRequestV1,
    ThemeVariableV1,
    compile_theme_render_css,
    compute_theme_checksum,
    normalize_theme_id,
    normalize_theme_variable_key,
    utcnow,
)


class ThemeStoreError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class ThemeStore:
    def __init__(self, storage_path: Path, *, legacy_json_paths: list[Path] | None = None) -> None:
        self._storage_path = storage_path
        self._legacy_json_paths = [path.expanduser() for path in (legacy_json_paths or [])]
        self._lock = RLock()
        self._schema_ready = False

    @classmethod
    def from_env(cls) -> "ThemeStore":
        runtime_raw = os.getenv("GRAPHAPI_RUNTIME_DB_PATH", "").strip()
        theme_raw = os.getenv("GRAPHAPI_THEME_STORE_PATH", "").strip()
        default_runtime = Path.home() / ".cache" / "graphapi" / "runtime.v1.sqlite3"
        default_legacy_json = Path.home() / ".cache" / "graphapi" / "themes.v1.json"

        sqlite_suffixes = {".sqlite", ".sqlite3", ".db"}
        if runtime_raw:
            storage_path = Path(runtime_raw).expanduser()
        elif theme_raw and Path(theme_raw).suffix.lower() in sqlite_suffixes:
            storage_path = Path(theme_raw).expanduser()
        else:
            storage_path = default_runtime

        legacy_json_paths: list[Path] = []
        if theme_raw and Path(theme_raw).suffix.lower() == ".json":
            legacy_json_paths.append(Path(theme_raw).expanduser())
        if default_legacy_json != storage_path:
            legacy_json_paths.append(default_legacy_json)

        return cls(storage_path, legacy_json_paths=legacy_json_paths)

    def ensure_default_theme(self, request: ThemeCreateRequestV1) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT 1 FROM themes WHERE theme_id = ?",
                    (request.themeId,),
                ).fetchone()
                if row is not None:
                    return

                bundle = self._build_bundle(
                    theme_id=request.themeId,
                    theme_version=1,
                    editable=request,
                )
                self._insert_theme(conn, bundle, publish=True)

    def list_themes(self) -> ThemeListResponseV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT
                        t.theme_id,
                        t.name,
                        t.draft_version,
                        t.draft_updated_at,
                        t.draft_checksum,
                        (
                            SELECT MAX(p.theme_version)
                            FROM theme_published_versions p
                            WHERE p.theme_id = t.theme_id
                        ) AS published_version
                    FROM themes t
                    ORDER BY t.theme_id ASC
                    """
                ).fetchall()

                summaries = [
                    ThemeSummaryV1(
                        themeId=str(row["theme_id"]),
                        name=str(row["name"]),
                        draftVersion=int(row["draft_version"]),
                        publishedVersion=(
                            int(row["published_version"]) if row["published_version"] is not None else None
                        ),
                        updatedAt=self._parse_dt(str(row["draft_updated_at"])),
                        checksum=str(row["draft_checksum"]),
                    )
                    for row in rows
                ]
                return ThemeListResponseV1(themes=summaries)

    def get_theme(self, theme_id: str) -> ThemeRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, theme_id)
                published = self._load_published_bundles(conn, theme_id)
                return ThemeRecordV1(
                    themeId=theme_id,
                    draft=draft,
                    publishedVersions=published,
                )

    def create_theme(self, request: ThemeCreateRequestV1) -> ThemeRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                exists = conn.execute(
                    "SELECT 1 FROM themes WHERE theme_id = ?",
                    (request.themeId,),
                ).fetchone()
                if exists is not None:
                    raise ThemeStoreError(
                        status_code=409,
                        code="THEME_ALREADY_EXISTS",
                        message=f"Theme '{request.themeId}' already exists.",
                    )

                bundle = self._build_bundle(
                    theme_id=request.themeId,
                    theme_version=1,
                    editable=request,
                )
                self._insert_theme(conn, bundle, publish=False)

        return self.get_theme(request.themeId)

    def update_theme(
        self,
        theme_id: str,
        request: ThemeUpdateRequestV1,
    ) -> ThemeRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT draft_version FROM themes WHERE theme_id = ?",
                    (theme_id,),
                ).fetchone()
                if row is None:
                    raise ThemeStoreError(
                        status_code=404,
                        code="THEME_NOT_FOUND",
                        message=f"Theme '{theme_id}' was not found.",
                    )

                bundle = self._build_bundle(
                    theme_id=theme_id,
                    theme_version=int(row["draft_version"]) + 1,
                    editable=request,
                )
                self._replace_draft(conn, bundle)

        return self.get_theme(theme_id)

    def upsert_theme_variable(
        self,
        theme_id: str,
        key: str,
        request: ThemeVariableUpsertRequestV1,
    ) -> ThemeRecordV1:
        normalized_key = normalize_theme_variable_key(key)
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    """
                    SELECT name, draft_version, draft_css_body
                    FROM themes
                    WHERE theme_id = ?
                    """,
                    (theme_id,),
                ).fetchone()
                if row is None:
                    raise ThemeStoreError(
                        status_code=404,
                        code="THEME_NOT_FOUND",
                        message=f"Theme '{theme_id}' was not found.",
                    )

                variables = self._load_draft_variables(conn, theme_id)
                variables[normalized_key] = ThemeVariableV1.model_validate(request)

                editable = ThemeEditableFieldsV1.model_validate(
                    {
                        "name": str(row["name"]),
                        "cssBody": str(row["draft_css_body"]),
                        "variables": variables,
                    }
                )
                bundle = self._build_bundle(
                    theme_id=theme_id,
                    theme_version=int(row["draft_version"]) + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_theme(theme_id)

    def delete_theme_variable(
        self,
        theme_id: str,
        key: str,
    ) -> ThemeRecordV1:
        normalized_key = normalize_theme_variable_key(key)
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    """
                    SELECT name, draft_version, draft_css_body
                    FROM themes
                    WHERE theme_id = ?
                    """,
                    (theme_id,),
                ).fetchone()
                if row is None:
                    raise ThemeStoreError(
                        status_code=404,
                        code="THEME_NOT_FOUND",
                        message=f"Theme '{theme_id}' was not found.",
                    )

                variables = self._load_draft_variables(conn, theme_id)
                if normalized_key not in variables:
                    raise ThemeStoreError(
                        status_code=404,
                        code="THEME_VARIABLE_NOT_FOUND",
                        message=(
                            f"Theme variable '{normalized_key}' was not found in theme '{theme_id}'."
                        ),
                    )

                variables.pop(normalized_key, None)
                editable = ThemeEditableFieldsV1.model_validate(
                    {
                        "name": str(row["name"]),
                        "cssBody": str(row["draft_css_body"]),
                        "variables": variables,
                    }
                )
                bundle = self._build_bundle(
                    theme_id=theme_id,
                    theme_version=int(row["draft_version"]) + 1,
                    editable=editable,
                )
                self._replace_draft(conn, bundle)

        return self.get_theme(theme_id)

    def publish_theme(self, theme_id: str) -> ThemeBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, theme_id)
                published = conn.execute(
                    """
                    SELECT 1
                    FROM theme_published_versions
                    WHERE theme_id = ? AND theme_version = ?
                    """,
                    (theme_id, draft.themeVersion),
                ).fetchone()
                if published is not None:
                    raise ThemeStoreError(
                        status_code=409,
                        code="THEME_VERSION_ALREADY_PUBLISHED",
                        message=(
                            f"Theme '{theme_id}' version {draft.themeVersion} is already published."
                        ),
                    )

                self._insert_published_bundle(conn, draft)
                return draft

    def get_bundle(
        self,
        theme_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        theme_version: int | None = None,
    ) -> ThemeBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                if stage == "draft":
                    bundle = self._load_draft_bundle(conn, theme_id)
                    if theme_version is not None and bundle.themeVersion != theme_version:
                        raise ThemeStoreError(
                            status_code=404,
                            code="THEME_VERSION_NOT_FOUND",
                            message=(
                                f"Theme '{theme_id}' draft version {theme_version} was not found."
                            ),
                        )
                    return bundle

                rows = conn.execute(
                    """
                    SELECT theme_version
                    FROM theme_published_versions
                    WHERE theme_id = ?
                    ORDER BY theme_version ASC
                    """,
                    (theme_id,),
                ).fetchall()
                if not rows:
                    theme_exists = conn.execute(
                        "SELECT 1 FROM themes WHERE theme_id = ?",
                        (theme_id,),
                    ).fetchone()
                    if theme_exists is None:
                        raise ThemeStoreError(
                            status_code=404,
                            code="THEME_NOT_FOUND",
                            message=f"Theme '{theme_id}' was not found.",
                        )
                    raise ThemeStoreError(
                        status_code=404,
                        code="THEME_NOT_PUBLISHED",
                        message=f"Theme '{theme_id}' has no published version.",
                    )

                selected_version = int(rows[-1]["theme_version"])
                if theme_version is not None:
                    if not any(int(row["theme_version"]) == theme_version for row in rows):
                        raise ThemeStoreError(
                            status_code=404,
                            code="THEME_VERSION_NOT_FOUND",
                            message=(
                                f"Theme '{theme_id}' published version {theme_version} was not found."
                            ),
                        )
                    selected_version = theme_version

                return self._load_published_bundle(conn, theme_id, selected_version)

    def _build_bundle(
        self,
        *,
        theme_id: str,
        theme_version: int,
        editable: ThemeEditableFieldsV1,
        updated_at: datetime | None = None,
    ) -> ThemeBundleV1:
        timestamp = updated_at or utcnow()
        variables_payload = {
            key: value.model_dump(mode="python")
            for key, value in editable.variables.items()
        }
        render_css = compile_theme_render_css(
            editable.cssBody,
            {
                key: {
                    "lightValue": value.lightValue,
                    "darkValue": value.darkValue,
                }
                for key, value in editable.variables.items()
            },
        )
        payload: dict[str, Any] = {
            "schemaVersion": "v1",
            "themeId": theme_id,
            "themeVersion": theme_version,
            "name": editable.name,
            "cssBody": editable.cssBody,
            "variables": variables_payload,
            "renderCss": render_css,
            "updatedAt": timestamp,
        }
        payload["checksum"] = compute_theme_checksum(payload)
        return ThemeBundleV1.model_validate(payload)

    def _connect(self) -> sqlite3.Connection:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._storage_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        self._create_schema(conn)
        self._assert_schema_compatible(conn)
        self._maybe_import_legacy_json(conn)
        self._schema_ready = True

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS themes (
                theme_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL,
                draft_css_body TEXT NOT NULL,
                draft_render_css TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS theme_draft_variables (
                theme_id TEXT NOT NULL,
                variable_key TEXT NOT NULL,
                value_type TEXT NOT NULL,
                light_value TEXT NOT NULL,
                dark_value TEXT NOT NULL,
                PRIMARY KEY (theme_id, variable_key),
                FOREIGN KEY (theme_id) REFERENCES themes(theme_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS theme_published_versions (
                theme_id TEXT NOT NULL,
                theme_version INTEGER NOT NULL,
                name TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                css_body TEXT NOT NULL,
                render_css TEXT NOT NULL,
                PRIMARY KEY (theme_id, theme_version),
                FOREIGN KEY (theme_id) REFERENCES themes(theme_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS theme_published_variables (
                theme_id TEXT NOT NULL,
                theme_version INTEGER NOT NULL,
                variable_key TEXT NOT NULL,
                value_type TEXT NOT NULL,
                light_value TEXT NOT NULL,
                dark_value TEXT NOT NULL,
                PRIMARY KEY (theme_id, theme_version, variable_key),
                FOREIGN KEY (theme_id, theme_version)
                    REFERENCES theme_published_versions(theme_id, theme_version)
                    ON DELETE CASCADE
            );
            """
        )

    def _assert_schema_compatible(self, conn: sqlite3.Connection) -> None:
        required = {
            "themes": {
                "theme_id",
                "name",
                "draft_version",
                "draft_updated_at",
                "draft_checksum",
                "draft_css_body",
                "draft_render_css",
            },
            "theme_draft_variables": {
                "theme_id",
                "variable_key",
                "value_type",
                "light_value",
                "dark_value",
            },
            "theme_published_versions": {
                "theme_id",
                "theme_version",
                "name",
                "updated_at",
                "checksum",
                "css_body",
                "render_css",
            },
            "theme_published_variables": {
                "theme_id",
                "theme_version",
                "variable_key",
                "value_type",
                "light_value",
                "dark_value",
            },
        }
        for table_name, required_columns in required.items():
            existing_columns = self._table_columns(conn, table_name)
            if not required_columns.issubset(existing_columns):
                raise ThemeStoreError(
                    status_code=500,
                    code="THEME_STORAGE_CORRUPTED",
                    message=(
                        f"Theme storage schema for table '{table_name}' is incompatible. "
                        f"Missing columns: {sorted(required_columns - existing_columns)}"
                    ),
                )

    def _maybe_import_legacy_json(self, conn: sqlite3.Connection) -> None:
        existing = conn.execute("SELECT COUNT(*) AS c FROM themes").fetchone()
        if existing is not None and int(existing["c"]) > 0:
            return

        for candidate in self._legacy_json_paths:
            if not candidate.exists() or self._is_sqlite_file(candidate):
                continue
            try:
                raw = candidate.read_text(encoding="utf-8")
                if not raw.strip():
                    continue
                data = json.loads(raw)
            except (OSError, json.JSONDecodeError):
                continue

            imported = self._import_legacy_document(conn, data)
            if imported:
                return

    def _import_legacy_document(self, conn: sqlite3.Connection, data: dict[str, Any]) -> bool:
        themes = data.get("themes")
        if not isinstance(themes, dict):
            return False

        imported_any = False
        for key, stored in themes.items():
            if not isinstance(stored, dict):
                continue
            try:
                theme_id = normalize_theme_id(stored.get("themeId", key))
            except ValueError:
                continue
            draft_data = stored.get("draft")
            if not isinstance(draft_data, dict):
                continue

            draft_bundle = self._legacy_bundle(theme_id, draft_data)
            if draft_bundle is None:
                continue
            self._insert_theme(conn, draft_bundle, publish=False)

            published_versions = stored.get("publishedVersions")
            if isinstance(published_versions, list):
                for published_data in published_versions:
                    if not isinstance(published_data, dict):
                        continue
                    bundle = self._legacy_bundle(theme_id, published_data)
                    if bundle is None:
                        continue
                    exists = conn.execute(
                        """
                        SELECT 1
                        FROM theme_published_versions
                        WHERE theme_id = ? AND theme_version = ?
                        """,
                        (bundle.themeId, bundle.themeVersion),
                    ).fetchone()
                    if exists is not None:
                        continue
                    self._insert_published_bundle(conn, bundle)

            imported_any = True

        return imported_any

    def _legacy_bundle(self, theme_id: str, bundle_data: dict[str, Any]) -> ThemeBundleV1 | None:
        try:
            theme_version = int(bundle_data["themeVersion"])
            name = str(bundle_data["name"]).strip()
            render_css = str(bundle_data["renderCss"])
            updated_raw = bundle_data.get("updatedAt")
            updated_at = self._parse_dt(str(updated_raw)) if updated_raw else utcnow()
        except (KeyError, TypeError, ValueError):
            return None

        if theme_version < 1 or not name or not render_css.strip():
            return None

        editable = ThemeEditableFieldsV1.model_validate(
            {
                "name": name,
                "cssBody": render_css,
                "variables": {},
            }
        )
        return self._build_bundle(
            theme_id=theme_id,
            theme_version=theme_version,
            editable=editable,
            updated_at=updated_at,
        )

    def _insert_theme(self, conn: sqlite3.Connection, bundle: ThemeBundleV1, *, publish: bool) -> None:
        conn.execute(
            """
            INSERT INTO themes (
                theme_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_css_body,
                draft_render_css
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.themeId,
                bundle.name,
                bundle.themeVersion,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
                bundle.cssBody,
                bundle.renderCss,
            ),
        )
        self._replace_draft_variables(conn, bundle)
        if publish:
            self._insert_published_bundle(conn, bundle)

    def _replace_draft(self, conn: sqlite3.Connection, bundle: ThemeBundleV1) -> None:
        conn.execute(
            """
            UPDATE themes
            SET
                name = ?,
                draft_version = ?,
                draft_updated_at = ?,
                draft_checksum = ?,
                draft_css_body = ?,
                draft_render_css = ?
            WHERE theme_id = ?
            """,
            (
                bundle.name,
                bundle.themeVersion,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
                bundle.cssBody,
                bundle.renderCss,
                bundle.themeId,
            ),
        )
        self._replace_draft_variables(conn, bundle)

    def _replace_draft_variables(self, conn: sqlite3.Connection, bundle: ThemeBundleV1) -> None:
        conn.execute(
            "DELETE FROM theme_draft_variables WHERE theme_id = ?",
            (bundle.themeId,),
        )
        if not bundle.variables:
            return
        conn.executemany(
            """
            INSERT INTO theme_draft_variables (
                theme_id,
                variable_key,
                value_type,
                light_value,
                dark_value
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    bundle.themeId,
                    key,
                    value.valueType,
                    value.lightValue,
                    value.darkValue,
                )
                for key, value in bundle.variables.items()
            ],
        )

    def _insert_published_bundle(self, conn: sqlite3.Connection, bundle: ThemeBundleV1) -> None:
        conn.execute(
            """
            INSERT INTO theme_published_versions (
                theme_id,
                theme_version,
                name,
                updated_at,
                checksum,
                css_body,
                render_css
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.themeId,
                bundle.themeVersion,
                bundle.name,
                self._serialize_dt(bundle.updatedAt),
                bundle.checksum,
                bundle.cssBody,
                bundle.renderCss,
            ),
        )
        if not bundle.variables:
            return
        conn.executemany(
            """
            INSERT INTO theme_published_variables (
                theme_id,
                theme_version,
                variable_key,
                value_type,
                light_value,
                dark_value
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    bundle.themeId,
                    bundle.themeVersion,
                    key,
                    value.valueType,
                    value.lightValue,
                    value.darkValue,
                )
                for key, value in bundle.variables.items()
            ],
        )

    def _load_draft_bundle(self, conn: sqlite3.Connection, theme_id: str) -> ThemeBundleV1:
        row = conn.execute(
            """
            SELECT
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_css_body,
                draft_render_css
            FROM themes
            WHERE theme_id = ?
            """,
            (theme_id,),
        ).fetchone()
        if row is None:
            raise ThemeStoreError(
                status_code=404,
                code="THEME_NOT_FOUND",
                message=f"Theme '{theme_id}' was not found.",
            )

        payload = {
            "schemaVersion": "v1",
            "themeId": theme_id,
            "themeVersion": int(row["draft_version"]),
            "name": str(row["name"]),
            "cssBody": str(row["draft_css_body"]),
            "variables": {
                key: value.model_dump(mode="python")
                for key, value in self._load_draft_variables(conn, theme_id).items()
            },
            "renderCss": str(row["draft_render_css"]),
            "updatedAt": self._parse_dt(str(row["draft_updated_at"])),
            "checksum": str(row["draft_checksum"]),
        }
        return ThemeBundleV1.model_validate(payload)

    def _load_draft_variables(self, conn: sqlite3.Connection, theme_id: str) -> dict[str, ThemeVariableV1]:
        rows = conn.execute(
            """
            SELECT variable_key, value_type, light_value, dark_value
            FROM theme_draft_variables
            WHERE theme_id = ?
            ORDER BY variable_key ASC
            """,
            (theme_id,),
        ).fetchall()
        return {
            str(row["variable_key"]): ThemeVariableV1.model_validate(
                {
                    "valueType": str(row["value_type"]),
                    "lightValue": str(row["light_value"]),
                    "darkValue": str(row["dark_value"]),
                }
            )
            for row in rows
        }

    def _load_published_bundle(
        self,
        conn: sqlite3.Connection,
        theme_id: str,
        theme_version: int,
    ) -> ThemeBundleV1:
        row = conn.execute(
            """
            SELECT name, updated_at, checksum, css_body, render_css
            FROM theme_published_versions
            WHERE theme_id = ? AND theme_version = ?
            """,
            (theme_id, theme_version),
        ).fetchone()
        if row is None:
            raise ThemeStoreError(
                status_code=404,
                code="THEME_VERSION_NOT_FOUND",
                message=f"Theme '{theme_id}' published version {theme_version} was not found.",
            )

        payload = {
            "schemaVersion": "v1",
            "themeId": theme_id,
            "themeVersion": theme_version,
            "name": str(row["name"]),
            "cssBody": str(row["css_body"]),
            "variables": {
                key: value.model_dump(mode="python")
                for key, value in self._load_published_variables(conn, theme_id, theme_version).items()
            },
            "renderCss": str(row["render_css"]),
            "updatedAt": self._parse_dt(str(row["updated_at"])),
            "checksum": str(row["checksum"]),
        }
        return ThemeBundleV1.model_validate(payload)

    def _load_published_variables(
        self,
        conn: sqlite3.Connection,
        theme_id: str,
        theme_version: int,
    ) -> dict[str, ThemeVariableV1]:
        rows = conn.execute(
            """
            SELECT variable_key, value_type, light_value, dark_value
            FROM theme_published_variables
            WHERE theme_id = ? AND theme_version = ?
            ORDER BY variable_key ASC
            """,
            (theme_id, theme_version),
        ).fetchall()
        return {
            str(row["variable_key"]): ThemeVariableV1.model_validate(
                {
                    "valueType": str(row["value_type"]),
                    "lightValue": str(row["light_value"]),
                    "darkValue": str(row["dark_value"]),
                }
            )
            for row in rows
        }

    def _load_published_bundles(self, conn: sqlite3.Connection, theme_id: str) -> list[ThemeBundleV1]:
        rows = conn.execute(
            """
            SELECT theme_version
            FROM theme_published_versions
            WHERE theme_id = ?
            ORDER BY theme_version ASC
            """,
            (theme_id,),
        ).fetchall()
        return [self._load_published_bundle(conn, theme_id, int(row["theme_version"])) for row in rows]

    @staticmethod
    def _is_sqlite_file(path: Path) -> bool:
        try:
            with path.open("rb") as fh:
                return fh.read(16) == b"SQLite format 3\x00"
        except OSError:
            return False

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    @staticmethod
    def _serialize_dt(value: datetime) -> str:
        return value.isoformat()

    @staticmethod
    def _parse_dt(value: str) -> datetime:
        return datetime.fromisoformat(value)
