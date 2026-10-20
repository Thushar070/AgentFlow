"""Runnable workflow templates. Loading these never writes to the database."""

EXAMPLES = [
    {
        "id": "customer_validation",
        "name": "Customer validation",
        "description": "Check a customer's email and return a normalized validation result.",
        "version": 1,
        "input_schema": {
            "customer": {
                "type": "object",
                "required": True,
                "description": "Customer record with name and email.",
            }
        },
        "steps": [
            {
                "id": "email",
                "action": "get_json_field",
                "inputs": {"value": {"$input": "customer"}, "path": "email"},
            },
            {
                "id": "email_check",
                "action": "validate_email",
                "inputs": {"value": {"$ref": "email"}},
            },
            {
                "id": "customer_name",
                "action": "get_json_field",
                "inputs": {"value": {"$input": "customer"}, "path": "name"},
            },
            {
                "id": "validity",
                "type": "conditional",
                "condition": {
                    "left": {"$ref": {"step": "email_check", "path": "/value/valid"}},
                    "operator": "eq",
                    "right": True,
                },
                "then_steps": [
                    {
                        "id": "valid_result",
                        "action": "create_result",
                        "inputs": {
                            "fields": {
                                "valid": True,
                                "email": {"$ref": "email"},
                                "name": {"$ref": "customer_name"},
                                "customer": {"$input": "customer"},
                            }
                        },
                    }
                ],
                "else_steps": [
                    {
                        "id": "invalid_result",
                        "action": "create_result",
                        "inputs": {
                            "fields": {
                                "valid": False,
                                "email": {"$ref": "email"},
                                "name": {"$ref": "customer_name"},
                                "customer": {"$input": "customer"},
                            }
                        },
                    }
                ],
            },
        ],
    },
    {
        "id": "api_data_processing",
        "name": "API data processing",
        "description": "Fetch an allowlisted JSON API, filter active items, and total its amounts.",
        "version": 1,
        "input_schema": {
            "api_url": {
                "type": "string",
                "required": True,
                "description": "URL on a host configured in HTTP_ALLOWED_HOSTS.",
            }
        },
        "steps": [
            {
                "id": "fetch",
                "action": "http_request",
                "inputs": {"url": {"$input": "api_url"}, "expected_status": [200]},
            },
            {
                "id": "items",
                "action": "get_json_field",
                "inputs": {"value": {"$ref": "fetch"}, "path": "body.items"},
            },
            {
                "id": "active_items",
                "action": "filter_records",
                "inputs": {
                    "records": {"$ref": "items"},
                    "field": "active",
                    "operator": "eq",
                    "value": True,
                },
            },
            {
                "id": "amounts",
                "action": "get_json_field",
                "inputs": {"value": {"$ref": "fetch"}, "path": "body.amounts"},
            },
            {"id": "total", "action": "calculate_total", "inputs": {"values": {"$ref": "amounts"}}},
            {
                "id": "result",
                "action": "create_result",
                "inputs": {
                    "fields": {"active_items": {"$ref": "active_items"}, "total": {"$ref": "total"}}
                },
            },
        ],
    },
    {
        "id": "human_approval",
        "name": "Human approval",
        "description": "Pause a sensitive request until an authorized approver decides it.",
        "version": 1,
        "input_schema": {
            "request": {
                "type": "object",
                "required": True,
                "description": "Structured request requiring human review.",
            }
        },
        "steps": [
            {
                "id": "review",
                "action": "require_approval",
                "inputs": {"value": {"$input": "request"}},
            },
            {
                "id": "result",
                "action": "create_result",
                "inputs": {"fields": {"approved_request": {"$ref": "review"}}},
            },
        ],
    },
    {
        "id": "data_cleaning",
        "name": "Data cleaning pipeline",
        "description": (
            "Drop invalid/inactive records, trim selected fields, and total the cleaned amounts."
        ),
        "version": 1,
        "input_schema": {
            "records": {
                "type": "array",
                "required": True,
                "description": "Records with active, name, email, and numeric amount fields.",
            },
        },
        "steps": [
            {
                "id": "clean",
                "action": "clean_records",
                "inputs": {
                    "records": {"$input": "records"},
                    "required_fields": ["name", "email", "amount", "active"],
                    "trim_fields": ["name", "email"],
                    "active_only": True,
                    "amount_field": "amount",
                },
            },
            {
                "id": "records",
                "action": "get_json_field",
                "inputs": {"value": {"$ref": "clean"}, "path": "records"},
            },
            {
                "id": "amounts",
                "action": "get_json_field",
                "inputs": {"value": {"$ref": "clean"}, "path": "amounts"},
            },
            {
                "id": "removed",
                "action": "get_json_field",
                "inputs": {"value": {"$ref": "clean"}, "path": "removed_count"},
            },
            {
                "id": "total",
                "action": "calculate_total",
                "inputs": {"values": {"$ref": "amounts"}},
            },
            {
                "id": "result",
                "action": "create_result",
                "inputs": {
                    "fields": {
                        "records": {"$ref": "records"},
                        "total": {"$ref": "total"},
                        "removed_count": {"$ref": "removed"},
                    }
                },
            },
        ],
    },
]


def list_examples():
    """Return definitions as fresh JSON-compatible data for callers to edit."""
    import copy

    return copy.deepcopy(EXAMPLES)
