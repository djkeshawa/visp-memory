"""Backend-independent validation of new semantic beliefs."""

from datetime import timedelta

from visp_memory.core.beliefs import (
    EpistemicStatus,
    normalize_belief_type,
    normalize_epistemic_status,
)
from visp_memory.core.clock import parse_utc


def prepare_belief(
    layer,
    category,
    epistemic_status,
    metadata,
    created_at,
    authority_attestation=None,
    replaces_belief_id=None,
):
    belief_type = None
    if layer == "semantic":
        category = category or "fact"
        belief_type = normalize_belief_type(category)
        category = belief_type
        if belief_type == "hypothesis":
            if epistemic_status not in (None, EpistemicStatus.HYPOTHESIZED.value):
                raise ValueError("a hypothesis must begin with hypothesized epistemic status")
            epistemic_status = EpistemicStatus.HYPOTHESIZED.value
            created_time = parse_utc(created_at)
            if created_time is None:
                raise ValueError("created_at must be a valid timestamp")
            maximum_valid_to = created_time + timedelta(days=7)
            supplied_valid_to = metadata.get("valid_to")
            if supplied_valid_to is None:
                metadata = {**metadata, "valid_to": maximum_valid_to.isoformat()}
            else:
                valid_to = parse_utc(supplied_valid_to)
                if valid_to is None:
                    raise ValueError("hypothesis valid_to must be a valid timestamp")
                if valid_to > maximum_valid_to:
                    raise ValueError("hypothesis valid_to exceeds the seven-day maximum TTL")
                metadata = {**metadata, "valid_to": valid_to.isoformat()}
        elif belief_type == "prohibition":
            if epistemic_status not in (None, EpistemicStatus.OBSERVED.value):
                raise ValueError("a verified prohibition must begin with observed epistemic status")
            epistemic_status = EpistemicStatus.OBSERVED.value
        else:
            epistemic_status = normalize_epistemic_status(
                epistemic_status or EpistemicStatus.INFERRED.value
            )
        if belief_type != "prohibition" and authority_attestation is not None:
            raise ValueError("authority attestation applies only to a prohibition belief")
        if replaces_belief_id is not None and belief_type != "prohibition":
            raise ValueError("replaces_belief_id applies only to a prohibition belief")
    elif epistemic_status is not None:
        raise ValueError("epistemic status applies only to a semantic belief")
    else:
        category = category or "general"
    return category, belief_type, epistemic_status, metadata
