"""Dataset file parsing, column mapping and validation (spec §10.2 Datasets: mapping step + validation report).

Pure functions, no DB: parse bytes -> rows -> normalized cases + a row-numbered report.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 5000
MAX_TEXT = 20000
MAX_CHUNKS = 50
MAX_TAGS = 20
MAX_REPORTED_ISSUES = 200

# Mapping targets. `input` is required; the rest optional.
TARGETS = ("input", "expected_output", "response", "context", "criteria", "tags")
TARGET_LABELS = {
    "input": "Question / input",
    "expected_output": "Reference answer",
    "response": "Recorded model response",
    "context": "Context",
    "criteria": "Evaluation criteria",
    "tags": "Tags",
}
_SUGGEST = {
    "input": ("input", "question", "query", "prompt", "user_input", "q"),
    "expected_output": ("expected_output", "reference", "expected", "ground_truth", "reference_answer", "gold"),
    "response": ("response", "actual_output", "output", "answer", "model_output", "model_response", "completion"),
    "context": ("context", "contexts", "retrieved_context", "retrieved_contexts", "documents", "passages"),
    "criteria": ("criteria", "rubric", "evaluation_criteria"),
    "tags": ("tags", "labels", "category", "categories"),
}


class ParseError(ValueError):
    """The file as a whole cannot be read (wrong format, too big, no header, ...)."""


@dataclass
class Issue:
    row: int
    column: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"row": self.row, "column": self.column, "message": self.message}


@dataclass
class NormalizedCase:
    row: int
    input: str
    expected_output: str | None = None
    response: str | None = None
    context: list[str] | None = None
    criteria: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedFile:
    columns: list[str]
    rows: list[tuple[int, dict[str, Any]]]  # (row number as the user sees it, raw record)


@dataclass
class Report:
    total_rows: int
    valid: list[NormalizedCase]
    errors: list[Issue]
    warnings: list[Issue]

    @property
    def invalid_rows(self) -> int:
        return len({e.row for e in self.errors})

    def fields_present(self) -> list[str]:
        present = set()
        for c in self.valid:
            if c.expected_output:
                present.add("reference")
            if c.response:
                present.add("response")
            if c.context:
                present.add("context")
            if c.criteria:
                present.add("criteria")
        return sorted(present)

    def as_dict(self, preview: int = 5) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "valid_rows": len(self.valid),
            "invalid_rows": self.invalid_rows,
            "errors": [e.as_dict() for e in self.errors[:MAX_REPORTED_ISSUES]],
            "warnings": [w.as_dict() for w in self.warnings[:MAX_REPORTED_ISSUES]],
            "truncated": len(self.errors) > MAX_REPORTED_ISSUES or len(self.warnings) > MAX_REPORTED_ISSUES,
            "fields_present": self.fields_present(),
            "preview": [
                {
                    "row": c.row, "input": c.input, "expected_output": c.expected_output, "response": c.response,
                    "context": c.context, "criteria": c.criteria, "tags": c.tags,
                }
                for c in self.valid[:preview]
            ],
        }


# ------------------------------------------------------------------ parsing
def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ParseError("File is not UTF-8 text. Export it as UTF-8 CSV or JSON.") from e


def parse_file(filename: str, data: bytes) -> ParsedFile:
    if len(data) > MAX_BYTES:
        raise ParseError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    if not data.strip():
        raise ParseError("File is empty.")
    name = filename.lower()
    text = _decode(data)
    if name.endswith(".csv") or name.endswith(".tsv"):
        return _parse_csv(text, "\t" if name.endswith(".tsv") else None)
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        return _parse_jsonl(text)
    if name.endswith(".json"):
        return _parse_json(text)
    raise ParseError("Unsupported file type. Upload .csv, .tsv, .json or .jsonl.")


def _parse_csv(text: str, delimiter: str | None) -> ParsedFile:
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames or not any(h and h.strip() for h in reader.fieldnames):
        raise ParseError("CSV has no header row.")
    columns = [h.strip() for h in reader.fieldnames if h is not None]
    if len(set(columns)) != len(columns):
        raise ParseError("CSV header has duplicate column names.")
    rows = []
    for i, rec in enumerate(reader):
        if len(rows) >= MAX_ROWS:
            raise ParseError(f"More than {MAX_ROWS} rows. Split the file.")
        clean = {(k or "").strip(): v for k, v in rec.items() if k is not None}
        rows.append((i + 2, clean))  # header is row 1
    return ParsedFile(columns, rows)


def _records_to_parsed(records: list[Any]) -> ParsedFile:
    if len(records) > MAX_ROWS:
        raise ParseError(f"More than {MAX_ROWS} rows. Split the file.")
    columns: list[str] = []
    rows = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ParseError(f"Item {i + 1} is not an object.")
        for k in rec:
            if k not in columns:
                columns.append(str(k))
        rows.append((i + 1, rec))
    if not columns:
        raise ParseError("No fields found.")
    return ParsedFile(columns, rows)


def _parse_json(text: str) -> ParsedFile:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ParseError(f"Invalid JSON at line {e.lineno}: {e.msg}") from e
    if isinstance(data, dict):
        for key in ("rows", "data", "cases", "items", "examples"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ParseError("JSON must be a list of objects (or an object with a 'rows' list).")
    return _records_to_parsed(data)


def _parse_jsonl(text: str) -> ParsedFile:
    records = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON on line {n}: {e.msg}") from e
    return _records_to_parsed(records)


# ------------------------------------------------------------------ mapping
def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "_", name.strip().lower()).strip("_")


def suggest_mapping(columns: list[str]) -> dict[str, str | None]:
    by_norm = {_norm(c): c for c in columns}
    taken: set[str] = set()
    out: dict[str, str | None] = {}
    for target in TARGETS:
        hit = next((by_norm[s] for s in _SUGGEST[target] if s in by_norm and by_norm[s] not in taken), None)
        out[target] = hit
        if hit:
            taken.add(hit)
    return out


def validate_mapping(mapping: dict[str, str | None], columns: list[str], metadata_columns: list[str]) -> list[str]:
    problems = []
    unknown = [t for t in mapping if t not in TARGETS]
    if unknown:
        problems.append(f"Unknown mapping target(s): {', '.join(unknown)}")
    if not mapping.get("input"):
        problems.append("Map a column to 'Question / input' (required).")
    used = [c for c in mapping.values() if c]
    for c in used + metadata_columns:
        if c not in columns:
            problems.append(f"Column '{c}' is not in the file.")
    if len(used) != len(set(used)):
        problems.append("Each column can be mapped to one field only.")
    if set(used) & set(metadata_columns):
        problems.append("A column cannot be both mapped and kept as metadata.")
    return problems


# ------------------------------------------------------------------ normalization
def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    s = str(v).strip()
    return s or None


def parse_context(v: Any) -> list[str] | None:
    """List -> chunks; JSON-array string -> chunks; otherwise split on blank lines (same as Studio)."""
    if v is None:
        return None
    if isinstance(v, list):
        chunks = [str(x).strip() for x in v if x is not None and str(x).strip()]
        return chunks or None
    s = str(v).strip()
    if not s:
        return None
    if s.startswith("["):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return parse_context(arr)
        except json.JSONDecodeError:
            pass
    chunks = [c.strip() for c in re.split(r"\n\s*\n", s) if c.strip()]
    return chunks or None


def parse_tags(v: Any) -> list[str]:
    if v is None:
        return []
    items = v if isinstance(v, list) else re.split(r"[,;|]", str(v))
    seen: list[str] = []
    for t in items:
        t = str(t).strip()
        if t and t not in seen:
            seen.append(t)
    return seen


def normalize(parsed: ParsedFile, mapping: dict[str, str | None], metadata_columns: list[str]) -> Report:
    errors: list[Issue] = []
    warnings: list[Issue] = []
    valid: list[NormalizedCase] = []
    seen_inputs: dict[str, int] = {}

    def col(target: str) -> str | None:
        return mapping.get(target) or None

    for rownum, rec in parsed.rows:
        if all(_text(v) is None for v in rec.values()):
            warnings.append(Issue(rownum, None, "Empty row skipped"))
            continue
        row_errors: list[Issue] = []
        inp = _text(rec.get(col("input"))) if col("input") else None
        if not inp:
            row_errors.append(Issue(rownum, col("input"), "Question / input is empty"))
        elif len(inp) > MAX_TEXT:
            row_errors.append(Issue(rownum, col("input"), f"Input is longer than {MAX_TEXT:,} characters"))

        fields: dict[str, Any] = {}
        for target in ("expected_output", "response", "criteria"):
            c = col(target)
            val = _text(rec.get(c)) if c else None
            if val and len(val) > (5000 if target == "criteria" else MAX_TEXT):
                row_errors.append(Issue(rownum, c, f"{TARGET_LABELS[target]} is too long"))
            fields[target] = val

        ctx = parse_context(rec.get(col("context"))) if col("context") else None
        if ctx:
            if len(ctx) > MAX_CHUNKS:
                row_errors.append(Issue(rownum, col("context"), f"More than {MAX_CHUNKS} context chunks"))
            if any(len(c) > MAX_TEXT for c in ctx):
                row_errors.append(Issue(rownum, col("context"), f"A context chunk is longer than {MAX_TEXT:,} characters"))

        tags = parse_tags(rec.get(col("tags"))) if col("tags") else []
        if len(tags) > MAX_TAGS or any(len(t) > 50 for t in tags):
            row_errors.append(Issue(rownum, col("tags"), f"At most {MAX_TAGS} tags of up to 50 characters"))

        if row_errors:
            errors.extend(row_errors)
            continue
        assert inp is not None
        if inp in seen_inputs:
            warnings.append(Issue(rownum, col("input"), f"Same input as row {seen_inputs[inp]}"))
        else:
            seen_inputs[inp] = rownum
        meta = {m: rec.get(m) for m in metadata_columns if rec.get(m) not in (None, "")}
        valid.append(NormalizedCase(rownum, inp, context=ctx, tags=tags, metadata=meta, **fields))

    return Report(total_rows=len(parsed.rows), valid=valid, errors=errors, warnings=warnings)


# ------------------------------------------------------------------ export safety
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> Any:
    """Neutralize spreadsheet formula injection (spec §11) by prefixing a quote."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value
