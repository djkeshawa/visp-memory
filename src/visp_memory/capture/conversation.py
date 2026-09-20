"""Capture source-backed memories from conversation logs."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from visp_memory.capture.git import CaptureManifest, capture_content_hash
from visp_memory.core.beliefs import map_producer_belief_type
from visp_memory.core.llm import LLMClient, create_llm_client
from visp_memory.core.memory import Memory
from visp_memory.core.source_passages import turn_ranges, validate_source_quote
from visp_memory.core.trust import WriteChannel
from visp_memory.quality.secrets import redact_for_storage

logger = logging.getLogger(__name__)

CHUNK_SIZE = 20_000
EXTRACTION_VERSION = "f2"
ROLE_LINE = re.compile(r"(?m)^(user|assistant|system):[ \t]*")
DATE_LINE = re.compile(r"(?mi)^(?:session|conversation) date:[^\n]+")
_CATEGORIES = ("decisions", "learnings", "bugs", "tasks")


class ConversationCapture:
    """Capture memories while retaining the conversation as source evidence."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self.config = memory.config.capture
        self._client: Optional[LLMClient] = None

    def _get_client(self) -> LLMClient:
        if self._client:
            return self._client
        provider = self.config.llm_provider or self.memory.config.compression.llm_provider
        if not provider:
            raise ValueError(
                "No LLM provider configured for conversation capture. "
                "Set VISP_MEMORY_CAPTURE_LLM_PROVIDER."
            )
        self._client = create_llm_client(
            provider=provider,
            model=self.config.llm_model or self.memory.config.compression.llm_model,
            api_key=self.memory.config.embedding.api_key,
        )
        return self._client

    def parse_file(self, path: str, dry_run: bool = False) -> Dict[str, Any]:
        """Parse a conversation file and record memories."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return self.parse_text(
            path.read_text(encoding="utf-8"), source=path.name, dry_run=dry_run
        )

    def parse_text(
        self, text: str, source: str = "conversation", dry_run: bool = False
    ) -> Dict[str, Any]:
        """Extract source-backed memories from every bounded input chunk."""
        text, _ = redact_for_storage(text, None)
        manifest = CaptureManifest(self.memory)
        content_hash = capture_content_hash({"source": source, "text": text})
        overall_key = manifest._key("conversation", source)
        existing = manifest.data["entries"].get(overall_key)
        if not dry_run and existing and existing.get("content_hash") == content_hash:
            # Entries from the pre-source capture parser have no extraction
            # version and must be replayed under the source-backed contract.
            if (
                existing.get("completed", False)
                and existing.get("extraction_version") == EXTRACTION_VERSION
                and existing.get("status") != "failed"
            ):
                return self._unchanged_result(existing)

        client = self._get_client()
        descriptors = self._source_descriptors(text, source)
        if not dry_run and (
            not existing or existing.get("content_hash") != content_hash
        ):
            manifest.record("conversation", source, content_hash, [], status="in_progress")
            self._update_entry(
                manifest, overall_key, completed=False, source_memory_ids=[],
                source_turns=descriptors, extraction_version=EXTRACTION_VERSION,
            )
        source_ids = self._prepare_source_episodes(
            text, source, content_hash, descriptors, existing, dry_run, manifest, overall_key
        )
        stable_to_actual = dict(zip((d["stable_id"] for d in descriptors), source_ids))
        spans = self._chunk_spans(text, descriptors)
        if not dry_run:
            self._update_entry(
                manifest, overall_key, completed=False, source_memory_ids=list(source_ids),
                source_turns=descriptors, chunk_spans=spans, failures=[],
                extraction_version=EXTRACTION_VERSION,
            )

        totals = {category: 0 for category in _CATEGORIES}
        raw: dict[str, list[Any]] = {category: [] for category in _CATEGORIES}
        output_ids = list(source_ids)
        failures: list[dict[str, Any]] = []
        for start, end in spans:
            chunk_identity = f"{source}:{start}:{end}"
            chunk_hash = capture_content_hash({
                "source_hash": content_hash, "start": start, "end": end,
                "text": text[start:end], "extraction_version": EXTRACTION_VERSION,
            })
            chunk_key = manifest._key("conversation_chunk", chunk_identity)
            chunk_entry = manifest.data["entries"].get(chunk_key)
            if chunk_entry and chunk_entry.get("content_hash") != chunk_hash:
                chunk_entry = None
            if chunk_entry and not dry_run:
                output_ids.extend(chunk_entry.get("output_memory_ids", []))
            if (
                not dry_run and chunk_entry
                and chunk_entry.get("content_hash") == chunk_hash
                and chunk_entry.get(
                    "completed", chunk_entry.get("status") in ("complete", "unchanged")
                )
            ):
                output_ids.extend(chunk_entry.get("output_memory_ids", []))
                continue
            try:
                data = self._decode_response(client.completion(
                    prompt=self._prompt(text, source, descriptors, start, end),
                    system_prompt=self._system_prompt(),
                ))
            except Exception as exc:
                failure = {
                    "chunk": {"start": start, "end": end},
                    "reason": f"extraction failed: {exc}",
                }
                failures.append(failure)
                if not dry_run:
                    manifest.record(
                        "conversation_chunk", chunk_identity, chunk_hash,
                        list(chunk_entry.get("output_memory_ids", [])) if chunk_entry else [],
                        status="failed",
                    )
                    self._update_entry(
                        manifest, manifest._key("conversation_chunk", chunk_identity),
                        completed=False, offsets={"start": start, "end": end}, failure=failure,
                        completed_item_keys=(
                            chunk_entry.get("completed_item_keys", []) if chunk_entry else []
                        ),
                    )
                continue

            chunk_output_ids: list[str] = list(
                chunk_entry.get("output_memory_ids", [])
            ) if chunk_entry else []
            completed_item_keys = set(
                chunk_entry.get("completed_item_keys", [])
            ) if chunk_entry else set()
            claim_failures: list[dict[str, Any]] = []
            write_failed = False
            extraction_failed = False
            for category in _CATEGORIES:
                values = data.get(category, [])
                if not isinstance(values, list):
                    extraction_failed = True
                    failures.append({
                        "chunk": {"start": start, "end": end},
                        "category": category, "reason": "category must be a list",
                    })
                    continue
                raw[category].extend(values)
                for index, item in enumerate(values):
                    try:
                        claim, claim_source_ids = self._validate_claim(
                            category, item, text, descriptors, stable_to_actual
                        )
                    except Exception as exc:
                        claim_failures.append(
                            {"category": category, "index": index, "reason": str(exc)}
                        )
                        continue
                    item_key = self._item_key(category, claim)
                    if item_key in completed_item_keys:
                        continue
                    try:
                        if not dry_run:
                            memory_id = self._record_claim(category, claim, claim_source_ids)
                            chunk_output_ids.append(str(memory_id))
                            output_ids.append(str(memory_id))
                        completed_item_keys.add(item_key)
                        if not dry_run:
                            manifest.record(
                                "conversation_chunk", chunk_identity, chunk_hash,
                                chunk_output_ids, status="in_progress",
                            )
                            self._update_entry(
                                manifest, manifest._key("conversation_chunk", chunk_identity),
                                completed=False, offsets={"start": start, "end": end},
                                completed_item_keys=sorted(completed_item_keys),
                            )
                        totals[category] += 1
                    except Exception as exc:
                        write_failed = True
                        failures.append({
                            "chunk": {"start": start, "end": end},
                            "category": category, "index": index, "reason": f"write failed: {exc}",
                        })
                        break
                if write_failed:
                    break
            failures.extend(
                {"chunk": {"start": start, "end": end}, **failure}
                for failure in claim_failures
            )
            if not dry_run:
                manifest.record(
                    "conversation_chunk", chunk_identity, chunk_hash, chunk_output_ids,
                    status=(
                        "failed"
                        if write_failed or extraction_failed or claim_failures
                        else "complete"
                    ),
                )
                self._update_entry(
                    manifest, manifest._key("conversation_chunk", chunk_identity),
                    completed=not write_failed and not extraction_failed and not claim_failures,
                    offsets={"start": start, "end": end},
                    failures=claim_failures, completed_item_keys=sorted(completed_item_keys),
                )

        complete = (not failures) if dry_run else self._all_chunks_complete(
            manifest, spans, source, dry_run
        )
        if not dry_run:
            final = manifest.record(
                "conversation", source, content_hash, list(dict.fromkeys(output_ids)),
                status="changed",
            )
            self._update_entry(
                manifest, overall_key, completed=complete, source_memory_ids=list(source_ids),
                source_turns=descriptors, chunk_spans=spans, failures=failures,
                extraction_version=EXTRACTION_VERSION,
            )
        else:
            final = None
        result = {**totals, "raw": raw, "failures": failures}
        result["capture_manifest"] = {
            "status": final.get("status", "changed") if final else "dry_run",
            "completed": complete,
            "output_memory_ids": list(dict.fromkeys(output_ids)),
        }
        return result

    @staticmethod
    def _system_prompt() -> str:
        return """You are a source-grounded memory extraction system.
Extract decisions, learnings, bugs, and tasks only when directly supported by the
conversation source records. Every item MUST include exactly one source_id, an exact
quoted source span in quote, and the speaker. Use the source_id printed with the
record, not a made-up ID. The quote must be a complete verbatim span from that
record; it is the content that will be stored. Treat what/why/knowledge/description,
cause, and fix as labels only and never add details that are absent from quote.
Preserve negation and conditions. Return strict JSON in this shape, using empty
arrays when a category has no supported item:
{"decisions":[{"what":"...","why":"...","alternatives":[],"quote":"...","source_id":"...","speaker":"user|assistant|system"}],
 "learnings":[{"knowledge":"...","category":"fact|pattern","importance":0.0,"quote":"...","source_id":"...","speaker":"..."}],
 "bugs":[{"description":"...","cause":"...","fix":"...","quote":"...","source_id":"...","speaker":"..."}],
 "tasks":[{"description":"...","status":"todo","quote":"...","source_id":"...","speaker":"..."}]}"""

    @staticmethod
    def _prompt(
        text: str, source: str, descriptors: list[dict[str, Any]], start: int, end: int
    ) -> str:
        records = []
        for descriptor in descriptors:
            if descriptor["end"] <= start or descriptor["start"] >= end:
                continue
            records.append(
                f"source_id: {descriptor['stable_id']} offsets: "
                f"{descriptor['start']}:{descriptor['end']} "
                f"speaker: {descriptor['speaker']} "
                f"date: {descriptor.get('source_date') or 'unknown'}\n"
                f"{text[descriptor['start']:descriptor['end']]}"
            )
        return (
            f"Analyze this bounded portion of conversation source {source!r}.\n\n"
            + "\n\n".join(records)
        )

    @staticmethod
    def _decode_response(response: Any) -> dict[str, Any]:
        data = response if isinstance(response, dict) else json.loads(
            str(response).replace("```json", "").replace("```", "").strip()
        )
        if not isinstance(data, dict):
            raise ValueError("extraction response must be a JSON object")
        return data

    @staticmethod
    def _source_descriptors(text: str, source: str) -> list[dict[str, Any]]:
        ranges = turn_ranges(text) or [(0, len(text))]
        source_key = capture_content_hash(source)[:12]
        date_match = DATE_LINE.search(text)
        source_date = date_match.group(0) if date_match else None
        date_matches = list(DATE_LINE.finditer(text))
        descriptors = []
        index = 0
        first_start = ranges[0][0] if ranges else 0
        if first_start > 0:
            descriptors.append({
                "stable_id": f"conversation:{source_key}:{index}",
                "start": 0, "end": first_start,
                "speaker": "as recorded in source", "source_date": source_date,
                "content_hash": capture_content_hash(text[:first_start]),
            })
            index += 1
        for start, end in ranges:
            role = ROLE_LINE.match(text, start)
            speaker = role.group(1) if role else "as recorded in source"
            segment = start
            while segment < end:
                segment_end = min(end, segment + CHUNK_SIZE)
                preceding_dates = [m.group(0) for m in date_matches if m.start() <= segment]
                descriptors.append({
                    "stable_id": f"conversation:{source_key}:{index}",
                    "start": segment, "end": segment_end, "speaker": speaker,
                    "source_date": preceding_dates[-1] if preceding_dates else None,
                    "content_hash": capture_content_hash(text[segment:segment_end]),
                })
                index += 1
                segment = segment_end
        return descriptors

    @staticmethod
    def _chunk_spans(text: str, descriptors: list[dict[str, Any]]) -> list[tuple[int, int]]:
        if not text:
            return [(0, 0)]
        spans: list[tuple[int, int]] = []
        current = 0
        for descriptor in descriptors:
            turn_start, turn_end = descriptor["start"], descriptor["end"]
            if turn_start > current:
                while turn_start - current > CHUNK_SIZE:
                    spans.append((current, current + CHUNK_SIZE))
                    current += CHUNK_SIZE
                spans.append((current, turn_start))
            spans.append((turn_start, turn_end))
            current = turn_end
        while current < len(text):
            end = min(len(text), current + CHUNK_SIZE)
            spans.append((current, end))
            current = end
        return [(start, end) for start, end in spans if end > start or not text] or [(0, len(text))]

    def _prepare_source_episodes(
        self, text: str, source: str, content_hash: str,
        descriptors: list[dict[str, Any]], existing: dict[str, Any] | None, dry_run: bool,
        manifest: CaptureManifest, overall_key: str,
    ) -> list[str]:
        if dry_run:
            return [descriptor["stable_id"] for descriptor in descriptors]
        saved = (existing or {}).get("source_memory_ids") or []
        reusable = []
        if (existing or {}).get("content_hash") == content_hash:
            storage = getattr(self.memory, "_storage", None)
            for value in saved[: len(descriptors)]:
                if storage is not None:
                    try:
                        if storage.get_memory(str(value)) is None:
                            break
                    except Exception:
                        break
                reusable.append(str(value))
        repo_id = self.memory.config.repo_id
        source_ids = list(reusable)
        for d in descriptors[len(reusable):]:
            source_ids.append(str(self.memory.record(
                text[d["start"]:d["end"]], category="session", repo_id=repo_id,
                context={
                "capture_source": source, "capture_source_id": d["stable_id"],
                "source_start": d["start"], "source_end": d["end"],
                "speaker": d["speaker"], "source_date": d.get("source_date"),
                "source_content_hash": content_hash,
                }, tags=["conversation_source"], _write_channel=WriteChannel.CONVERSATION,
            )))
            manifest_entry = manifest.data["entries"].get(overall_key)
            if manifest_entry is not None:
                manifest_entry["source_memory_ids"] = list(source_ids)
                manifest._write()
        return source_ids

    @staticmethod
    def _update_entry(manifest: CaptureManifest, key: str, **updates: Any) -> None:
        entry = manifest.data["entries"].get(key)
        if entry is not None:
            entry.update(updates)
            manifest._write()

    @staticmethod
    def _item_key(category: str, item: Any) -> str:
        # Model-proposed labels, order and importance may vary on a retry. The
        # persisted unit is the attributed source span, whose identity is stable.
        return capture_content_hash({
            "category": category, "source_id": item["source_id"],
            "start": item["source_start"], "end": item["source_end"],
            "context": item["source_context"],
        })

    @staticmethod
    def _unchanged_result(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "decisions": 0, "learnings": 0, "bugs": 0, "tasks": 0, "raw": {},
            "failures": entry.get("failures", []),
            "capture_manifest": {
                "status": "unchanged", "completed": True,
                "output_memory_ids": entry.get("output_memory_ids", []),
            },
        }

    @staticmethod
    def _all_chunks_complete(
        manifest: CaptureManifest, spans: list[tuple[int, int]], source: str, dry_run: bool
    ) -> bool:
        if dry_run:
            return True
        return all(
            (entry := manifest.data["entries"].get(
                manifest._key("conversation_chunk", f"{source}:{start}:{end}")
            ))
            and entry.get("completed", entry.get("status") in ("complete", "unchanged"))
            for start, end in spans
        )

    def _validate_claim(
        self, category: str, item: Any, text: str, descriptors: list[dict[str, Any]],
        stable_to_actual: dict[str, str],
    ) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(item, dict):
            raise ValueError("extraction item must be an object")
        source_ref = item.get(
            "source_id", item.get("sourceID", item.get("source_ids"))
        )
        quote = item.get(
            "quote", item.get("source_quote", item.get("quoted_text", item.get("source_span")))
        )
        if not source_ref:
            raise ValueError("missing source_id")
        if isinstance(source_ref, list):
            if len(source_ref) != 1:
                raise ValueError("exactly one source_id is required")
            source_ref = source_ref[0]
        source_ref = str(source_ref)
        descriptor = next((d for d in descriptors if d["stable_id"] == source_ref), None)
        if descriptor is None:
            alias = re.fullmatch(r"(?:turn|source|episode)[_-]?(\d+)", source_ref, re.IGNORECASE)
            if alias:
                source_ref = alias.group(1)
        if descriptor is None and source_ref.isdigit():
            index = int(source_ref)
            descriptor = descriptors[index] if 0 <= index < len(descriptors) else None
        if descriptor is None:
            raise ValueError("unknown source_id")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError("missing source quote")
        validated = validate_source_quote(text, quote)
        if not descriptor["start"] <= validated["start"] < validated["end"] <= descriptor["end"]:
            raise ValueError("quote does not belong to source_id")
        speaker = item.get("speaker")
        if not speaker:
            raise ValueError("missing speaker")
        if str(speaker).casefold() != validated["speaker"].casefold():
            raise ValueError("speaker does not match source")
        for start_key, end_key in (("source_start", "source_end"), ("start", "end")):
            if start_key in item and int(item[start_key]) != validated["start"]:
                raise ValueError("source start does not match quote")
            if end_key in item and int(item[end_key]) != validated["end"]:
                raise ValueError("source end does not match quote")
        claim_fields = self._claim_fields(category, item)
        if not self._claim_supported(claim_fields, quote):
            raise ValueError("generated claim is not supported by its quoted source")
        context_ids = []
        for span in validated["passage_spans"]:
            for candidate in descriptors:
                if candidate["start"] < span["end"] and span["start"] < candidate["end"]:
                    actual = stable_to_actual.get(candidate["stable_id"])
                    if actual and actual not in context_ids:
                        context_ids.append(actual)
        actual = stable_to_actual.get(descriptor["stable_id"])
        if actual and actual not in context_ids:
            context_ids.append(actual)
        if not context_ids:
            raise ValueError("source_id has no persisted source episode")
        enriched = dict(item)
        enriched.update({
            "source_id": descriptor["stable_id"], "source_start": validated["start"],
            "source_end": validated["end"], "speaker": validated["speaker"],
            "source_context": validated["context"],
        })
        return enriched, context_ids

    @staticmethod
    def _claim_fields(category: str, item: dict[str, Any]) -> list[str]:
        if category == "decisions":
            fields = (item.get("what"), item.get("why"))
        elif category == "learnings":
            fields = (item.get("knowledge"),)
        elif category == "bugs":
            fields = (item.get("description"), item.get("cause"), item.get("fix"))
        else:
            fields = (item.get("description"),)
        values = [str(value) for value in fields if value]
        if not values:
            raise ValueError("extraction item has no claim text")
        return values

    @staticmethod
    def _claim_supported(claim_fields: list[str], quote: str) -> bool:
        normalized_quote = " ".join(quote.casefold().split())
        return all(
            " ".join(field.casefold().split()) in normalized_quote
            for field in claim_fields
        )

    def _record_claim(
        self, category: str, item: dict[str, Any], source_ids: list[str]
    ) -> str:
        repo_id = self.memory.config.repo_id
        category_hint = {
            "decisions": "preference",
            "learnings": item.get("category", "fact"),
            "bugs": "negative",
            "tasks": "fact",
        }[category]
        memory_id = self.memory.learn(
            knowledge=item["source_context"], category=map_producer_belief_type(category_hint),
            importance=item.get("importance", 0.5), repo_id=repo_id,
            tags=[f"capture_category:{category}"], source_episodes=source_ids,
            _write_channel=WriteChannel.CONVERSATION,
        )
        return str(memory_id)
