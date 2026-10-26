"""Placeholder for the KeepFrame API (services/api not present in this checkout). Always 503."""

import json


def handler(event, _context):
    return {
        "statusCode": 503,
        "headers": {"content-type": "application/json"},
        "body": json.dumps({"error": "placeholder", "detail": "KeepFrame API image not deployed yet"}),
    }
