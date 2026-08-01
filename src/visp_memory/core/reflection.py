"""Source-backed runbook and principle reflection proposals."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from visp_memory.core.clock import utc_now_iso
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.trust import WriteChannel, channel_policy, with_channel_provenance


class ReflectionEngine:
    """Derive reviewable higher-order memories from repeated evidence."""

    def __init__(self, storage, model_router: ModelRouter):
        self.storage = storage
        self.model_router = model_router

    def preview(self, repo_id: str, *, min_evidence: int = 3) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for memory in self.storage.list_memories(
            repo_id=repo_id, status="active", limit=100000
        ):
            if memory.get("layer") not in {"episodic", "semantic"}:
                continue
            metadata = memory.get("metadata") or {}
            keys = [
                *(f"file:{path}" for path in metadata.get("files") or []),
                *(f"tag:{tag}" for tag in memory.get("tags") or []),
            ]
            if not keys:
                keys.append(f"category:{memory.get('category', 'general')}")
            for key in set(keys):
                groups[key].append(memory)

        proposals = []
        for key, evidence in groups.items():
            unique = sorted(
                {memory["id"]: memory for memory in evidence}.values(),
                key=lambda memory: (str(memory.get("created_at") or ""), memory["id"]),
            )
            if len(unique) < max(2, min_evidence):
                continue
            evidence_ids = [memory["id"] for memory in unique]
            digest = hashlib.sha256("|".join(sorted(evidence_ids)).encode()).hexdigest()[:12]
            label = key.split(":", 1)[-1]
            proposals.append(
                {
                    "id": f"reflection_{digest}",
                    "key": key,
                    "title": f"Runbook for {label}",
                    "evidence_ids": evidence_ids,
                    "evidence_count": len(evidence_ids),
                    "preview": " ".join(
                        memory.get("content", "").splitlines()[0][:180] for memory in unique[:4]
                    ),
                }
            )
        return sorted(proposals, key=lambda item: (-item["evidence_count"], item["key"]))

    def materialize(
        self,
        *,
        repo_id: str,
        title: str,
        evidence_ids: list[str],
        actor_id: str,
    ) -> dict[str, Any]:
        evidence = []
        for memory_id in dict.fromkeys(evidence_ids):
            memory = self.storage.get_memory(memory_id)
            if (
                not memory
                or memory.get("repo_id") != repo_id
                or memory.get("status") != "active"
            ):
                raise ValueError("Reflection evidence must be active and from one repository")
            evidence.append(memory)
        if len(evidence) < 2:
            raise ValueError("At least two evidence memories are required")

        evidence_text = "\n".join(
            f"- [{memory['id']}] {memory.get('content', '')}" for memory in evidence
        )
        content = f"{title}\n\n{evidence_text}"
        provider = "deterministic"
        model = None
        if self.model_router.configured:
            result = self.model_router.complete(
                "reflection",
                (
                    f"Create a concise coding runbook titled '{title}' from this evidence. "
                    "Preserve important constraints and cite source IDs.\n\n"
                    f"{evidence_text[:12000]}"
                ),
                system_prompt=(
                    "Produce a durable, evidence-grounded runbook. Do not invent facts."
                ),
            )
            content = result["text"]
            provider = result["provider"]
            model = result["model"]
        confidence_values = [
            float((memory.get("metadata") or {}).get("confidence", 0.5))
            for memory in evidence
        ]
        write_channel = WriteChannel.REFLECTION
        policy = channel_policy(write_channel)
        metadata = {
            "title": title,
            "reflection": True,
            "reflection_type": "runbook",
            "evidence": [{"memory_id": memory["id"]} for memory in evidence],
            "lineage": [memory["id"] for memory in evidence],
            "confidence": round(sum(confidence_values) / len(confidence_values), 4),
            "provider": provider,
            "model": model,
            "derived_at": utc_now_iso(),
            "derived_by": actor_id,
            "write_channel": write_channel.value,
        }
        memory_id = self.storage.store_memory(
            content,
            layer="semantic",
            category="runbook",
            repo_id=repo_id,
            source_ids=[memory["id"] for memory in evidence],
            metadata=metadata,
            tags=with_channel_provenance(
                ["runbook", "reflection"], write_channel
            ),
            source=policy.source,
            auto_link=False,
        )
        return {"id": memory_id, "content": content, "metadata": metadata}
