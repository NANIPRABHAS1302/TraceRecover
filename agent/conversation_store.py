"""Session-isolated, privacy-conscious conversation and query history store."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from config import PROJECT_ROOT


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SECRET_SCRUB_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"sk-ant-[0-9A-Za-z_-]{16,}"),
    re.compile(r"sk-[0-9A-Za-z_-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
)


def scrub_secrets(text: str) -> str:
    """Redact credential-like tokens before writing conversation records to disk."""
    cleaned = text
    for pattern in _SECRET_SCRUB_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return scrub_secrets(obj)
    if isinstance(obj, list):
        return [_scrub_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {str(k): _scrub_obj(v) for k, v in obj.items()}
    return obj


def _sanitize_session_id(raw: str | None) -> str:
    candidate = (raw or "default").strip()
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip("-.")
    if not candidate:
        return "default"
    return candidate[:64]


class ConversationStore:
    """Thread-safe JSON store for multi-turn conversations isolated per X-Session-ID."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        *,
        max_conversations_per_session: int | None = None,
        max_turns_per_conversation: int = 100,
    ) -> None:
        resolved = base_dir or os.getenv(
            "TRACERECOVER_CONVERSATIONS_DIR",
            str(PROJECT_ROOT / "logs" / "conversations"),
        )
        self.base_dir = Path(resolved).resolve()
        self.max_conversations_per_session = (
            max_conversations_per_session
            if max_conversations_per_session is not None
            else max(5, int(os.getenv("TRACERECOVER_MAX_CONVERSATIONS_PER_SESSION", "50")))
        )
        self.max_turns_per_conversation = max(10, int(max_turns_per_conversation))
        self._lock = threading.Lock()

    def _session_dir(self, session_id: str) -> Path:
        safe_session = _sanitize_session_id(session_id)
        path = (self.base_dir / safe_session).resolve()
        path.relative_to(self.base_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _conversation_path(self, session_id: str, conversation_id: str) -> Path:
        if not _SAFE_ID_RE.fullmatch(conversation_id or ""):
            raise ValueError("Invalid conversation_id format.")
        sdir = self._session_dir(session_id)
        target = (sdir / f"{conversation_id}.json").resolve()
        target.relative_to(sdir)
        return target

    def create_conversation(
        self, session_id: str = "default", *, title: str = "New Conversation"
    ) -> dict[str, Any]:
        """Create and persist a new conversation inside the caller's session namespace."""
        safe_session = _sanitize_session_id(session_id)
        conv_id = f"conv-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
        now = datetime.now(timezone.utc).isoformat()
        record: dict[str, Any] = {
            "conversation_id": conv_id,
            "session_id": safe_session,
            "title": scrub_secrets((title or "New Conversation").strip()[:120]),
            "created_at": now,
            "updated_at": now,
            "turns": [],
        }
        with self._lock:
            path = self._conversation_path(safe_session, conv_id)
            path.write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            self._enforce_retention_locked(safe_session)
        return self._summarize_conversation(record)

    def _enforce_retention_locked(self, safe_session: str) -> None:
        sdir = self._session_dir(safe_session)
        files = sorted(
            (p for p in sdir.glob("*.json") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in files[self.max_conversations_per_session :]:
            stale.unlink(missing_ok=True)

    def _read_raw(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Corrupted conversation file.")
        return data

    def _write_raw(self, path: Path, record: Mapping[str, Any]) -> None:
        path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _summarize_conversation(record: Mapping[str, Any]) -> dict[str, Any]:
        turns = list(record.get("turns", []))
        last_turn = turns[-1] if turns else None
        return {
            "conversation_id": record.get("conversation_id"),
            "session_id": record.get("session_id"),
            "title": record.get("title", "Conversation"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "turn_count": len(turns),
            "last_decision": last_turn.get("final_decision") if isinstance(last_turn, Mapping) else None,
            "last_provider": last_turn.get("provider") if isinstance(last_turn, Mapping) else None,
            "preview": str(last_turn.get("question", ""))[:90] if isinstance(last_turn, Mapping) else "",
        }

    def list_conversations(self, session_id: str = "default") -> list[dict[str, Any]]:
        """List conversations belonging strictly to this session_id."""
        safe_session = _sanitize_session_id(session_id)
        with self._lock:
            sdir = self._session_dir(safe_session)
            items: list[dict[str, Any]] = []
            for path in sdir.glob("*.json"):
                try:
                    rec = self._read_raw(path)
                except Exception:
                    continue
                if rec.get("session_id") != safe_session:
                    continue
                items.append(self._summarize_conversation(rec))
        items.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
        return items

    def get_conversation(
        self, session_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """Load one full conversation if owned by session_id; otherwise None."""
        safe_session = _sanitize_session_id(session_id)
        path = self._conversation_path(safe_session, conversation_id)
        with self._lock:
            if not path.is_file():
                return None
            rec = self._read_raw(path)
        if rec.get("session_id") != safe_session:
            return None
        return rec

    def get_context_messages(
        self,
        session_id: str,
        conversation_id: str | None,
        *,
        max_turns: int = 8,
        max_chars: int = 6000,
    ) -> list[dict[str, str]]:
        """Extract bounded recent user/assistant message history for multi-turn planning."""
        if not conversation_id:
            return []
        rec = self.get_conversation(session_id, conversation_id)
        if rec is None:
            return []
        turns = list(rec.get("turns", []))[-max_turns:]
        messages: list[dict[str, str]] = []
        used_chars = 0
        for turn in turns:
            q = str(turn.get("question", "")).strip()
            a = str(turn.get("final_response") or turn.get("answer") or "").strip()
            if q:
                entry = {"role": "user", "content": q[:1000]}
                messages.append(entry)
                used_chars += len(entry["content"])
            if a and used_chars < max_chars:
                entry = {"role": "assistant", "content": a[:1200]}
                messages.append(entry)
                used_chars += len(entry["content"])
            if used_chars >= max_chars:
                break
        return messages

    def append_turn(
        self,
        session_id: str,
        conversation_id: str | None,
        turn_record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append an executed query turn, creating a conversation if conversation_id is omitted."""
        safe_session = _sanitize_session_id(session_id)
        with self._lock:
            if not conversation_id:
                conv_id = f"conv-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
                path = self._conversation_path(safe_session, conv_id)
                now = datetime.now(timezone.utc).isoformat()
                question_title = scrub_secrets(str(turn_record.get("question", "Conversation")).strip()[:80])
                rec: dict[str, Any] = {
                    "conversation_id": conv_id,
                    "session_id": safe_session,
                    "title": question_title or "New Conversation",
                    "created_at": now,
                    "updated_at": now,
                    "turns": [],
                }
            else:
                conv_id = conversation_id
                path = self._conversation_path(safe_session, conv_id)
                if not path.is_file():
                    raise KeyError(f"Conversation {conv_id!r} not found in session.")
                rec = self._read_raw(path)
                if rec.get("session_id") != safe_session:
                    raise PermissionError("Conversation belongs to a different session.")

            scrubbed_turn = _scrub_obj(dict(turn_record))
            turns = list(rec.get("turns", []))
            if not turns or rec.get("title") in {"", "New Conversation"}:
                q_title = str(scrubbed_turn.get("question", "")).strip()[:80]
                if q_title:
                    rec["title"] = q_title
            turns.append(scrubbed_turn)
            if len(turns) > self.max_turns_per_conversation:
                turns = turns[-self.max_turns_per_conversation :]
            rec["turns"] = turns
            rec["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_raw(path, rec)
            self._enforce_retention_locked(safe_session)
            return rec

    def clear_conversation(self, session_id: str, conversation_id: str) -> dict[str, Any] | None:
        """Clear turn history inside an existing conversation while keeping the ID."""
        safe_session = _sanitize_session_id(session_id)
        path = self._conversation_path(safe_session, conversation_id)
        with self._lock:
            if not path.is_file():
                return None
            rec = self._read_raw(path)
            if rec.get("session_id") != safe_session:
                return None
            rec["turns"] = []
            rec["title"] = "Cleared Conversation"
            rec["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_raw(path, rec)
            return self._summarize_conversation(rec)

    def delete_conversation(self, session_id: str, conversation_id: str) -> bool:
        """Delete a conversation file only if owned by session_id."""
        safe_session = _sanitize_session_id(session_id)
        path = self._conversation_path(safe_session, conversation_id)
        with self._lock:
            if not path.is_file():
                return False
            try:
                rec = self._read_raw(path)
            except Exception:
                return False
            if rec.get("session_id") != safe_session:
                return False
            path.unlink(missing_ok=True)
            return True

    def list_all_user_turns(self) -> list[dict[str, Any]]:
        """Return sanitized turn telemetry across sessions for population-level analytics."""
        turns: list[dict[str, Any]] = []
        if not self.base_dir.is_dir():
            return turns
        with self._lock:
            for path in self.base_dir.rglob("*.json"):
                if not path.is_file():
                    continue
                try:
                    rec = self._read_raw(path)
                except Exception:
                    continue
                for turn in rec.get("turns", []):
                    if isinstance(turn, Mapping):
                        turns.append(dict(turn))
        return turns


__all__ = ["ConversationStore", "scrub_secrets"]
