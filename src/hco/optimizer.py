"""Первый вертикальный срез deterministic HCO."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MARKER_RE = re.compile(r"<hco source_hash=([a-f0-9]{64}) />")
_COMPACT_BLOCK_RE = re.compile(
    r"<hco-compact source_hash=([a-f0-9]{64})>.*?</hco-compact>",
    re.DOTALL,
)
_INCOMPLETE_MARKER_RE = re.compile(
    r"<hco-incomplete source_hash=([a-f0-9]{64}) reason=([a-z0-9_-]+) />"
)
_PAGINATED_MARKER_RE = re.compile(
    r"<hco-paginated source_hash=([a-f0-9]{64}) />"
)
_UPSTREAM_TRUNCATION_RE = re.compile(
    r"(?i)(?:\.\.\.|…)?\s*\[truncated\]|\[content truncated\]|next_offset\s*[:=]"
)
_READ_FILE_LINE_PREFIX_RE = re.compile(r"(?m)^\d+\|")
_TOKEN_RE = re.compile(r"[\w./:\\+-]+", re.UNICODE)
_IDENTIFIER_NAMESPACES = "SRC|EV|ACCT|AP|LOG|CODE|CFG|DEP|DOC|SEC|MKT|MET|API|DAT|OPS"
_EXPLICIT_IDENTIFIER_RE = re.compile(
    rf"^(?:{_IDENTIFIER_NAMESPACES})-[A-Za-z0-9][A-Za-z0-9_-]{{1,63}}$",
    re.IGNORECASE,
)
_EXPLICIT_IDENTIFIER_SCAN_RE = re.compile(
    rf"(?<![A-Za-z0-9_-])(?:{_IDENTIFIER_NAMESPACES})-[A-Za-z0-9][A-Za-z0-9_-]{{1,63}}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r'''(?ix)
    ["']?(?:api[_-]?key|token|secret|password|authorization)["']?
    \s*[:=]\s*["']
    (?!\s*(?:redacted|masked|none|null|example|placeholder)\s*["'])
    [^"'\r\n]{8,}["']
    |\b(?:sk|pk|ghp|github_pat|xox[baprs]|AKIA)-?[A-Za-z0-9_\-]{12,}\b
    ''',
)
_EMBEDDED_SECRET_RE = re.compile(
    r'''(?ix)
    \b(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]{8,}
    |-----BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE\s+KEY-----
    |[\\"']?(?:password|client[_-]?secret|access[_-]?token|refresh[_-]?token|oauth[_-]?token|session[_-]?id|telegram[_-]?bot[_-]?token|private[_-]?key|secret|api[_-]?key|aws[_-]?secret[_-]?access[_-]?key|x-api-key)[\\"']?
       \s*[:=]\s*[\\"']?[^\s\\"',;}]{8,}
    ''',
)
_SENSITIVE_KEY_RE = re.compile(
    r"(?ix)^(?:.*[_-])?(?:api[_-]?key|password|client[_-]?secret|access[_-]?token|refresh[_-]?token|oauth[_-]?token|session[_-]?id|telegram[_-]?bot[_-]?token|private[_-]?key|secret|key|token)$"
)
_NON_SECRET_VALUES = {"", "redacted", "masked", "none", "null", "example", "placeholder"}
_QUERY_BOILERPLATE_TERMS = {
    "also", "and", "answer", "decision", "evidence", "find", "from",
    "give", "include", "provide", "record", "records", "return", "show",
    "source", "using", "what", "which", "with",
}


def _search_tokens(value: str) -> list[str]:
    """Tokenize prose and split common identifier separators for retrieval."""
    tokens: list[str] = []
    for token in _TOKEN_RE.findall(value):
        folded = token.casefold()
        tokens.append(folded)
        tokens.extend(
            part for part in re.split(r"[_./:\\+-]+", folded)
            if len(part) >= 3 and part != folded
        )
    return tokens


def _explicit_ids_in(value: str) -> set[str]:
    """Return full-token explicit identifiers present in ``value``.

    Uses the boundary-aware scan regex rather than substring search so that
    ``SRC-044`` does not match the identifier ``SRC-0440``.
    """
    return {
        match.group(0).casefold()
        for match in _EXPLICIT_IDENTIFIER_SCAN_RE.finditer(value)
    }


def _validated_compact_hashes(value: str) -> tuple[list[str], bool]:
    """Extract hashes only from canonical, non-nested compact envelopes."""
    blocks = list(_COMPACT_BLOCK_RE.finditer(value))
    hco_looking = any(
        marker in value for marker in ("<hco-compact", "</hco-compact>", "<hco source_hash=")
    )
    if not blocks:
        return [], not hco_looking
    if value.count("<hco-compact") != len(blocks) or value.count("</hco-compact>") != len(blocks):
        return [], False

    hashes: list[str] = []
    remainder_parts: list[str] = []
    cursor = 0
    for block in blocks:
        remainder_parts.append(value[cursor:block.start()])
        text = block.group(0)
        envelope_hash = block.group(1)
        markers = _MARKER_RE.findall(text)
        if (
            text.count("<hco-compact") != 1
            or text.count("</hco-compact>") != 1
            or markers != [envelope_hash]
        ):
            return [], False
        hashes.append(envelope_hash)
        cursor = block.end()
    remainder_parts.append(value[cursor:])
    remainder = "".join(remainder_parts)
    if any(marker in remainder for marker in ("<hco-compact", "</hco-compact>", "<hco source_hash=")):
        return [], False
    return hashes, True


@dataclass(frozen=True)
class CompactResult:
    content: str
    source_hash: str
    changed: bool


@dataclass(frozen=True)
class CoverageReceipt:
    decision: str
    coverage_complete: bool
    source_hashes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PreparedRequest:
    messages: list[dict[str, Any]]
    receipt: CoverageReceipt


class ContextOptimizer:
    """Сохраняет original и детерминированно расширяет request до provider call."""

    def __init__(
        self,
        *,
        store_path: str | Path,
        min_chars: int = 20_000,
        retention_ttl_seconds: int = 86_400,
        retention_max_rows: int = 1_000,
    ) -> None:
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.min_chars = min_chars
        self.retention_ttl_seconds = retention_ttl_seconds
        self.retention_max_rows = retention_max_rows
        self._init_store()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA secure_delete=ON")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _init_store(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sources'"
            ).fetchone()
            if existing:
                columns = connection.execute("PRAGMA table_info(sources)").fetchall()
                primary_key = {
                    str(column[1]): int(column[5]) for column in columns if int(column[5])
                }
                if primary_key != {"session_id": 1, "source_hash": 2}:
                    connection.execute("ALTER TABLE sources RENAME TO sources_legacy_v1")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS sources (
                    source_hash TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    original_content TEXT NOT NULL,
                    fragments_json TEXT NOT NULL,
                    created_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_id, source_hash)
                )"""
            )
            current_columns = {
                str(column[1]) for column in connection.execute(
                    "PRAGMA table_info(sources)"
                ).fetchall()
            }
            if "created_at" not in current_columns:
                connection.execute(
                    "ALTER TABLE sources ADD COLUMN created_at REAL NOT NULL DEFAULT 0"
                )
            legacy = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sources_legacy_v1'"
            ).fetchone()
            if legacy:
                connection.execute(
                    """INSERT OR IGNORE INTO sources
                       (source_hash, session_id, tool_name, tool_call_id, original_content, fragments_json, created_at)
                       SELECT source_hash,
                              CASE WHEN session_id = '' THEN 'legacy-quarantine' ELSE session_id END,
                              tool_name, tool_call_id, original_content, fragments_json, 0
                       FROM sources_legacy_v1"""
                )
                connection.execute("DROP TABLE sources_legacy_v1")
            connection.execute("PRAGMA user_version = 3")
            connection.commit()

    def _apply_retention(self, connection: sqlite3.Connection) -> None:
        cutoff = time.time() - self.retention_ttl_seconds
        connection.execute("DELETE FROM sources WHERE created_at < ?", (cutoff,))
        connection.execute(
            """DELETE FROM sources WHERE rowid IN (
                   SELECT rowid FROM sources
                   ORDER BY created_at DESC, session_id DESC, source_hash DESC
                   LIMIT -1 OFFSET ?
               )""",
            (self.retention_max_rows,),
        )

    def purge_session(self, session_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sources WHERE session_id = ?", (session_id,)
            )
            connection.commit()
            return int(cursor.rowcount)

    def optimize_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        content: str,
        read_only: bool,
        session_id: str,
    ) -> CompactResult:
        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not read_only or self._contains_secret(content):
            return CompactResult(content=content, source_hash=source_hash, changed=False)

        segment_content = content
        paginated = False
        if tool_name == "read_file":
            segment_content, incomplete, paginated = self._unwrap_read_file_result(content)
            if incomplete:
                marker = (
                    f"<hco-incomplete source_hash={source_hash} "
                    "reason=upstream_truncated />"
                )
                return CompactResult(content=marker, source_hash=source_hash, changed=True)
        if len(content) < self.min_chars:
            return CompactResult(content=content, source_hash=source_hash, changed=False)

        parsed, fragment_contents = self._segment(segment_content)
        if not fragment_contents:
            return CompactResult(content=content, source_hash=source_hash, changed=False)

        fragments = [
            {
                "fragment_id": f"row-{index:06d}",
                "position": index,
                "content": row,
            }
            for index, row in enumerate(fragment_contents)
        ]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO sources
                   (source_hash, session_id, tool_name, tool_call_id, original_content, fragments_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id, source_hash) DO UPDATE SET
                       tool_name = excluded.tool_name,
                       tool_call_id = excluded.tool_call_id,
                       original_content = excluded.original_content,
                       fragments_json = excluded.fragments_json,
                       created_at = excluded.created_at""",
                (
                    source_hash,
                    session_id,
                    tool_name,
                    tool_call_id,
                    content,
                    json.dumps(fragments, ensure_ascii=False, separators=(",", ":")),
                    time.time(),
                ),
            )
            self._apply_retention(connection)
            stored = connection.execute(
                "SELECT original_content FROM sources WHERE source_hash = ? AND session_id = ?",
                (source_hash, session_id),
            ).fetchone()
        if stored is None or hashlib.sha256(stored["original_content"].encode("utf-8")).hexdigest() != source_hash:
            return CompactResult(content=content, source_hash=source_hash, changed=False)

        sample_positions = sorted({0, min(1, len(fragment_contents) - 1), max(0, len(fragment_contents) - 2), len(fragment_contents) - 1})
        sample = [fragment_contents[index] for index in sample_positions]
        compact = json.dumps(
            {
                "hco_compact": True,
                "source_hash": source_hash,
                "total_items": len(fragment_contents),
                "sample": sample,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        compact = (
            f"<hco-compact source_hash={source_hash}>"
            f"{compact}\n<hco source_hash={source_hash} />"
            "</hco-compact>"
        )
        if paginated:
            compact += f"\n<hco-paginated source_hash={source_hash} />"
        return CompactResult(content=compact, source_hash=source_hash, changed=True)

    @staticmethod
    def _unwrap_read_file_result(content: str) -> tuple[str, bool, bool]:
        """Expose payload inside a Hermes ``read_file`` result wrapper.

        The renderer can clip the inner ``content`` while the outer
        ``truncated`` flag remains false. Missing bytes are unrecoverable, so
        either signal must produce incomplete coverage instead of a guess.
        """
        try:
            wrapper = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content, bool(_UPSTREAM_TRUNCATION_RE.search(content)), False
        if not isinstance(wrapper, dict) or not isinstance(wrapper.get("content"), str):
            return content, False, False
        inner = str(wrapper["content"])
        paginated = wrapper.get("next_offset") is not None
        # A structured page ending at a line boundary is usable even when more
        # pages remain. Inner renderer clipping is different: bytes inside the
        # current record stream were removed and must fail closed.
        hard_incomplete = bool(_UPSTREAM_TRUNCATION_RE.search(inner))
        metadata_incomplete = bool(wrapper.get("truncated")) and not paginated
        if hard_incomplete or metadata_incomplete:
            return inner, True, False
        return _READ_FILE_LINE_PREFIX_RE.sub("", inner), False, paginated

    @classmethod
    def _contains_secret(cls, content: str) -> bool:
        if _SECRET_VALUE_RE.search(content) or _EMBEDDED_SECRET_RE.search(content):
            return True
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            parsed = content

        def walk(value: Any, key: str = "") -> bool:
            if isinstance(value, dict):
                return any(walk(item, str(name)) for name, item in value.items())
            if isinstance(value, list):
                return any(walk(item, key) for item in value)
            if not isinstance(value, str):
                return False
            stripped = value.strip().strip("\"'").casefold()
            if _EMBEDDED_SECRET_RE.search(value):
                return True
            if _SENSITIVE_KEY_RE.fullmatch(key.strip()):
                if stripped in _NON_SECRET_VALUES:
                    return False
                if stripped.startswith(("never ", "do not ", "don't ", "must not ")):
                    return False
                return len(stripped) >= 8
            return False

        return walk(parsed)

    @staticmethod
    def _segment(content: str) -> tuple[Any, list[Any]]:
        """Return parsed payload plus deterministic searchable fragments."""
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return parsed, parsed
        if isinstance(parsed, dict):
            return parsed, [{"key": key, "value": value} for key, value in parsed.items()]

        lines = [line for line in content.splitlines() if line.strip()]
        if lines:
            try:
                jsonl = [json.loads(line) for line in lines]
            except json.JSONDecodeError:
                jsonl = []
            if jsonl and all(isinstance(item, (dict, list)) for item in jsonl):
                return jsonl, jsonl

        blocks = [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]
        if len(blocks) >= 2:
            return blocks, blocks
        return parsed, []

    def prepare_request(
        self,
        messages: list[dict[str, Any]],
        *,
        session_id: str,
    ) -> PreparedRequest:
        incomplete_hashes: list[str] = []
        paginated_hashes: set[str] = set()
        hashes: list[str] = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                incomplete_hashes.extend(
                    match.group(1) for match in _INCOMPLETE_MARKER_RE.finditer(content)
                )
                paginated_hashes.update(_PAGINATED_MARKER_RE.findall(content))
                message_hashes, valid_envelopes = _validated_compact_hashes(content)
                if not valid_envelopes:
                    return PreparedRequest(
                        messages=messages,
                        receipt=CoverageReceipt("error", False),
                    )
                hashes.extend(message_hashes)
        if incomplete_hashes:
            return PreparedRequest(
                messages=messages,
                receipt=CoverageReceipt(
                    "upstream_incomplete",
                    False,
                    tuple(dict.fromkeys(incomplete_hashes)),
                ),
            )
        if not hashes:
            return PreparedRequest(messages=messages, receipt=CoverageReceipt("passthrough", True))

        query = self._current_query(messages)
        selected_by_hash: dict[str, list[dict[str, Any]]] = {}
        for source_hash in dict.fromkeys(hashes):
            selected, confident = self._select(
                source_hash, session_id=session_id, query=query
            )
            if not selected or not confident:
                if source_hash in paginated_hashes:
                    return PreparedRequest(
                        messages=messages,
                        receipt=CoverageReceipt(
                            "upstream_incomplete", False, (source_hash,)
                        ),
                    )
                return self._full_fallback(messages, hashes, session_id=session_id)
            selected_by_hash[source_hash] = selected

        additions: list[Any] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            found = _MARKER_RE.findall(content)
            if not found:
                continue
            for source_hash in found:
                additions.extend(
                    fragment["content"] for fragment in selected_by_hash[source_hash]
                )
        fragment_block = "<hco-proactive-fragments>\n" + json.dumps(
            additions, ensure_ascii=False, separators=(",", ":")
        ) + "\n</hco-proactive-fragments>"
        expanded = copy.deepcopy(messages)
        if (
            expanded
            and expanded[-1].get("role") == "user"
            and isinstance(expanded[-1].get("content"), str)
        ):
            expanded[-1]["content"] = expanded[-1]["content"] + "\n" + fragment_block
        else:
            expanded.append({"role": "user", "content": fragment_block})
        return PreparedRequest(
            messages=expanded,
            receipt=CoverageReceipt("proactive_expand", True, tuple(dict.fromkeys(hashes))),
        )

    @staticmethod
    def _current_query(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return message["content"]
        return ""

    def _select(
        self, source_hash: str, *, session_id: str, query: str
    ) -> tuple[list[dict[str, Any]], bool]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fragments_json FROM sources WHERE source_hash = ? AND session_id = ?",
                (source_hash, session_id),
            ).fetchone()
            if row is None:
                return [], False
            fragments = json.loads(str(row["fragments_json"]))
        terms = list(dict.fromkeys(term for term in _search_tokens(query) if len(term) >= 3))

        # Explicit IDs in a query are mandatory coverage facets.  A lexical
        # top-score alone can otherwise select SRC-145 while silently dropping
        # SRC-044 and still claim ``coverage_complete=True``.  Resolve every
        # alphanumeric identifier independently; only use selective expansion
        # when each one maps to exactly one fragment.  Missing or ambiguous IDs
        # fall through to conservative full-original fallback.
        explicit_ids = list(dict.fromkeys(
            match.group(0).casefold()
            for match in _EXPLICIT_IDENTIFIER_SCAN_RE.finditer(query)
        ))
        if explicit_ids:
            selected_by_position: dict[int, dict[str, Any]] = {}
            for identifier in explicit_ids:
                matches = [
                    fragment
                    for fragment in fragments
                    if identifier in _explicit_ids_in(
                        json.dumps(fragment["content"], ensure_ascii=False, default=str)
                    )
                ]
                if len(matches) != 1:
                    return [], False
                fragment = matches[0]
                selected_by_position[int(fragment["position"])] = fragment
            selected = [selected_by_position[position] for position in sorted(selected_by_position)]
            if len(selected) > 4:
                return [], False
            query_without_ids = _EXPLICIT_IDENTIFIER_SCAN_RE.sub(" ", query)
            lexical_terms = {
                term for term in _search_tokens(query_without_ids)
                if len(term) >= 3 and term not in _QUERY_BOILERPLATE_TERMS
            }
            corpus_documents = [
                _search_tokens(json.dumps(fragment["content"], ensure_ascii=False, default=str))
                for fragment in fragments
            ]
            rare_limit = max(3, math.ceil(len(fragments) * 0.02))
            meaningful_terms = {
                term for term in lexical_terms
                if 0 < sum(term in document for document in corpus_documents) <= rare_limit
            }
            selected_documents = [
                _search_tokens(json.dumps(fragment["content"], ensure_ascii=False, default=str))
                for fragment in selected
            ]
            selected_matched_terms = {
                term for term in meaningful_terms
                if any(term in document for document in selected_documents)
            }
            uncovered = set(meaningful_terms - selected_matched_terms)
            lexical_positions: set[int] = set()
            while uncovered:
                candidates: list[tuple[int, int, dict[str, Any]]] = []
                for fragment, document in zip(fragments, corpus_documents):
                    position = int(fragment["position"])
                    if position in selected_by_position:
                        continue
                    gain = len(uncovered.intersection(document))
                    if gain:
                        candidates.append((gain, -position, fragment))
                if not candidates or len(selected_by_position) >= 4:
                    return [], False
                _, _, fragment = max(candidates, key=lambda item: (item[0], item[1]))
                position = int(fragment["position"])
                selected_by_position[position] = fragment
                lexical_positions.add(position)
                uncovered.difference_update(corpus_documents[position])

            positions = set(selected_by_position)
            for position in lexical_positions:
                positions.update(
                    candidate for candidate in (position - 1, position + 1)
                    if 0 <= candidate < len(fragments)
                )
            if len(positions) > 8:
                return [], False
            final_documents = [corpus_documents[position] for position in sorted(positions)]
            if any(
                not any(term in document for document in final_documents)
                for term in meaningful_terms
            ):
                return [], False
            return [fragments[position] for position in sorted(positions)], True

        if not terms:
            return [], False
        tokenized = [
            _search_tokens(
                json.dumps(fragment["content"], ensure_ascii=False, default=str)
            )
            for fragment in fragments
        ]
        average_length = sum(map(len, tokenized)) / max(1, len(tokenized))
        document_frequency = {
            term: sum(term in document for document in tokenized) for term in terms
        }
        scored: list[tuple[float, int, dict[str, Any]]] = []
        for fragment, document in zip(fragments, tokenized):
            score = 0.0
            length_normalization = 1 - 0.75 + 0.75 * len(document) / max(1.0, average_length)
            for term in terms:
                frequency = document.count(term)
                if not frequency:
                    continue
                inverse_frequency = math.log(
                    1 + (len(fragments) - document_frequency[term] + 0.5)
                    / (document_frequency[term] + 0.5)
                )
                score += inverse_frequency * (frequency * 2.2) / (
                    frequency + 1.2 * length_normalization
                )
            if score > 0:
                scored.append((score, -int(fragment["position"]), fragment))
        if not scored:
            return [], False
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        matched_terms = {
            term for term in terms if any(term in document for document in tokenized)
        }
        required_matches = min(len(terms), 2)
        if len(matched_terms) < required_matches:
            return [], False
        best_score = scored[0][0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        if second_score and best_score / second_score < 1.2:
            return [], False
        primary = [
            fragment for score, _, fragment in scored
            if score >= best_score * 0.75
        ][:4]
        positions = {int(fragment["position"]) for fragment in primary}
        for position in tuple(positions):
            positions.update(candidate for candidate in (position - 1, position + 1)
                             if 0 <= candidate < len(fragments))
        if len(positions) > 8:
            return [], False
        selected = [fragments[position] for position in sorted(positions)]
        selected_documents = [tokenized[position] for position in sorted(positions)]
        selected_terms = {
            term for term in terms
            if any(term in document for document in selected_documents)
        }
        if len(selected_terms) < required_matches:
            return [], False
        return selected, True

    def _full_fallback(
        self,
        messages: list[dict[str, Any]],
        hashes: list[str],
        *,
        session_id: str,
    ) -> PreparedRequest:
        expanded = copy.deepcopy(messages)
        originals: dict[str, str] = {}
        with self._connect() as connection:
            for source_hash in dict.fromkeys(hashes):
                row = connection.execute(
                    "SELECT original_content FROM sources WHERE source_hash = ? AND session_id = ?",
                    (source_hash, session_id),
                ).fetchone()
                if row is None:
                    return PreparedRequest(messages=messages, receipt=CoverageReceipt("error", False))
                originals[source_hash] = row["original_content"]
        for message in expanded:
            content = message.get("content")
            if not isinstance(content, str):
                continue
            def expand_block(match: re.Match[str]) -> str:
                source_hash = match.group(1)
                return originals.get(source_hash, match.group(0))

            message["content"] = _COMPACT_BLOCK_RE.sub(expand_block, content)
        combined = "\n".join(
            str(message.get("content", ""))
            for message in expanded
            if isinstance(message.get("content"), str)
        )
        for source_hash, original in originals.items():
            if original not in combined:
                return PreparedRequest(messages=messages, receipt=CoverageReceipt("error", False))
        if any(
            isinstance(message.get("content"), str)
            and _COMPACT_BLOCK_RE.search(message["content"])
            for message in expanded
        ):
            return PreparedRequest(messages=messages, receipt=CoverageReceipt("error", False))
        return PreparedRequest(
            messages=expanded,
            receipt=CoverageReceipt("full_fallback", True, tuple(dict.fromkeys(hashes))),
        )
