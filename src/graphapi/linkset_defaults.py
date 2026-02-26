from __future__ import annotations

from .graph_type_contract import LinkSetCreateRequestV1


def default_link_set_create_request() -> LinkSetCreateRequestV1:
    return LinkSetCreateRequestV1.model_validate(
        {
            "linkSetId": "default",
            "name": "Default Link Set",
            "entries": {
                "directed": {
                    "label": "Directed",
                    "elkEdgeType": "DIRECTED",
                    "elkProperties": {},
                },
                "undirected": {
                    "label": "Undirected",
                    "elkEdgeType": "UNDIRECTED",
                    "elkProperties": {},
                },
                "association": {
                    "label": "Association",
                    "elkEdgeType": "UNDIRECTED",
                    "elkProperties": {
                        "org.eclipse.elk.edge.thickness": 1,
                    },
                },
                "dependency": {
                    "label": "Dependency",
                    "elkEdgeType": "DIRECTED",
                    "elkProperties": {
                        "org.eclipse.elk.edge.thickness": 1,
                    },
                },
                "generalization": {
                    "label": "Generalization",
                    "elkEdgeType": "DIRECTED",
                    "elkProperties": {
                        "org.eclipse.elk.edge.thickness": 1,
                    },
                },
                "none": {
                    "label": "None",
                    "elkEdgeType": "UNDIRECTED",
                    "elkProperties": {
                        "org.eclipse.elk.edge.thickness": 1,
                    },
                },
            },
        }
    )
