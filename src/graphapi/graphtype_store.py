from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Literal

from graphloom import ElkSettings
from pydantic import ValidationError

from .graph_type_contract import (
    AutocompleteCatalogResponseV1,
    GraphTypeBundleV1,
    GraphTypeCreateRequestV1,
    GraphTypeEditableFieldsV1,
    GraphTypeListResponseV1,
    GraphTypeRecordV1,
    GraphTypeRuntimeResponseV1,
    GraphTypeSummaryV1,
    GraphTypeUpdateRequestV1,
    IconsetSourceRefV1,
    NodeTypeSourceV1,
    build_edge_type_overrides,
    compute_autocomplete_checksum,
    compute_graph_type_checksum,
    compute_graph_type_runtime_checksum,
    compute_icon_set_resolution_checksum,
    normalize_type_key,
)
from .iconset_store import IconsetStore, IconsetStoreError
from .layoutset_store import LayoutSetStore, LayoutSetStoreError
from .linkset_store import LinkSetStore, LinkSetStoreError


class GraphTypeStoreError(Exception):
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


class GraphTypeStore:
    def __init__(
        self,
        storage_path: Path,
        iconset_store: IconsetStore,
        layout_set_store: LayoutSetStore,
        link_set_store: LinkSetStore,
    ) -> None:
        self._storage_path = storage_path
        self._iconset_store = iconset_store
        self._layout_set_store = layout_set_store
        self._link_set_store = link_set_store
        self._lock = RLock()
        self._schema_ready = False

    @classmethod
    def from_env(
        cls,
        iconset_store: IconsetStore,
        layout_set_store: LayoutSetStore,
        link_set_store: LinkSetStore,
    ) -> "GraphTypeStore":
        raw = os.getenv("GRAPHAPI_RUNTIME_DB_PATH", "").strip()
        if not raw:
            raw = os.getenv("GRAPHAPI_GRAPH_TYPE_STORE_PATH", "").strip()
        if raw:
            return cls(Path(raw).expanduser(), iconset_store, layout_set_store, link_set_store)
        return cls(
            Path.home() / ".cache" / "graphapi" / "runtime.v1.sqlite3",
            iconset_store,
            layout_set_store,
            link_set_store,
        )

    def ensure_default_graph_type(self, request: GraphTypeCreateRequestV1) -> None:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT 1 FROM graph_types WHERE graph_type_id = ?",
                    (request.graphTypeId,),
                ).fetchone()
                if row is not None:
                    return

                bundle = self._build_bundle(
                    graph_type_id=request.graphTypeId,
                    graph_type_version=1,
                    editable=request,
                )
                self._insert_graph_type(conn, bundle, publish=True)

    def list_graph_types(self) -> GraphTypeListResponseV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    """
                    SELECT
                        g.graph_type_id,
                        g.name,
                        g.draft_version,
                        g.draft_updated_at,
                        g.draft_checksum,
                        g.draft_runtime_checksum,
                        g.draft_icon_set_resolution_checksum,
                        (
                            SELECT MAX(v.graph_type_version)
                            FROM graph_type_published_versions v
                            WHERE v.graph_type_id = g.graph_type_id
                        ) AS published_version
                    FROM graph_types g
                    ORDER BY g.graph_type_id ASC
                    """
                ).fetchall()

                return GraphTypeListResponseV1(
                    graphTypes=[
                        GraphTypeSummaryV1(
                            graphTypeId=str(row["graph_type_id"]),
                            name=str(row["name"]),
                            draftVersion=int(row["draft_version"]),
                            publishedVersion=(
                                int(row["published_version"]) if row["published_version"] is not None else None
                            ),
                            updatedAt=datetime.fromisoformat(str(row["draft_updated_at"])),
                            checksum=str(row["draft_checksum"]),
                            runtimeChecksum=str(row["draft_runtime_checksum"]),
                            iconSetResolutionChecksum=str(row["draft_icon_set_resolution_checksum"]),
                        )
                        for row in rows
                    ]
                )

    def get_graph_type(self, graph_type_id: str) -> GraphTypeRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, graph_type_id)
                published_rows = conn.execute(
                    """
                    SELECT payload
                    FROM graph_type_published_versions
                    WHERE graph_type_id = ?
                    ORDER BY graph_type_version ASC
                    """,
                    (graph_type_id,),
                ).fetchall()
                published = [self._bundle_from_json(str(row["payload"])) for row in published_rows]
                return GraphTypeRecordV1(
                    graphTypeId=graph_type_id,
                    draft=draft,
                    publishedVersions=published,
                )

    def create_graph_type(self, request: GraphTypeCreateRequestV1) -> GraphTypeRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                exists = conn.execute(
                    "SELECT 1 FROM graph_types WHERE graph_type_id = ?",
                    (request.graphTypeId,),
                ).fetchone()
                if exists is not None:
                    raise GraphTypeStoreError(
                        status_code=409,
                        code="GRAPH_TYPE_ALREADY_EXISTS",
                        message=f"Graph type '{request.graphTypeId}' already exists.",
                    )

                bundle = self._build_bundle(
                    graph_type_id=request.graphTypeId,
                    graph_type_version=1,
                    editable=request,
                )
                self._insert_graph_type(conn, bundle, publish=False)

        return self.get_graph_type(request.graphTypeId)

    def update_graph_type(self, graph_type_id: str, request: GraphTypeUpdateRequestV1) -> GraphTypeRecordV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT draft_version FROM graph_types WHERE graph_type_id = ?",
                    (graph_type_id,),
                ).fetchone()
                if row is None:
                    raise GraphTypeStoreError(
                        status_code=404,
                        code="GRAPH_TYPE_NOT_FOUND",
                        message=f"Graph type '{graph_type_id}' was not found.",
                    )

                next_version = int(row["draft_version"]) + 1
                bundle = self._build_bundle(
                    graph_type_id=graph_type_id,
                    graph_type_version=next_version,
                    editable=request,
                )
                self._replace_draft(conn, bundle)

        return self.get_graph_type(graph_type_id)

    def publish_graph_type(self, graph_type_id: str) -> GraphTypeBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)
                draft = self._load_draft_bundle(conn, graph_type_id)
                exists = conn.execute(
                    """
                    SELECT 1
                    FROM graph_type_published_versions
                    WHERE graph_type_id = ?
                      AND graph_type_version = ?
                    """,
                    (graph_type_id, draft.graphTypeVersion),
                ).fetchone()
                if exists is not None:
                    raise GraphTypeStoreError(
                        status_code=409,
                        code="GRAPH_TYPE_VERSION_ALREADY_PUBLISHED",
                        message=(
                            f"Graph type '{graph_type_id}' version {draft.graphTypeVersion} is already published."
                        ),
                    )

                conn.execute(
                    """
                    INSERT INTO graph_type_published_versions (
                        graph_type_id,
                        graph_type_version,
                        updated_at,
                        checksum,
                        runtime_checksum,
                        icon_set_resolution_checksum,
                        payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft.graphTypeId,
                        draft.graphTypeVersion,
                        draft.updatedAt.isoformat(),
                        draft.checksum,
                        draft.runtimeChecksum,
                        draft.iconSetResolutionChecksum,
                        self._bundle_to_json(draft),
                    ),
                )
                return draft

    def get_bundle(
        self,
        graph_type_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        graph_type_version: int | None = None,
    ) -> GraphTypeBundleV1:
        with self._lock:
            with self._connect() as conn:
                self._ensure_schema(conn)

                if stage == "draft":
                    draft = self._load_draft_bundle(conn, graph_type_id)
                    if graph_type_version is not None and draft.graphTypeVersion != graph_type_version:
                        raise GraphTypeStoreError(
                            status_code=404,
                            code="GRAPH_TYPE_VERSION_NOT_FOUND",
                            message=(
                                f"Graph type '{graph_type_id}' draft version {graph_type_version} was not found."
                            ),
                        )
                    return draft

                rows = conn.execute(
                    """
                    SELECT graph_type_version, payload
                    FROM graph_type_published_versions
                    WHERE graph_type_id = ?
                    ORDER BY graph_type_version ASC
                    """,
                    (graph_type_id,),
                ).fetchall()

                if not rows:
                    self._assert_graph_type_exists(conn, graph_type_id)
                    raise GraphTypeStoreError(
                        status_code=404,
                        code="GRAPH_TYPE_NOT_PUBLISHED",
                        message=f"Graph type '{graph_type_id}' has no published version.",
                    )

                selected = None
                if graph_type_version is None:
                    selected = rows[-1]
                else:
                    for row in rows:
                        if int(row["graph_type_version"]) == graph_type_version:
                            selected = row
                            break
                    if selected is None:
                        raise GraphTypeStoreError(
                            status_code=404,
                            code="GRAPH_TYPE_VERSION_NOT_FOUND",
                            message=(
                                f"Graph type '{graph_type_id}' published version {graph_type_version} was not found."
                            ),
                        )

                return self._bundle_from_json(str(selected["payload"]))

    def get_runtime(
        self,
        graph_type_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        graph_type_version: int | None = None,
    ) -> GraphTypeRuntimeResponseV1:
        bundle = self.get_bundle(
            graph_type_id,
            stage=stage,
            graph_type_version=graph_type_version,
        )
        resolved_entries, sources, key_sources, _ = self._resolve_icon_sets(
            bundle.iconSetRefs,
            bundle.iconConflictPolicy,
        )

        return GraphTypeRuntimeResponseV1(
            graphTypeId=bundle.graphTypeId,
            graphTypeVersion=bundle.graphTypeVersion,
            graphTypeChecksum=bundle.checksum,
            runtimeChecksum=bundle.runtimeChecksum,
            conflictPolicy=bundle.iconConflictPolicy,
            resolvedEntries=resolved_entries,
            sources=sources,
            keySources=key_sources,
            linkTypes=bundle.linkTypes,
            edgeTypeOverrides=bundle.edgeTypeOverrides,
            checksum=bundle.runtimeChecksum,
        )

    def get_autocomplete_catalog(
        self,
        graph_type_id: str,
        *,
        stage: Literal["draft", "published"] = "published",
        graph_type_version: int | None = None,
    ) -> AutocompleteCatalogResponseV1:
        bundle = self.get_bundle(
            graph_type_id,
            stage=stage,
            graph_type_version=graph_type_version,
        )

        return AutocompleteCatalogResponseV1(
            graphTypeId=bundle.graphTypeId,
            graphTypeVersion=bundle.graphTypeVersion,
            graphTypeChecksum=bundle.checksum,
            runtimeChecksum=bundle.runtimeChecksum,
            iconSetResolutionChecksum=bundle.iconSetResolutionChecksum,
            checksum=compute_autocomplete_checksum(bundle),
            nodeTypes=bundle.nodeTypes,
            linkTypes=bundle.linkTypes,
        )

    def _resolve_icon_sets(
        self,
        refs,
        conflict_policy,
    ) -> tuple[dict[str, str], list[IconsetSourceRefV1], dict[str, NodeTypeSourceV1], str]:
        resolved_entries: dict[str, str] = {}
        source_refs: list[IconsetSourceRefV1] = []
        key_sources_payload: dict[str, dict] = {}

        for ref in refs:
            try:
                bundle = self._iconset_store.get_bundle(
                    ref.iconSetId,
                    stage="published",
                    icon_set_version=ref.iconSetVersion,
                )
            except IconsetStoreError as exc:
                if exc.code in {
                    "ICONSET_NOT_FOUND",
                    "ICONSET_NOT_PUBLISHED",
                    "ICONSET_VERSION_NOT_FOUND",
                }:
                    raise GraphTypeStoreError(
                        status_code=404,
                        code="GRAPH_TYPE_ICONSET_REF_INVALID",
                        message=(
                            f"Graph type iconset reference '{ref.iconSetId}@{ref.iconSetVersion}' could not be resolved."
                        ),
                        details={
                            "iconSetId": ref.iconSetId,
                            "iconSetVersion": ref.iconSetVersion,
                            "cause": exc.code,
                        },
                    ) from exc
                raise GraphTypeStoreError(
                    status_code=500,
                    code="ICONSET_RESOLUTION_FAILED",
                    message="Failed to resolve iconset references.",
                ) from exc

            if ref.checksum and ref.checksum != bundle.checksum:
                raise GraphTypeStoreError(
                    status_code=409,
                    code="GRAPH_TYPE_ICONSET_REF_INVALID",
                    message=(
                        f"Graph type iconset reference '{ref.iconSetId}@{ref.iconSetVersion}' checksum mismatch."
                    ),
                    details={
                        "iconSetId": ref.iconSetId,
                        "iconSetVersion": ref.iconSetVersion,
                        "expectedChecksum": ref.checksum,
                        "actualChecksum": bundle.checksum,
                    },
                )

            source = IconsetSourceRefV1(
                iconSetId=bundle.iconSetId,
                iconSetVersion=bundle.iconSetVersion,
                checksum=bundle.checksum,
            )
            source_refs.append(source)

            for key, icon in bundle.entries.items():
                normalized_key = normalize_type_key(key)
                existing_icon = resolved_entries.get(normalized_key)
                source_payload = source.model_dump()

                if normalized_key not in key_sources_payload:
                    key_sources_payload[normalized_key] = {
                        "key": normalized_key,
                        "icon": icon,
                        "selectedFrom": source_payload,
                        "candidates": [source_payload],
                    }
                else:
                    key_sources_payload[normalized_key]["candidates"].append(source_payload)

                if existing_icon is None:
                    resolved_entries[normalized_key] = icon
                    continue

                if existing_icon == icon:
                    continue

                if conflict_policy == "reject":
                    raise GraphTypeStoreError(
                        status_code=409,
                        code="ICONSET_KEY_CONFLICT",
                        message=(
                            f"Node type key '{normalized_key}' maps to multiple icons under reject policy."
                        ),
                        details={
                            "key": normalized_key,
                            "existingIcon": existing_icon,
                            "incomingIcon": icon,
                            "conflictPolicy": conflict_policy,
                        },
                    )

                if conflict_policy == "last-wins":
                    resolved_entries[normalized_key] = icon
                    key_sources_payload[normalized_key]["icon"] = icon
                    key_sources_payload[normalized_key]["selectedFrom"] = source_payload

        resolved_entries = dict(sorted(resolved_entries.items(), key=lambda item: item[0]))
        if not resolved_entries:
            raise GraphTypeStoreError(
                status_code=400,
                code="GRAPH_TYPE_ICONSET_REF_INVALID",
                message="Resolved iconset map is empty.",
            )

        key_sources = {
            key: NodeTypeSourceV1.model_validate(payload)
            for key, payload in sorted(key_sources_payload.items(), key=lambda item: item[0])
        }

        checksum = compute_icon_set_resolution_checksum(
            conflict_policy=conflict_policy,
            sources=[item.model_dump() for item in source_refs],
            resolved_entries=resolved_entries,
        )

        return resolved_entries, source_refs, key_sources, checksum

    def _build_bundle(
        self,
        *,
        graph_type_id: str,
        graph_type_version: int,
        editable: GraphTypeEditableFieldsV1,
    ) -> GraphTypeBundleV1:
        try:
            layout_bundle = self._layout_set_store.get_bundle(
                editable.layoutSetRef.layoutSetId,
                stage="published",
                layout_set_version=editable.layoutSetRef.layoutSetVersion,
            )
        except LayoutSetStoreError as exc:
            code = exc.code
            if code in {"LAYOUT_SET_NOT_FOUND", "LAYOUT_SET_NOT_PUBLISHED", "LAYOUT_SET_VERSION_NOT_FOUND"}:
                raise GraphTypeStoreError(
                    status_code=404,
                    code="GRAPH_TYPE_LAYOUT_SET_REF_INVALID",
                    message=(
                        f"Graph type layout set reference '{editable.layoutSetRef.layoutSetId}@{editable.layoutSetRef.layoutSetVersion}' could not be resolved."
                    ),
                    details={
                        "layoutSetId": editable.layoutSetRef.layoutSetId,
                        "layoutSetVersion": editable.layoutSetRef.layoutSetVersion,
                        "cause": exc.code,
                    },
                ) from exc
            raise GraphTypeStoreError(
                status_code=500,
                code="GRAPH_TYPE_LAYOUT_SET_RESOLUTION_FAILED",
                message="Failed to resolve layout set reference.",
            ) from exc

        if editable.layoutSetRef.checksum and editable.layoutSetRef.checksum != layout_bundle.checksum:
            raise GraphTypeStoreError(
                status_code=409,
                code="GRAPH_TYPE_LAYOUT_SET_REF_INVALID",
                message=(
                    f"Graph type layout set reference '{editable.layoutSetRef.layoutSetId}@{editable.layoutSetRef.layoutSetVersion}' checksum mismatch."
                ),
                details={
                    "layoutSetId": editable.layoutSetRef.layoutSetId,
                    "layoutSetVersion": editable.layoutSetRef.layoutSetVersion,
                    "expectedChecksum": editable.layoutSetRef.checksum,
                    "actualChecksum": layout_bundle.checksum,
                },
            )

        try:
            link_bundle = self._link_set_store.get_bundle(
                editable.linkSetRef.linkSetId,
                stage="published",
                link_set_version=editable.linkSetRef.linkSetVersion,
            )
        except LinkSetStoreError as exc:
            code = exc.code
            if code in {"LINK_SET_NOT_FOUND", "LINK_SET_NOT_PUBLISHED", "LINK_SET_VERSION_NOT_FOUND"}:
                raise GraphTypeStoreError(
                    status_code=404,
                    code="GRAPH_TYPE_LINK_SET_REF_INVALID",
                    message=(
                        f"Graph type link set reference '{editable.linkSetRef.linkSetId}@{editable.linkSetRef.linkSetVersion}' could not be resolved."
                    ),
                    details={
                        "linkSetId": editable.linkSetRef.linkSetId,
                        "linkSetVersion": editable.linkSetRef.linkSetVersion,
                        "cause": exc.code,
                    },
                ) from exc
            raise GraphTypeStoreError(
                status_code=500,
                code="GRAPH_TYPE_LINK_SET_RESOLUTION_FAILED",
                message="Failed to resolve link set reference.",
            ) from exc

        if editable.linkSetRef.checksum and editable.linkSetRef.checksum != link_bundle.checksum:
            raise GraphTypeStoreError(
                status_code=409,
                code="GRAPH_TYPE_LINK_SET_REF_INVALID",
                message=(
                    f"Graph type link set reference '{editable.linkSetRef.linkSetId}@{editable.linkSetRef.linkSetVersion}' checksum mismatch."
                ),
                details={
                    "linkSetId": editable.linkSetRef.linkSetId,
                    "linkSetVersion": editable.linkSetRef.linkSetVersion,
                    "expectedChecksum": editable.linkSetRef.checksum,
                    "actualChecksum": link_bundle.checksum,
                },
            )

        resolved_entries, source_refs, _key_sources, resolution_checksum = self._resolve_icon_sets(
            editable.iconSetRefs,
            editable.iconConflictPolicy,
        )

        edge_defaults = dict(layout_bundle.elkSettings.get("edge_defaults", {}))
        edge_type_overrides = build_edge_type_overrides(
            base_edge_defaults=edge_defaults,
            link_entries=link_bundle.entries,
        )

        resolved_elk_settings = dict(layout_bundle.elkSettings)
        resolved_elk_settings["type_icon_map"] = dict(sorted(resolved_entries.items(), key=lambda item: item[0]))
        resolved_elk_settings["edge_type_overrides"] = edge_type_overrides

        try:
            validated_elk = ElkSettings.model_validate(resolved_elk_settings)
        except ValidationError as exc:
            raise GraphTypeStoreError(
                status_code=500,
                code="GRAPH_TYPE_RUNTIME_INVALID",
                message="Resolved graph type runtime settings failed GraphLoom validation.",
                details={"errors": exc.errors()},
            ) from exc

        link_types = sorted(link_bundle.entries.keys())
        payload = {
            "schemaVersion": "v1",
            "graphTypeId": graph_type_id,
            "graphTypeVersion": graph_type_version,
            "name": editable.name,
            "layoutSetRef": {
                "layoutSetId": layout_bundle.layoutSetId,
                "layoutSetVersion": layout_bundle.layoutSetVersion,
                "checksum": layout_bundle.checksum,
            },
            "iconSetRefs": [
                {
                    "iconSetId": source.iconSetId,
                    "iconSetVersion": source.iconSetVersion,
                    "checksum": source.checksum,
                }
                for source in source_refs
            ],
            "linkSetRef": {
                "linkSetId": link_bundle.linkSetId,
                "linkSetVersion": link_bundle.linkSetVersion,
                "checksum": link_bundle.checksum,
            },
            "iconConflictPolicy": editable.iconConflictPolicy,
            "nodeTypes": sorted(resolved_entries.keys()),
            "linkTypes": link_types,
            "typeIconMap": resolved_entries,
            "edgeTypeOverrides": edge_type_overrides,
            "iconSetResolutionChecksum": resolution_checksum,
            "elkSettings": validated_elk.model_dump(by_alias=True, exclude_none=True, mode="json"),
            "updatedAt": datetime.now().astimezone(),
        }

        payload["runtimeChecksum"] = compute_graph_type_runtime_checksum(payload)
        payload["checksum"] = compute_graph_type_checksum(payload)
        return GraphTypeBundleV1.model_validate(payload)

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
        self._migrate_legacy_schema(conn)
        self._assert_schema_compatible(conn)
        if self._has_invalid_bundle_payload(conn):
            raise GraphTypeStoreError(
                status_code=500,
                code="GRAPH_TYPE_STORAGE_CORRUPTED",
                message="Graph type storage payload is unreadable or invalid. Manual migration required.",
            )
        self._schema_ready = True

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS graph_types (
                graph_type_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                draft_version INTEGER NOT NULL,
                draft_updated_at TEXT NOT NULL,
                draft_checksum TEXT NOT NULL,
                draft_runtime_checksum TEXT NOT NULL,
                draft_icon_set_resolution_checksum TEXT NOT NULL,
                draft_payload TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS graph_type_published_versions (
                graph_type_id TEXT NOT NULL,
                graph_type_version INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                runtime_checksum TEXT NOT NULL,
                icon_set_resolution_checksum TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (graph_type_id, graph_type_version),
                FOREIGN KEY (graph_type_id) REFERENCES graph_types(graph_type_id) ON DELETE CASCADE
            );
            """
        )

    def _migrate_legacy_schema(self, conn: sqlite3.Connection) -> None:
        graph_type_columns = self._table_columns(conn, "graph_types")
        published_columns = self._table_columns(conn, "graph_type_published_versions")

        if graph_type_columns and "draft_runtime_checksum" not in graph_type_columns:
            conn.execute(
                """
                ALTER TABLE graph_types
                ADD COLUMN draft_runtime_checksum TEXT NOT NULL DEFAULT ''
                """
            )

        if graph_type_columns and "draft_icon_set_resolution_checksum" not in graph_type_columns:
            conn.execute(
                """
                ALTER TABLE graph_types
                ADD COLUMN draft_icon_set_resolution_checksum TEXT NOT NULL DEFAULT ''
                """
            )

        if published_columns and "runtime_checksum" not in published_columns:
            conn.execute(
                """
                ALTER TABLE graph_type_published_versions
                ADD COLUMN runtime_checksum TEXT NOT NULL DEFAULT ''
                """
            )

        if published_columns and "icon_set_resolution_checksum" not in published_columns:
            conn.execute(
                """
                ALTER TABLE graph_type_published_versions
                ADD COLUMN icon_set_resolution_checksum TEXT NOT NULL DEFAULT ''
                """
            )

        self._backfill_checksum_columns(conn)

    def _backfill_checksum_columns(self, conn: sqlite3.Connection) -> None:
        graph_type_rows = conn.execute(
            """
            SELECT graph_type_id, draft_payload, draft_runtime_checksum, draft_icon_set_resolution_checksum
            FROM graph_types
            """
        ).fetchall()
        for row in graph_type_rows:
            payload = self._parse_payload_json(str(row["draft_payload"]))
            runtime_checksum = str(payload.get("runtimeChecksum") or row["draft_runtime_checksum"] or "").strip()
            resolution_checksum = str(
                payload.get("iconSetResolutionChecksum") or row["draft_icon_set_resolution_checksum"] or ""
            ).strip()
            conn.execute(
                """
                UPDATE graph_types
                SET draft_runtime_checksum = ?, draft_icon_set_resolution_checksum = ?
                WHERE graph_type_id = ?
                """,
                (runtime_checksum, resolution_checksum, str(row["graph_type_id"])),
            )

        published_rows = conn.execute(
            """
            SELECT graph_type_id, graph_type_version, payload, runtime_checksum, icon_set_resolution_checksum
            FROM graph_type_published_versions
            """
        ).fetchall()
        for row in published_rows:
            payload = self._parse_payload_json(str(row["payload"]))
            runtime_checksum = str(payload.get("runtimeChecksum") or row["runtime_checksum"] or "").strip()
            resolution_checksum = str(payload.get("iconSetResolutionChecksum") or row["icon_set_resolution_checksum"] or "").strip()
            conn.execute(
                """
                UPDATE graph_type_published_versions
                SET runtime_checksum = ?, icon_set_resolution_checksum = ?
                WHERE graph_type_id = ? AND graph_type_version = ?
                """,
                (
                    runtime_checksum,
                    resolution_checksum,
                    str(row["graph_type_id"]),
                    int(row["graph_type_version"]),
                ),
            )

    @staticmethod
    def _parse_payload_json(raw: str) -> dict:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GraphTypeStoreError(
                status_code=500,
                code="GRAPH_TYPE_STORAGE_CORRUPTED",
                message="Graph type storage payload is unreadable or invalid. Manual migration required.",
            ) from exc
        if not isinstance(parsed, dict):
            raise GraphTypeStoreError(
                status_code=500,
                code="GRAPH_TYPE_STORAGE_CORRUPTED",
                message="Graph type storage payload is unreadable or invalid. Manual migration required.",
            )
        return parsed

    def _assert_schema_compatible(self, conn: sqlite3.Connection) -> None:
        expected_columns = {
            "graph_types": {
                "graph_type_id",
                "name",
                "draft_version",
                "draft_updated_at",
                "draft_checksum",
                "draft_runtime_checksum",
                "draft_icon_set_resolution_checksum",
                "draft_payload",
            },
            "graph_type_published_versions": {
                "graph_type_id",
                "graph_type_version",
                "updated_at",
                "checksum",
                "runtime_checksum",
                "icon_set_resolution_checksum",
                "payload",
            },
        }

        for table_name, required_columns in expected_columns.items():
            actual_columns = self._table_columns(conn, table_name)
            missing = required_columns - actual_columns
            if missing:
                raise GraphTypeStoreError(
                    status_code=500,
                    code="GRAPH_TYPE_SCHEMA_MIGRATION_REQUIRED",
                    message=(
                        f"Graph type store schema is incompatible for table '{table_name}'. "
                        "Manual migration required."
                    ),
                    details={"missingColumns": sorted(missing)},
                )

    @staticmethod
    def _drop_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            DROP TABLE IF EXISTS graph_type_published_versions;
            DROP TABLE IF EXISTS graph_types;
            """
        )

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}

    @staticmethod
    def _has_invalid_bundle_payload(conn: sqlite3.Connection) -> bool:
        rows = conn.execute(
            """
            SELECT draft_payload AS payload FROM graph_types
            UNION ALL
            SELECT payload FROM graph_type_published_versions
            """
        ).fetchall()
        for row in rows:
            try:
                GraphTypeBundleV1.model_validate(json.loads(str(row["payload"])))
            except (json.JSONDecodeError, ValidationError, TypeError):
                return True
        return False

    def _assert_graph_type_exists(self, conn: sqlite3.Connection, graph_type_id: str) -> None:
        row = conn.execute(
            "SELECT 1 FROM graph_types WHERE graph_type_id = ?",
            (graph_type_id,),
        ).fetchone()
        if row is None:
            raise GraphTypeStoreError(
                status_code=404,
                code="GRAPH_TYPE_NOT_FOUND",
                message=f"Graph type '{graph_type_id}' was not found.",
            )

    def _insert_graph_type(self, conn: sqlite3.Connection, bundle: GraphTypeBundleV1, *, publish: bool) -> None:
        conn.execute(
            """
            INSERT INTO graph_types (
                graph_type_id,
                name,
                draft_version,
                draft_updated_at,
                draft_checksum,
                draft_runtime_checksum,
                draft_icon_set_resolution_checksum,
                draft_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.graphTypeId,
                bundle.name,
                bundle.graphTypeVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
                bundle.runtimeChecksum,
                bundle.iconSetResolutionChecksum,
                self._bundle_to_json(bundle),
            ),
        )
        if publish:
            conn.execute(
                """
                INSERT INTO graph_type_published_versions (
                    graph_type_id,
                    graph_type_version,
                    updated_at,
                    checksum,
                    runtime_checksum,
                    icon_set_resolution_checksum,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle.graphTypeId,
                    bundle.graphTypeVersion,
                    bundle.updatedAt.isoformat(),
                    bundle.checksum,
                    bundle.runtimeChecksum,
                    bundle.iconSetResolutionChecksum,
                    self._bundle_to_json(bundle),
                ),
            )

    def _replace_draft(self, conn: sqlite3.Connection, bundle: GraphTypeBundleV1) -> None:
        conn.execute(
            """
            UPDATE graph_types
            SET
                name = ?,
                draft_version = ?,
                draft_updated_at = ?,
                draft_checksum = ?,
                draft_runtime_checksum = ?,
                draft_icon_set_resolution_checksum = ?,
                draft_payload = ?
            WHERE graph_type_id = ?
            """,
            (
                bundle.name,
                bundle.graphTypeVersion,
                bundle.updatedAt.isoformat(),
                bundle.checksum,
                bundle.runtimeChecksum,
                bundle.iconSetResolutionChecksum,
                self._bundle_to_json(bundle),
                bundle.graphTypeId,
            ),
        )

    def _load_draft_bundle(self, conn: sqlite3.Connection, graph_type_id: str) -> GraphTypeBundleV1:
        row = conn.execute(
            "SELECT draft_payload FROM graph_types WHERE graph_type_id = ?",
            (graph_type_id,),
        ).fetchone()
        if row is None:
            raise GraphTypeStoreError(
                status_code=404,
                code="GRAPH_TYPE_NOT_FOUND",
                message=f"Graph type '{graph_type_id}' was not found.",
            )
        return self._bundle_from_json(str(row["draft_payload"]))

    @staticmethod
    def _bundle_to_json(bundle: GraphTypeBundleV1) -> str:
        return json.dumps(bundle.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _bundle_from_json(raw: str) -> GraphTypeBundleV1:
        try:
            return GraphTypeBundleV1.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise GraphTypeStoreError(
                status_code=500,
                code="GRAPH_TYPE_STORAGE_CORRUPTED",
                message="Graph type storage payload is unreadable or invalid.",
            ) from exc
