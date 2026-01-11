"""
Conversation Capture Module

Parses LLM conversation logs (e.g. from Claude Desktop, ChatGPT exports)
to extract structured memories.
"""

import logging
import json
from typing import List, Dict, Any, Optional
from pathlib import Path

from llm_memory.core.memory import Memory
from llm_memory.core.llm import create_llm_client, LLMClient

logger = logging.getLogger(__name__)

class ConversationCapture:
    """Captures memories from conversation logs."""

    def __init__(self, memory: Memory):
        self.memory = memory
        self.config = memory.config.capture
        self._client: Optional[LLMClient] = None

    def _get_client(self) -> LLMClient:
        if self._client:
            return self._client
        
        provider = self.config.llm_provider
        # Fallback to compression provider if not set in capture
        if not provider:
            provider = self.memory.config.compression.llm_provider
            
        if not provider:
            raise ValueError("No LLM provider configured for conversation capture. Set LLM_MEMORY_CAPTURE_LLM_PROVIDER.")
            
        self._client = create_llm_client(
            provider=provider,
            model=self.config.llm_model or self.memory.config.compression.llm_model,
            api_key=self.memory.config.embedding.api_key # Reuse generic key if applicable, or rely on env
        )
        return self._client

    def parse_file(self, path: str, dry_run: bool = False) -> Dict[str, Any]:
        """Parse a conversation file and record memories."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
            
        content = path.read_text(encoding="utf-8")
        return self.parse_text(content, source=path.name, dry_run=dry_run)

    def parse_text(self, text: str, source: str = "conversation", dry_run: bool = False) -> Dict[str, Any]:
        """Parse conversation text and record memories."""
        client = self._get_client()
        
        system_prompt = """You are a Memory Extraction System. 
Your goal is to analyze the user's conversation with an AI and extract structured memories.

Extract these categories:
1. DECISIONS: Key architectural or design choices made (what, why, alternatives).
2. LEARNINGS: New facts, patterns, or technical knowledge gained.
3. BUGS: Fixed bugs or discovered issues (description, cause, fix).
4. TASKS: Items marked as todo or future work.

Output strictly valid JSON with this schema:
{
  "decisions": [{"what": "...", "why": "...", "alternatives": ["..."]}],
  "learnings": [{"knowledge": "...", "category": "fact/pattern", "importance": 0.0-1.0}],
  "bugs": [{"description": "...", "cause": "...", "fix": "..."}],
  "tasks": [{"description": "...", "status": "todo"}]
}
If nothing relevant is found for a category, return an empty list.
"""
        
        prompt = f"Analyze this conversation log and extract memories:\n\n{text[:20000]}" # Limit context
        
        try:
            response = client.completion(
                prompt=prompt, 
                system_prompt=system_prompt,
                response_format={"type": "json_object"} if hasattr(client, 'client') and hasattr(client.client, 'chat') else None # Hint for OpenAI
            )
            
            # Clean response if markdown code block
            clean_resp = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_resp)
            
            summary = {
                "decisions": len(data.get("decisions", [])),
                "learnings": len(data.get("learnings", [])),
                "bugs": len(data.get("bugs", [])),
                "tasks": len(data.get("tasks", [])),
                "raw": data
            }

            if dry_run:
                return summary

            # Record Memories
            repo_id = self.memory.config.repo_id

            for d in data.get("decisions", []):
                self.memory.decision(
                    what=d["what"],
                    why=d.get("why", "Unknown"),
                    alternatives=d.get("alternatives"),
                    repo_id=repo_id
                )

            for l in data.get("learnings", []):
                self.memory.learn(
                    knowledge=l["knowledge"],
                    category=l.get("category", "fact"),
                    importance=l.get("importance", 0.5),
                    repo_id=repo_id
                )

            for b in data.get("bugs", []):
                self.memory.record(
                    event=f"Bug: {b['description']}",
                    category="bug",
                    metadata={"cause": b.get("cause"), "fix": b.get("fix")},
                    repo_id=repo_id
                )

            return summary

        except Exception as e:
            logger.error(f"Failed to parse conversation: {e}")
            raise
