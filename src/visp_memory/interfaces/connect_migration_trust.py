"""Apply import-channel trust only to the connect migration payload."""

from copy import deepcopy

from visp_memory.core.trust import WriteChannel, channel_policy, with_channel_provenance


def downgrade_for_migration(graph: dict) -> dict:
    """Copy the graph with external trust and no carried memory approvals."""
    transformed = deepcopy(graph)
    channel = WriteChannel.IMPORT
    policy = channel_policy(channel)
    for rows in (transformed.get("memories") or {}).values():
        for row in rows:
            row["tags"] = with_channel_provenance(row.get("tags"), channel)
            row["source"] = policy.source
            row["approved_by"] = None
            row["approved_at"] = None
            row["metadata"] = {**(row.get("metadata") or {}), "write_channel": channel.value}
    for row in transformed.get("evidence") or []:
        row["provenance"] = policy.provenance.value
        row["metadata"] = {**(row.get("metadata") or {}), "write_channel": channel.value}
    # Authority claims bind content, scope and Evidence hashes, not provenance or
    # approvals. Keeping those signed fields intact preserves signature validation;
    # stripping authority would make a prohibition graph invalid at import.
    return transformed
