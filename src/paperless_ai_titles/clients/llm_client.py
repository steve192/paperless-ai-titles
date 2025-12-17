from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, cast

import httpx

from ..clients.paperless_client import PaperlessDocument
from ..core.config import Settings
from ..services.settings import SettingsService

logger = logging.getLogger(__name__)

@dataclass
class TitleSuggestion:
    title: str
    raw: Dict[str, Any]
    confidence: Optional[float] = None


@dataclass
class TitleEvaluation:
    acceptable: bool
    raw: Dict[str, Any]
    confidence: Optional[float] = None


class TitleLLMClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or SettingsService().effective_settings()
        self.base_url = str(self.settings.llm_base_url)
        self.headers = {"Authorization": f"Bearer {self.settings.llm_api_token}"}

    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        timeout = httpx.Timeout(self.settings.llm_request_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.base_url, json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def propose_title(self, text: str, metadata: Optional[PaperlessDocument] = None) -> TitleSuggestion:
        metadata = metadata or cast(PaperlessDocument, {})
        context_lines: list[str] = []
        # TODO: Let paperless client fetch strings instead of ids
        correspondent_id = metadata.get("correspondent")
        if isinstance(correspondent_id, int):
            context_lines.append(f"Correspondent ID: {correspondent_id}")
        doc_type_id = metadata.get("document_type")
        if isinstance(doc_type_id, int):
            context_lines.append(f"Document type ID: {doc_type_id}")
        created_value = metadata.get("created")
        if created_value:
            context_lines.append(f"Date: {created_value}")
        context = "\n".join(context_lines)
        snippet = self._truncate_text(text)
        instructions = (
            "You generate concise, specific document titles. Respond ONLY with JSON in the format "
            '{"title":"<title>","confidence":0-1}. '
            "Confidence must be a float between 0 and 1."
        )
        user_prompt = f"{context}\nDocument text snippet:\n{snippet}" if context else f"Document text snippet:\n{snippet}"
        payload = {
            "model": self.settings.llm_model_name,
            "temperature": 0.3,
            "messages": [
                {
                    "role": "system",
                    "content": instructions,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        }
        raw, parsed = await self._post_with_json_retry(payload, purpose="propose_title")
        title_value = parsed.get("title") if isinstance(parsed, dict) else None
        if not isinstance(title_value, str) or not title_value.strip():
            raise ValueError("LLM response missing 'title' field")
        title = title_value.strip()
        confidence = self._normalize_confidence(parsed.get("confidence")) if isinstance(parsed, dict) else None
        logger.debug(
            "LLM propose_title completed (len=%s) => '%s' confidence=%s",
            len(text),
            title.strip(),
            confidence,
        )
        return TitleSuggestion(title=title.strip(), raw=raw, confidence=confidence)

    async def evaluate_title(self, title: str, text: str) -> TitleEvaluation:
        snippet = self._truncate_text(text)
        instructions = (
            "Decide if a proposed document title matches the content. No generic titles allowed. Respond ONLY with JSON in the format "
            '{"decision":"GOOD|BAD","acceptable":true|false,"confidence":0-1}. '
            "Confidence must be a float between 0 and 1."
        )
        payload = {
            "model": self.settings.llm_model_name,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": instructions,
                },
                {
                    "role": "user",
                    "content": f"Title: {title}\nDocument excerpt:\n{snippet}",
                },
            ],
        }
        raw, parsed = await self._post_with_json_retry(payload, purpose="evaluate_title")
        decision_text = (parsed.get("decision") if isinstance(parsed, dict) else "").strip()
        acceptable_flag = parsed.get("acceptable") if isinstance(parsed, dict) else None
        decision = decision_text.upper()
        acceptable = acceptable_flag if isinstance(acceptable_flag, bool) else decision.startswith("GOOD")
        confidence = self._normalize_confidence(parsed.get("confidence")) if isinstance(parsed, dict) else None
        logger.debug(
            "LLM evaluate_title decision=%s acceptable=%s confidence=%s",
            decision,
            acceptable,
            confidence,
        )
        return TitleEvaluation(acceptable=acceptable, raw=raw, confidence=confidence)

    @staticmethod
    def _extract_content(response: Dict[str, Any]) -> str:
        choices = response.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        return content.strip()

    def _truncate_text(self, text: str) -> str:
        limit = max(1, int(self.settings.llm_prompt_char_limit))
        return text[:limit]

    async def _post_with_json_retry(
        self,
        payload: Dict[str, Any],
        *,
        purpose: str,
        max_attempts: int = 2,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Post to the LLM and ensure a JSON object response, retrying once if needed.

        Never returns a non-JSON response; instead raises ValueError after retries.
        """
        last_content: str | None = None
        attempts = max(1, int(max_attempts))
        for attempt in range(1, attempts + 1):
            raw = await self._post(payload)
            content = self._extract_content(raw)
            last_content = content
            parsed = self._parse_json_content(content)
            if isinstance(parsed, dict):
                return raw, parsed
            logger.warning(
                "LLM %s returned non-JSON content on attempt %s/%s: %r",
                purpose,
                attempt,
                attempts,
                content[:200],
            )
        raise ValueError(
            f"LLM returned non-JSON response for {purpose} after {attempts} attempts: {last_content!r}"
        )

    @staticmethod
    def _parse_json_content(content: str) -> Dict[str, Any] | None:
        text = content.strip()
        if not text:
            return None
        attempts = [text]
        if text.startswith("```"):
            attempts.append(TitleLLMClient._strip_code_fence(text))
        for candidate in attempts:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        lines = text.splitlines()
        if not lines:
            return text
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_confidence(value: Any) -> Optional[float]:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return None
        if confidence < 0:
            return 0.0
        if confidence > 1:
            return 1.0
        return confidence
