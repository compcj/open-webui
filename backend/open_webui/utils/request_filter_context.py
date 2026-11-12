"""Detached payloads for request filters, without copying runtime metadata handles."""

from copy import deepcopy


def snapshot_request_filter_input(payload: dict) -> dict:
    """Keep the unfiltered provider input separately from request-owned metadata."""
    return deepcopy({key: value for key, value in payload.items() if key != 'metadata'})


def copy_request_filter_input(
    baseline: dict,
    *,
    messages: list[dict] | None = None,
    metadata: dict | None = None,
) -> dict:
    """Build one mutable filter input without exposing its baseline or raw history."""
    source = {**baseline, **({'messages': messages} if messages is not None else {})}
    body = snapshot_request_filter_input(source)
    if metadata is not None:
        body['metadata'] = dict(metadata)
    return body
