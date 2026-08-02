"""Offline signing and runtime verification for prohibition attestations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from visp_memory.core.clock import parse_utc, utc_now

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    ED25519_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by controlled mutation
    InvalidSignature = Exception
    Ed25519PrivateKey = None
    Ed25519PublicKey = None
    ED25519_AVAILABLE = False


PROHIBITION_AUTHORITY_KEYS_ENV = "VISP_MEMORY_PROHIBITION_AUTHORITY_KEYS"
ATTESTATION_VERSION = "visp.prohibition.v1"
ATTESTATION_ALGORITHM = "ed25519"
FUTURE_CLOCK_TOLERANCE = timedelta(minutes=5)

_ENVELOPE_REQUIRED = {
    "attestation_version",
    "algorithm",
    "key_id",
    "nonce",
    "issued_at",
    "claim",
    "signature",
}
_ENVELOPE_OPTIONAL = {"not_before"}
_CLAIM_REQUIRED = {
    "content",
    "content_hash",
    "belief_type",
    "repo_id",
    "environment",
    "task_type",
    "valid_from",
    "valid_to",
    "evidence",
}
_CLAIM_OPTIONAL = {"replaces_belief_id"}


class ProhibitionAuthorityError(ValueError):
    """Raised when a prohibition lacks a valid authority attestation."""


@dataclass(frozen=True)
class VerifiedProhibitionAttestation:
    digest: str
    key_id: str
    nonce: str
    envelope: str


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProhibitionAuthorityError(
                f"duplicate prohibition attestation field: {key}"
            )
        result[key] = value
    return result


def _strict_json(raw: str, *, context: str) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        raise ProhibitionAuthorityError(f"{context} must be non-empty JSON text")
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProhibitionAuthorityError(f"invalid JSON constant: {value}")
            ),
        )
    except ProhibitionAuthorityError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProhibitionAuthorityError(f"malformed {context}") from exc


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProhibitionAuthorityError(
            "attestation is not canonically serializable"
        ) from exc


def _closed_object(
    value: Any, *, required: set[str], optional: set[str], name: str
) -> dict:
    if not isinstance(value, dict):
        raise ProhibitionAuthorityError(f"{name} must be an object")
    fields = set(value)
    missing = required - fields
    unknown = fields - required - optional
    if missing:
        raise ProhibitionAuthorityError(
            f"{name} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ProhibitionAuthorityError(
            f"{name} has unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _normalized_scope(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ProhibitionAuthorityError(f"claim {field} must be a string collection")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ProhibitionAuthorityError(f"claim {field} contains an invalid value")
    return sorted({item.strip().casefold() for item in values})


def _normalized_bound(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    parsed = parse_utc(value)
    if parsed is None:
        raise ProhibitionAuthorityError(f"claim {field} must be a valid timestamp")
    return parsed.isoformat()


def _normalized_evidence(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ProhibitionAuthorityError("claim evidence must be a non-empty list")
    normalized = []
    for item in value:
        item = _closed_object(
            item,
            required={"id", "content_hash"},
            optional=set(),
            name="claim evidence item",
        )
        evidence_id = item["id"]
        content_hash = item["content_hash"]
        if not isinstance(evidence_id, str) or not evidence_id:
            raise ProhibitionAuthorityError("claim evidence id must be non-empty")
        if (
            not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(char not in "0123456789abcdef" for char in content_hash)
        ):
            raise ProhibitionAuthorityError("claim evidence content_hash is invalid")
        normalized.append({"id": evidence_id, "content_hash": content_hash})
    normalized.sort(key=lambda item: (item["id"], item["content_hash"]))
    if len({item["id"] for item in normalized}) != len(normalized):
        raise ProhibitionAuthorityError("claim evidence contains duplicate ids")
    return normalized


def build_prohibition_claim(
    *,
    content: str,
    repo_id: str,
    evidence: list[dict[str, str]],
    environment: Any = None,
    task_type: Any = None,
    valid_from: Any = None,
    valid_to: Any = None,
    replaces_belief_id: str | None = None,
) -> dict[str, Any]:
    """Build the exact normalized claim signed by an offline authority."""
    if not isinstance(content, str) or not content:
        raise ProhibitionAuthorityError("claim content must be non-empty")
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise ProhibitionAuthorityError("claim repo_id must be non-empty")
    claim = {
        "content": content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "belief_type": "prohibition",
        "repo_id": repo_id.strip(),
        "environment": _normalized_scope(environment, field="environment"),
        "task_type": _normalized_scope(task_type, field="task_type"),
        "valid_from": _normalized_bound(valid_from, field="valid_from"),
        "valid_to": _normalized_bound(valid_to, field="valid_to"),
        "evidence": _normalized_evidence(evidence),
    }
    if replaces_belief_id is not None:
        if not isinstance(replaces_belief_id, str) or not replaces_belief_id:
            raise ProhibitionAuthorityError("replaces_belief_id must be non-empty")
        claim["replaces_belief_id"] = replaces_belief_id
    return claim


def _iso_timestamp(value: Any, *, field: str) -> str:
    parsed = parse_utc(value)
    if parsed is None:
        raise ProhibitionAuthorityError(f"{field} must be a valid timestamp")
    return parsed.isoformat()


def _decode_base64(value: Any, *, field: str, urlsafe: bool = False) -> bytes:
    if not isinstance(value, str) or not value:
        raise ProhibitionAuthorityError(f"{field} must be non-empty base64")
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(
            padded,
            altchars=b"-_" if urlsafe else None,
            validate=True,
        )
    except (ValueError, TypeError) as exc:
        raise ProhibitionAuthorityError(f"{field} is malformed base64") from exc


def _encode_signature(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def sign_prohibition_attestation(
    claim: dict[str, Any],
    *,
    key_id: str,
    private_key: str,
    nonce: str,
    issued_at: Any = None,
    not_before: Any = None,
) -> str:
    """Sign a prohibition claim for an explicit offline signer path."""
    if not ED25519_AVAILABLE:
        raise ProhibitionAuthorityError("Ed25519 support is unavailable")
    claim = _closed_object(
        claim,
        required=_CLAIM_REQUIRED,
        optional=_CLAIM_OPTIONAL,
        name="prohibition claim",
    )
    if not isinstance(key_id, str) or not key_id:
        raise ProhibitionAuthorityError("key_id must be non-empty")
    if not isinstance(nonce, str) or not nonce:
        raise ProhibitionAuthorityError("nonce must be non-empty")
    envelope = {
        "attestation_version": ATTESTATION_VERSION,
        "algorithm": ATTESTATION_ALGORITHM,
        "key_id": key_id,
        "nonce": nonce,
        "issued_at": _iso_timestamp(issued_at or utc_now(), field="issued_at"),
        "claim": claim,
    }
    if not_before is not None:
        envelope["not_before"] = _iso_timestamp(not_before, field="not_before")
    private_raw = _decode_base64(private_key, field="private key")
    if len(private_raw) != 32:
        raise ProhibitionAuthorityError(
            "private key must contain 32 raw Ed25519 bytes"
        )
    try:
        signer = Ed25519PrivateKey.from_private_bytes(private_raw)
        signature = signer.sign(_canonical_json(envelope).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ProhibitionAuthorityError(
            "private key is not a valid Ed25519 key"
        ) from exc
    envelope["signature"] = _encode_signature(signature)
    return _canonical_json(envelope)


def _public_key(key_id: str):
    raw_map = os.environ.get(PROHIBITION_AUTHORITY_KEYS_ENV)
    if not raw_map:
        raise ProhibitionAuthorityError(
            "no matching prohibition authority public key"
        )
    keys = _strict_json(raw_map, context="prohibition authority public key map")
    if not isinstance(keys, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in keys.items()
    ):
        raise ProhibitionAuthorityError(
            "prohibition authority public key map is malformed"
        )
    encoded = keys.get(key_id)
    if encoded is None:
        raise ProhibitionAuthorityError(
            "no matching prohibition authority public key"
        )
    public_raw = _decode_base64(encoded, field="public key")
    if len(public_raw) != 32:
        raise ProhibitionAuthorityError(
            "public key must contain 32 raw Ed25519 bytes"
        )
    try:
        return Ed25519PublicKey.from_public_bytes(public_raw)
    except (TypeError, ValueError) as exc:
        raise ProhibitionAuthorityError(
            "public key is not a valid Ed25519 key"
        ) from exc


def verify_prohibition_attestation(
    raw_envelope: str,
    *,
    content: str,
    repo_id: str,
    metadata: dict[str, Any],
    evidence: list[dict[str, str]],
) -> VerifiedProhibitionAttestation:
    """Verify one envelope against the exact LocalStorage write candidate."""
    if not ED25519_AVAILABLE:
        raise ProhibitionAuthorityError("Ed25519 support is unavailable")
    envelope = _closed_object(
        _strict_json(raw_envelope, context="prohibition attestation"),
        required=_ENVELOPE_REQUIRED,
        optional=_ENVELOPE_OPTIONAL,
        name="prohibition attestation",
    )
    if envelope["attestation_version"] != ATTESTATION_VERSION:
        raise ProhibitionAuthorityError("unknown prohibition attestation version")
    if envelope["algorithm"] != ATTESTATION_ALGORITHM:
        raise ProhibitionAuthorityError("unknown prohibition attestation algorithm")
    if not isinstance(envelope["key_id"], str) or not envelope["key_id"]:
        raise ProhibitionAuthorityError("attestation key_id must be non-empty")
    if not isinstance(envelope["nonce"], str) or not envelope["nonce"]:
        raise ProhibitionAuthorityError("attestation nonce must be non-empty")
    issued_at = parse_utc(envelope["issued_at"])
    if issued_at is None:
        raise ProhibitionAuthorityError("attestation issued_at is malformed")
    now = utc_now()
    if issued_at > now + FUTURE_CLOCK_TOLERANCE:
        raise ProhibitionAuthorityError("attestation issued_at is in the future")
    if "not_before" in envelope:
        not_before = parse_utc(envelope["not_before"])
        if not_before is None:
            raise ProhibitionAuthorityError("attestation not_before is malformed")
        if now < not_before:
            raise ProhibitionAuthorityError("attestation is not yet valid")

    claim = _closed_object(
        envelope["claim"],
        required=_CLAIM_REQUIRED,
        optional=_CLAIM_OPTIONAL,
        name="prohibition claim",
    )
    expected_claim = build_prohibition_claim(
        content=content,
        repo_id=repo_id,
        environment=metadata.get("environment"),
        task_type=metadata.get("task_type"),
        valid_from=metadata.get("valid_from"),
        valid_to=metadata.get("valid_to"),
        evidence=evidence,
        replaces_belief_id=claim.get("replaces_belief_id"),
    )
    if claim != expected_claim:
        raise ProhibitionAuthorityError(
            "signed prohibition claim does not match the write candidate"
        )

    signature = _decode_base64(
        envelope["signature"], field="attestation signature", urlsafe=True
    )
    unsigned = {
        key: value for key, value in envelope.items() if key != "signature"
    }
    try:
        _public_key(envelope["key_id"]).verify(
            signature, _canonical_json(unsigned).encode("utf-8")
        )
    except InvalidSignature as exc:
        raise ProhibitionAuthorityError(
            "prohibition attestation signature is invalid"
        ) from exc
    canonical = _canonical_json(envelope)
    return VerifiedProhibitionAttestation(
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        key_id=envelope["key_id"],
        nonce=envelope["nonce"],
        envelope=canonical,
    )

