from __future__ import annotations

from .graph_type_contract import GraphTypeCreateRequestV1


def default_graph_type_create_request() -> GraphTypeCreateRequestV1:
    return GraphTypeCreateRequestV1.model_validate(
        {
            "graphTypeId": "default",
            "name": "Default Graph Type",
            "layoutSetRef": {
                "layoutSetId": "default",
                "layoutSetVersion": 1,
            },
            "iconSetRefs": [
                {
                    "iconSetId": "default",
                    "iconSetVersion": 1,
                }
            ],
            "linkSetRef": {
                "linkSetId": "default",
                "linkSetVersion": 1,
            },
            "iconConflictPolicy": "reject",
        }
    )
