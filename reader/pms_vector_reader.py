#!/usr/bin/env python3
"""
PMS-VECTOR Reader — executable presentation and repository-audit layer.

A single-file, dependency-free desktop reader for the PMS-VECTOR repository.

Core features:
- Loads a PMS-VECTOR repository from a folder or .zip file.
- Navigates Markdown, YAML, HTML, JSON, CSV, and reader source files.
- Renders local Markdown images directly in the main reader area.
- Provides corpus-wide full-text search and heading navigation.
- Parses the 23 Appendix-E case fixtures into lightweight case summaries.
- Includes an interactive Graph Lab with five VECTOR-specific views:
  * Architecture & Status,
  * Dependency / Warrant Graph,
  * Case Pressure Map,
  * Selected Case Trace,
  * Reduction Graph.

The graph layer visualizes only relations declared in repository artifacts. It
does not create theory, evidence, classifications, dependencies, warrant,
authority, or geometry. VECTOR is not visualized as a vector space; ROT is not
visualized as geometric rotation; DIST is not visualized as metric distance.

Run:
    python pms_vector_reader.py
    python pms_vector_reader.py /path/to/18. PMS-VECTOR
    python pms_vector_reader.py /path/to/PMS-VECTOR.zip
    python pms_vector_reader.py --self-test /path/to/PMS-VECTOR.zip

Tkinter is part of Python's standard library, but some Linux distributions
package it separately as ``python3-tk``.
"""

from __future__ import annotations

import ast
import base64
import bisect
import csv
import json
import math
import posixpath
import queue
import re
import sys
import threading
import time
import webbrowser
import warnings
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlparse

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    import tkinter.font as tkfont
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Tkinter is not available. Install the Tk bindings for your Python "
        "distribution, for example: sudo apt install python3-tk"
    ) from exc

APP_TITLE = "PMS-VECTOR Reader"
APP_VERSION = "0.9.5-cases-popup-direct"

DEBUG = True  # set False to silence console output


def dbg(msg: str) -> None:
    if DEBUG:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)


SECTION_ORDER: List[str] = [
    "README.md",
    "PMS-VECTOR.md",
    "model",
    "cases",
    "reference",
    "examples",
]

SECTION_LABELS: Dict[str, str] = {
    "README.md": "Start",
    "PMS-VECTOR.md": "Paper",
    "model": "Model",
    "cases": "Cases",
    "reference": "Reference",
    "examples": "Examples",
}

CANONICAL_BLOCK_LABELS: Dict[str, str] = {
    "README.md": "README",
    "PMS-VECTOR.md": "PMS-VECTOR — Paper",
    "model/PMS-VECTOR.yaml": "PMS-VECTOR — Machine Model",
    "model/Model Specification.html": "Model Specification",
    "model/VECTOR-Record.template.yaml": "VECTOR Record Template",
    "model/Case.template.yaml": "Case Template",
    "cases/README.md": "Case Suite — README",
    "cases/index.yaml": "Appendix E — Case Index",
    "reference/Claim Provenance.md": "Claim Provenance",
    "reference/Claim Provenance.yaml": "Claim Provenance — YAML",
    "reference/Dependency Map.md": "Dependency Map",
    "reference/Dependency Map.yaml": "Dependency Map — YAML",
    "reader/README.md": "Reader — README",
    "reader/pms_vector_reader.py": "PMS-VECTOR Reader",
}

ACTIVE_TEXT_EXTENSIONS = {".md", ".yaml", ".yml", ".json", ".csv", ".txt", ".py", ".html", ".htm"}
EXCLUDED_TOP_LEVEL = {"_workfiles", ".git", "__pycache__"}
EXCLUDED_PATH_PREFIXES: set[str] = set()

PREFERRED_HOME_FILES = [
    "README.md",
    "PMS-VECTOR.md",
    "model/Model Specification.html",
    "cases/README.md",
]

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)\s*([A-Za-z0-9_-]+)?(?:\s+.*)?\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+])\s+(.+?)\s*$")
ORDERED_LIST_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)
YAML_KEY_RE = re.compile(r"^(\s*)([^#\s][^:]*?):(?:\s*(.*))?$")
YAML_OUTLINE_KEY_RE = re.compile(r"^(\s*)(-\s+)?([A-Za-z0-9_.-]+)\s*:\s*(.*?)\s*$")
HTML_ANCHOR_RE = re.compile(
    r'^\s*<a\s+(?:name|id)=["\']([^"\']+)["\']\s*></a>\s*$',
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

LARGE_DOC_LINE_THRESHOLD = 8000
CHUNKED_RENDER_LINE_THRESHOLD = 10_000
CHUNKED_RENDER_BYTE_THRESHOLD = 1_048_576
CHUNK_TARGET_BYTES = 192 * 1024
MAX_SEARCH_HIGHLIGHTS = 2_000
IMAGE_MAX_DISPLAY_WIDTH = 1_200

YAML_OUTLINE_LEVEL3_KEYS = {
    "case_id", "paper_ref", "title", "case_format", "load_bearing",
    "pressure_families", "constructs_under_test", "theoretical_function",
    "architecture_effect", "baseline_claim", "relation", "result", "status",
    "surviving_claim", "scope", "stop_reason", "dependent", "dependency",
    "relation", "target", "current_status", "previous_candidate_status",
}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line_number: int
    anchor: str


@dataclass
class Document:
    rel_path: str
    title: str
    text: str
    file_type: str
    headings: List[Heading] = field(default_factory=list)
    frontmatter: Dict[str, str] = field(default_factory=dict)
    _line_count: int = field(init=False, repr=False)
    _word_count: int = field(init=False, repr=False)
    _byte_count: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._line_count = self.text.count("\n") + (
            1 if self.text and not self.text.endswith("\n") else 0
        )
        self._word_count = len(WORD_RE.findall(self.text))
        self._byte_count = len(self.text.encode("utf-8", errors="replace"))

    @property
    def line_count(self) -> int:
        return self._line_count

    @property
    def word_count(self) -> int:
        return self._word_count

    @property
    def byte_count(self) -> int:
        return self._byte_count


@dataclass(frozen=True)
class RenderChunk:
    text: str
    start_line: int


@dataclass
class EmbeddedImage:
    """One local Markdown image embedded in the main reader surface."""

    rel_path: str
    alt_text: str
    encoded_data: str
    source_width: int
    source_height: int
    frame: tk.Frame
    image_label: tk.Label
    caption_label: Optional[tk.Label]
    displayed_image: Optional[tk.PhotoImage] = None
    scale_factor: int = 0


@dataclass
class CaseSummary:
    case_id: str
    title: str
    paper_ref: str
    yaml_path: str
    markdown_path: str = ""
    case_format: str = ""
    adversarial: bool = False
    paper_label: str = ""
    load_bearing: bool = False
    pressure_families: List[str] = field(default_factory=list)
    constructs_under_test: List[str] = field(default_factory=list)
    theoretical_function: str = ""
    result_status: str = ""
    baseline_claim: str = ""
    dir_relation: str = ""
    scope: str = ""
    surviving_claim: str = ""
    stop_reason: str = ""
    architecture_applies: bool = False
    architecture_targets: List[str] = field(default_factory=list)
    architecture_effect: str = ""
    non_implications: List[str] = field(default_factory=list)
    raw: Dict[str, object] = field(default_factory=dict, repr=False)


@dataclass
class GraphNode:
    node_id: str
    label: str
    kind: str
    x: float
    y: float
    z: float
    rel_path: str = ""
    details: str = ""
    record_id: str = ""


class CorpusError(RuntimeError):
    """Raised when a PMS-VECTOR corpus cannot be loaded."""


# ---------------------------------------------------------------------------
# Corpus source (folder or zip)
# ---------------------------------------------------------------------------

class CorpusSource:
    """Reads active PMS-VECTOR text artifacts from a folder or a zip file."""

    def __init__(self, source_path: Path):
        self.source_path = source_path.expanduser().resolve()
        dbg(f"CorpusSource: resolving {self.source_path}")
        self.kind: str = "folder" if self.source_path.is_dir() else "zip"
        self._zip: Optional[zipfile.ZipFile] = None
        self._zip_prefix = ""
        self.root_dir: Optional[Path] = None

        if self.source_path.is_dir():
            self.root_dir = self._detect_folder_root(self.source_path)
            dbg(f"CorpusSource: folder root = {self.root_dir}")
        elif self.source_path.is_file() and self.source_path.suffix.lower() == ".zip":
            self._zip = zipfile.ZipFile(self.source_path)
            self._zip_prefix = self._detect_zip_prefix(self._zip)
            dbg(f"CorpusSource: zip prefix = '{self._zip_prefix}'")
        else:
            raise CorpusError(f"Unsupported source: {self.source_path}")

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def describe(self) -> str:
        if self.kind == "folder" and self.root_dir is not None:
            return str(self.root_dir)
        return str(self.source_path)

    @staticmethod
    def _active_rel_path(rel_path: str) -> bool:
        rel_path = normalize_rel_path(rel_path)
        if not rel_path or rel_path.endswith("/"):
            return False
        first = rel_path.split("/", 1)[0]
        if first in EXCLUDED_TOP_LEVEL:
            return False
        if any(rel_path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
            return False
        return Path(rel_path).suffix.lower() in ACTIVE_TEXT_EXTENSIONS

    def exists(self, rel_path: str) -> bool:
        rel_path = normalize_rel_path(rel_path)
        if not self._active_rel_path(rel_path):
            return False
        if self.kind == "folder":
            assert self.root_dir is not None
            return (self.root_dir / rel_path).is_file()
        assert self._zip is not None
        return self._zip_name(rel_path) in self._zip.namelist()

    def read_text(self, rel_path: str) -> str:
        rel_path = normalize_rel_path(rel_path)
        if self.kind == "folder":
            assert self.root_dir is not None
            return (self.root_dir / rel_path).read_text(encoding="utf-8", errors="replace")
        assert self._zip is not None
        with self._zip.open(self._zip_name(rel_path), "r") as handle:
            raw = handle.read()
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _safe_asset_rel_path(rel_path: str) -> Optional[str]:
        normalized = normalize_rel_path(posixpath.normpath(unquote(rel_path)))
        if not normalized or normalized in {".", ".."} or normalized.startswith("../"):
            return None
        first = normalized.split("/", 1)[0]
        if first in EXCLUDED_TOP_LEVEL:
            return None
        if any(normalized.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES):
            return None
        return normalized

    def asset_exists(self, rel_path: str) -> bool:
        """Return whether a non-text repository asset exists inside the active root."""
        safe_path = self._safe_asset_rel_path(rel_path)
        if safe_path is None:
            return False
        if self.kind == "folder":
            assert self.root_dir is not None
            candidate = (self.root_dir / safe_path).resolve()
            try:
                candidate.relative_to(self.root_dir.resolve())
            except ValueError:
                return False
            return candidate.is_file()
        assert self._zip is not None
        return self._zip_name(safe_path) in self._zip.namelist()

    def read_bytes(self, rel_path: str) -> bytes:
        """Read a repository asset without admitting it to the active text corpus."""
        safe_path = self._safe_asset_rel_path(rel_path)
        if safe_path is None:
            raise CorpusError(f"Unsafe repository asset path: {rel_path}")
        if self.kind == "folder":
            assert self.root_dir is not None
            candidate = (self.root_dir / safe_path).resolve()
            try:
                candidate.relative_to(self.root_dir.resolve())
            except ValueError as exc:
                raise CorpusError(f"Repository asset leaves the active root: {rel_path}") from exc
            return candidate.read_bytes()
        assert self._zip is not None
        with self._zip.open(self._zip_name(safe_path), "r") as handle:
            return handle.read()

    def available_files(self) -> List[str]:
        if self.kind == "folder":
            assert self.root_dir is not None
            paths = [
                path.relative_to(self.root_dir).as_posix()
                for path in self.root_dir.rglob("*")
                if path.is_file()
            ]
        else:
            assert self._zip is not None
            paths = []
            for name in self._zip.namelist():
                if self._zip_prefix and not name.startswith(self._zip_prefix):
                    continue
                rel = name[len(self._zip_prefix):] if self._zip_prefix else name
                paths.append(rel)

        active = [normalize_rel_path(p) for p in paths if self._active_rel_path(p)]
        result = sorted(set(active), key=corpus_sort_key)
        dbg(f"CorpusSource.available_files: {len(result)} active text artifacts found")
        return result

    def _zip_name(self, rel_path: str) -> str:
        rel_path = normalize_rel_path(rel_path)
        return f"{self._zip_prefix}{rel_path}" if self._zip_prefix else rel_path

    @staticmethod
    def _looks_like_root(candidate: Path) -> bool:
        return (
            (candidate / "PMS-VECTOR.md").is_file()
            and (candidate / "model" / "PMS-VECTOR.yaml").is_file()
            and (candidate / "cases" / "index.yaml").is_file()
            and (candidate / "reference" / "Dependency Map.yaml").is_file()
        )

    @classmethod
    def _detect_folder_root(cls, path: Path) -> Path:
        candidates = [
            path,
            path / "18. PMS-VECTOR",
            path / "PMS-VECTOR",
        ]
        try:
            candidates.extend(child for child in path.iterdir() if child.is_dir())
        except OSError:
            pass

        seen = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            valid = cls._looks_like_root(resolved)
            dbg(f"  _detect_folder_root: {resolved} valid={valid}")
            if valid:
                return resolved
        raise CorpusError(
            "Could not find a PMS-VECTOR project root. Select the folder that "
            "contains PMS-VECTOR.md, model/PMS-VECTOR.yaml, cases/index.yaml, "
            "and reference/Dependency Map.yaml."
        )

    @staticmethod
    def _detect_zip_prefix(zf: zipfile.ZipFile) -> str:
        names = zf.namelist()
        candidates = [name for name in names if name.endswith("PMS-VECTOR.md")]
        for paper in sorted(candidates, key=lambda value: value.count("/")):
            prefix = paper[:-len("PMS-VECTOR.md")]
            required = [
                prefix + "model/PMS-VECTOR.yaml",
                prefix + "cases/index.yaml",
                prefix + "reference/Dependency Map.yaml",
            ]
            if all(item in names for item in required):
                return prefix
        raise CorpusError(
            "Could not find a PMS-VECTOR project root inside the zip file. "
            "Expected PMS-VECTOR.md, model/PMS-VECTOR.yaml, cases/index.yaml, "
            "and reference/Dependency Map.yaml."
        )



# ---------------------------------------------------------------------------
# Corpus (collection of loaded documents)
# ---------------------------------------------------------------------------

class Corpus:
    """Loaded active PMS-VECTOR artifacts plus case and graph helpers."""

    def __init__(self, source: CorpusSource):
        self.source = source
        self.documents: Dict[str, Document] = {}
        self.ordered_paths: List[str] = []
        self.cases: List[CaseSummary] = []
        self.case_by_yaml: Dict[str, CaseSummary] = {}
        self.case_by_markdown: Dict[str, CaseSummary] = {}
        self.case_by_id: Dict[str, CaseSummary] = {}
        self.case_index_data: Dict[str, object] = {}
        self.model_data: Dict[str, object] = {}
        self.dependency_data: Dict[str, object] = {}
        self.provenance_data: Dict[str, object] = {}
        self.load()

    def load(self) -> None:
        self.documents.clear()
        self.cases.clear()
        self.case_by_yaml.clear()
        self.case_by_markdown.clear()
        self.case_by_id.clear()
        self.ordered_paths = self.source.available_files()
        dbg(f"Corpus.load: loading {len(self.ordered_paths)} artifacts ...")

        for rel_path in self.ordered_paths:
            raw_text = self.source.read_text(rel_path)
            suffix = Path(rel_path).suffix.lower().lstrip(".") or "text"
            if suffix == "md":
                frontmatter, body = parse_frontmatter(raw_text)
                headings = parse_headings(body)
                title = frontmatter.get("title") or first_heading_title(headings) or prettify_file_name(rel_path)
                text = raw_text
                file_type = "md"
            elif suffix in {"html", "htm"}:
                frontmatter = {}
                text, html_title = html_to_markdownish(raw_text)
                headings = parse_headings(text)
                title = html_title or first_heading_title(headings) or prettify_file_name(rel_path)
                file_type = "md"
            elif suffix in {"yaml", "yml"}:
                frontmatter = {}
                text = raw_text
                headings = parse_yaml_outline(text)
                title = prettify_file_name(rel_path)
                file_type = suffix
            else:
                frontmatter = {}
                text = raw_text
                headings = []
                title = prettify_file_name(rel_path)
                file_type = suffix
            self.documents[rel_path] = Document(
                rel_path=rel_path, title=title, text=text, file_type=file_type,
                headings=headings, frontmatter=frontmatter,
            )

        self._load_structured_data()
        self._load_case_summaries()
        dbg(f"Corpus.load: done — {len(self.documents)} artifacts, {len(self.cases)} Appendix-E cases")

    def _read_yaml_data(self, rel_path: str) -> Dict[str, object]:
        if rel_path not in self.documents:
            return {}
        try:
            value = parse_simple_yaml(self.documents[rel_path].text)
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            dbg(f"structured YAML parse failed for {rel_path}: {exc}")
            return {}

    def _load_structured_data(self) -> None:
        self.case_index_data = self._read_yaml_data("cases/index.yaml")
        self.model_data = self._read_yaml_data("model/PMS-VECTOR.yaml")
        self.dependency_data = self._read_yaml_data("reference/Dependency Map.yaml")
        self.provenance_data = self._read_yaml_data("reference/Claim Provenance.yaml")

    def _load_case_summaries(self) -> None:
        index_root = as_dict(self.case_index_data.get("case_index"))
        index_cases = as_list(index_root.get("cases"))
        index_by_id = {
            str(as_dict(item).get("case_id", "")): as_dict(item)
            for item in index_cases if isinstance(item, dict)
        }
        for case_id in sorted(index_by_id, key=natural_sort_key):
            meta = index_by_id[case_id]
            pair = as_dict(meta.get("artifact_pair"))
            yaml_path = str(pair.get("yaml") or f"cases/{case_id}.yaml")
            markdown_path = str(pair.get("markdown") or f"cases/{case_id}.md")
            if yaml_path not in self.documents:
                continue
            data = self._read_yaml_data(yaml_path)
            case_meta = as_dict(data.get("case_metadata"))
            record = as_dict(data.get("vector_record"))
            result = as_dict(record.get("result"))
            dir_block = as_dict(record.get("dir"))
            architecture = as_dict(case_meta.get("architecture_effect"))
            adversarial = as_dict(case_meta.get("adversarial"))
            summary = CaseSummary(
                case_id=case_id,
                title=str(case_meta.get("title") or meta.get("title") or case_id),
                paper_ref=str(case_meta.get("paper_ref") or meta.get("paper_ref") or ""),
                yaml_path=yaml_path,
                markdown_path=markdown_path if markdown_path in self.documents else "",
                case_format=str(case_meta.get("case_format") or meta.get("case_format") or ""),
                adversarial=bool(adversarial.get("marked", as_dict(meta.get("adversarial")).get("marked", False))),
                paper_label=str(adversarial.get("paper_label") or ""),
                load_bearing=bool(case_meta.get("load_bearing", meta.get("load_bearing", False))),
                pressure_families=[str(x) for x in as_list(case_meta.get("pressure_families") or meta.get("pressure_families"))],
                constructs_under_test=[str(x) for x in as_list(case_meta.get("constructs_under_test") or meta.get("constructs_under_test"))],
                theoretical_function=str(case_meta.get("theoretical_function") or meta.get("theoretical_function") or ""),
                result_status=str(result.get("status") or ""),
                baseline_claim=scalar_text(record.get("baseline_claim")),
                dir_relation=scalar_text(dir_block.get("relation")),
                scope=scalar_text(result.get("scope")),
                surviving_claim=scalar_text(result.get("surviving_claim")),
                stop_reason=scalar_text(result.get("stop_reason")),
                architecture_applies=bool(architecture.get("applies", False)),
                architecture_targets=[str(x) for x in as_list(architecture.get("targets"))],
                architecture_effect=scalar_text(architecture.get("effect")),
                non_implications=[scalar_text(x) for x in as_list(case_meta.get("non_implications"))],
                raw=data,
            )
            self.cases.append(summary)
            self.case_by_yaml[yaml_path] = summary
            if summary.markdown_path:
                self.case_by_markdown[summary.markdown_path] = summary
            self.case_by_id[case_id] = summary

    def get(self, rel_path: str) -> Document:
        return self.documents[rel_path]

    def search(self, query: str, limit: int = 500) -> List[Tuple[str, int, str]]:
        query_norm = query.strip().lower()
        if not query_norm:
            return []
        results: List[Tuple[str, int, str]] = []
        for rel_path in self.ordered_paths:
            doc = self.documents[rel_path]
            for line_no, line in enumerate(strip_frontmatter(doc.text).splitlines(), start=1):
                if query_norm in line.lower():
                    snippet = line.strip() or "<blank line>"
                    results.append((rel_path, line_no, snippet[:300]))
                    if len(results) >= limit:
                        return results
        return results

    def case_for_path(self, rel_path: Optional[str]) -> Optional[CaseSummary]:
        if not rel_path:
            return None
        return self.case_by_yaml.get(rel_path) or self.case_by_markdown.get(rel_path)

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def total_word_count(self) -> int:
        return sum(doc.word_count for doc in self.documents.values())

    @property
    def total_line_count(self) -> int:
        return sum(doc.line_count for doc in self.documents.values())


class AutoHideScrollbar(ttk.Scrollbar):
    """A grid-managed scrollbar that disappears when the full range is visible."""

    def __init__(self, master: tk.Misc, **kwargs):
        super().__init__(master, **kwargs)
        self.visibility_callback = None
        self._is_visible: Optional[bool] = None

    def set(self, first: str, last: str) -> None:
        try:
            fully_visible = float(first) <= 0.0 and float(last) >= 0.999999
        except (TypeError, ValueError):
            fully_visible = False
        visible = not fully_visible
        if visible:
            self.grid()
        else:
            self.grid_remove()
        super().set(first, last)
        if visible != self._is_visible:
            self._is_visible = visible
            callback = self.visibility_callback
            if callback is not None:
                self.after_idle(lambda: callback(visible))


class BrowseFilesDialog(tk.Toplevel):
    """Stable case browser for the Graph Lab.

    E01–E23 are always present and selectable, independent of the active graph
    view. Current-view graph nodes are optional secondary navigation only.
    """

    def __init__(self, graph_lab: "GraphLab"):
        super().__init__(graph_lab)
        self.graph_lab = graph_lab
        self.title(f"{APP_TITLE} {APP_VERSION} — Browse Cases E01–E23")
        self.geometry("1120x720")
        self.minsize(820, 520)
        self.transient(graph_lab)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        self.query_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.show_view_nodes_var = tk.BooleanVar(value=False)
        self._row_target: Dict[str, Tuple[str, str]] = {}

        self._build_ui()
        self.apply_theme()
        self.refresh_from_graph()
        self.after_idle(self._center_over_parent)

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(10, 10, 10, 6))
        top.pack(fill=tk.X)
        ttk.Label(top, text="Search E01–E23").pack(side=tk.LEFT)
        self.search_entry = ttk.Entry(top, textvariable=self.query_var, width=44)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        self.search_entry.bind("<KeyRelease>", lambda event: self.refresh_files())
        ttk.Button(top, text="Clear", command=self._clear_search).pack(side=tk.LEFT)
        ttk.Button(top, text="Close", command=self.withdraw).pack(side=tk.RIGHT, padx=(8, 0))

        options = ttk.Frame(self, padding=(10, 0, 10, 6))
        options.pack(fill=tk.X)
        ttk.Label(
            options,
            text="Cases loaded directly from cases/E01.yaml … cases/E23.yaml. Select one to open its Case Trace.",
            style="Status.TLabel",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Checkbutton(
            options,
            text="Show current-view graph nodes",
            variable=self.show_view_nodes_var,
            command=self.refresh_files,
        ).pack(side=tk.RIGHT)

        table_wrap = ttk.Frame(self, padding=(10, 0, 10, 6))
        table_wrap.pack(fill=tk.BOTH, expand=True)
        self.files = ttk.Treeview(
            table_wrap,
            columns=("title", "result", "artifact"),
            show="tree headings",
            selectmode="browse",
            style="Browser.Treeview",
        )
        self.files.heading("#0", text="Case / Node")
        self.files.heading("title", text="Title / Description")
        self.files.heading("result", text="Result / Type")
        self.files.heading("artifact", text="Repository artifact")
        self.files.column("#0", width=130, minwidth=100, stretch=False)
        self.files.column("title", width=430, minwidth=240, stretch=True)
        self.files.column("result", width=210, minwidth=140, stretch=True)
        self.files.column("artifact", width=280, minwidth=180, stretch=True)
        yscroll = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL, command=self.files.yview)
        xscroll = AutoHideScrollbar(table_wrap, orient=tk.HORIZONTAL, command=self.files.xview)
        self.files.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.files.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)
        self.files.bind("<Double-1>", lambda event: self.select_selected(close=True))
        self.files.bind("<Return>", lambda event: self.select_selected(close=True))
        self.files.configure(cursor="hand2")
        self.search_entry.configure(cursor="xterm")

        bottom = ttk.Frame(self, padding=(10, 4, 10, 10))
        bottom.pack(fill=tk.X)
        ttk.Label(bottom, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(bottom, text="Open Selected Case Trace", command=lambda: self.select_selected(close=True)).pack(side=tk.RIGHT)

    @staticmethod
    def _kind_label(kind: str) -> str:
        return kind.replace("_", " ").replace("-", " ").title()

    def refresh_from_graph(self) -> None:
        self.refresh_files()

    def _clear_search(self) -> None:
        self.query_var.set("")
        self.refresh_files()
        self.search_entry.focus_set()

    def refresh_files(self) -> None:
        self.files.delete(*self.files.get_children())
        self._row_target.clear()
        query = self.query_var.get().strip().casefold()
        corpus = self.graph_lab.app.corpus
        if corpus is None:
            self.status_var.set("No PMS-VECTOR corpus loaded.")
            return

        cases_group = self.files.insert("", tk.END, text=f"CASES FROM cases/ ({len(corpus.cases)})", open=True)
        visible_cases = 0
        selected_row = ""
        active_case_id = self.graph_lab.trace_case_id

        for case in corpus.cases:
            haystack = " ".join([
                case.case_id,
                case.title,
                case.result_status,
                case.paper_ref,
                " ".join(case.pressure_families),
                " ".join(case.constructs_under_test),
            ]).casefold()
            if query and query not in haystack:
                continue
            row = self.files.insert(
                cases_group,
                tk.END,
                text=case.case_id,
                values=(case.title, case.result_status or "—", case.yaml_path),
            )
            self._row_target[row] = ("case", case.case_id)
            if case.case_id == active_case_id:
                selected_row = row
            visible_cases += 1

        visible_nodes = 0
        if self.show_view_nodes_var.get():
            view_group = self.files.insert(
                "", tk.END,
                text=f"CURRENT VIEW NODES — {self.graph_lab.view_var.get()}",
                open=True,
            )
            for node in self.graph_lab.nodes:
                # Case-linked graph nodes are already represented once, above.
                if node.record_id and node.record_id in corpus.case_by_id:
                    continue
                label = node.label.replace("\n", " / ").strip()
                kind_label = self._kind_label(node.kind)
                artifact = node.rel_path or "—"
                haystack = f"{label} {kind_label} {artifact} {node.details}".casefold()
                if query and query not in haystack:
                    continue
                row = self.files.insert(
                    view_group,
                    tk.END,
                    text=f"↳ {kind_label}",
                    values=(label, kind_label, artifact),
                )
                self._row_target[row] = ("node", node.node_id)
                visible_nodes += 1

        if selected_row:
            self.files.selection_set(selected_row)
            self.files.focus(selected_row)
            self.files.see(selected_row)

        extra = f" • {visible_nodes} optional graph nodes" if self.show_view_nodes_var.get() else ""
        self.status_var.set(f"{visible_cases} of {len(corpus.cases)} cases visible{extra}")

    def select_selected(self, close: bool = False) -> None:
        selection = self.files.selection()
        if not selection:
            return
        target = self._row_target.get(selection[0])
        if target is None:
            return
        target_kind, target_id = target
        if target_kind == "case":
            if self.graph_lab.select_case_trace(target_id):
                self.graph_lab.deiconify()
                self.graph_lab.lift()
                if close:
                    self.withdraw()
            return
        if self.graph_lab.select_node_by_id(target_id):
            self.graph_lab.deiconify()
            self.graph_lab.lift()
            if close:
                self.withdraw()

    def apply_theme(self) -> None:
        palette = self.graph_lab.theme_palette()
        self.configure(background=palette["window_bg"])
        style = ttk.Style(self)
        style.configure(
            "Browser.Treeview",
            background=palette["panel_bg"],
            fieldbackground=palette["panel_bg"],
            foreground=palette["fg"],
            rowheight=26,
        )
        style.map(
            "Browser.Treeview",
            background=[("selected", palette["selection_bg"])],
            foreground=[("selected", palette["fg"])],
        )
        style.configure(
            "Browser.Treeview.Heading",
            background=palette["button_bg"],
            foreground=palette["fg"],
        )
        style.map("Browser.Treeview.Heading", background=[("active", palette["button_hover_bg"])])

    def _center_over_parent(self) -> None:
        try:
            self.update_idletasks()
            width = max(self.winfo_width(), 820)
            height = max(self.winfo_height(), 520)
            parent_x = self.graph_lab.winfo_rootx()
            parent_y = self.graph_lab.winfo_rooty()
            parent_w = self.graph_lab.winfo_width()
            parent_h = self.graph_lab.winfo_height()
            x = max(0, parent_x + (parent_w - width) // 2)
            y = max(0, parent_y + (parent_h - height) // 2)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass


class GraphLab(tk.Toplevel):
    """Interactive 2D exploration layer for declared PMS-VECTOR relations."""

    VIEW_ARCHITECTURE = "Architecture & Status"
    VIEW_DEPENDENCY = "Dependency / Warrant"
    VIEW_PRESSURE = "Case Pressure Map"
    VIEW_CASE = "Selected Case Trace"
    VIEW_REDUCTION = "Reduction Graph"

    def __init__(self, app: "PmsVectorReaderApp"):
        super().__init__(app)
        self.app = app
        self.title(f"{APP_TITLE} — Graph Lab")
        self.geometry("1320x840")
        self.minsize(900, 600)
        self.protocol("WM_DELETE_WINDOW", self._hide)

        self.view_var = tk.StringVar(value=self.VIEW_ARCHITECTURE)
        self.pressure_var = tk.StringVar(value="ALL")
        self.result_var = tk.StringVar(value="ALL")
        self.labels_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Left-drag to pan • wheel to zoom • click a node for details")
        self.h_spacing_var = tk.DoubleVar(value=1.0)
        self.v_spacing_var = tk.DoubleVar(value=1.0)
        self.h_spacing_label_var = tk.StringVar(value="1.00×")
        self.v_spacing_label_var = tk.StringVar(value="1.00×")
        self.trace_case_id = ""

        self.nodes: List[GraphNode] = []
        self.edges: List[Tuple[str, str]] = []
        self.edge_kinds: Dict[Tuple[str, str], str] = {}
        self.node_by_id: Dict[str, GraphNode] = {}
        self.projected: Dict[str, Tuple[float, float, float, float]] = {}
        self.selected_node_id = ""
        self.hovered_node_id = ""
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._drag_start: Optional[Tuple[int, int]] = None
        self._drag_moved = False
        self.browser_dialog: Optional[BrowseFilesDialog] = None
        self._detail_texts: Dict[str, tk.Text] = {}

        self._build_ui()
        self.apply_theme()
        self.refresh()
        self.after_idle(self._maximize)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(8, 8, 8, 5))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="Browse Files", command=self.browse_files).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(toolbar, text="View").pack(side=tk.LEFT)
        self.view_box = ttk.Combobox(
            toolbar, textvariable=self.view_var, state="readonly", width=27,
            style="Graph.TCombobox",
            values=[self.VIEW_ARCHITECTURE, self.VIEW_DEPENDENCY, self.VIEW_PRESSURE, self.VIEW_CASE, self.VIEW_REDUCTION],
        )
        self.view_box.pack(side=tk.LEFT, padx=(5, 12))
        self.view_box.bind("<<ComboboxSelected>>", lambda event: self.refresh())

        ttk.Label(toolbar, text="Pressure").pack(side=tk.LEFT)
        self.pressure_box = ttk.Combobox(toolbar, textvariable=self.pressure_var, state="readonly", width=28, style="Graph.TCombobox")
        self.pressure_box.pack(side=tk.LEFT, padx=(5, 12))
        self.pressure_box.bind("<<ComboboxSelected>>", lambda event: self.refresh())

        ttk.Label(toolbar, text="Result").pack(side=tk.LEFT)
        self.result_box = ttk.Combobox(toolbar, textvariable=self.result_var, state="readonly", width=24, style="Graph.TCombobox")
        self.result_box.pack(side=tk.LEFT, padx=(5, 12))
        self.result_box.bind("<<ComboboxSelected>>", lambda event: self.refresh())

        ttk.Checkbutton(toolbar, text="Labels", variable=self.labels_var, command=self.redraw).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Reset View", command=self.reset_view).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(toolbar, text="Close", command=self._hide).pack(side=tk.RIGHT)
        for box in (self.view_box, self.pressure_box, self.result_box):
            box.configure(cursor="hand2")

        spacing = ttk.Frame(self, padding=(8, 0, 8, 5))
        spacing.pack(fill=tk.X)
        ttk.Label(spacing, text="Node spacing  H").pack(side=tk.LEFT)
        self.h_spacing_scale = ttk.Scale(
            spacing, from_=0.50, to=2.50, variable=self.h_spacing_var,
            orient=tk.HORIZONTAL, length=180, command=lambda _value: self._on_spacing_changed(),
        )
        self.h_spacing_scale.pack(side=tk.LEFT, padx=(6, 4))
        ttk.Label(spacing, textvariable=self.h_spacing_label_var, width=6).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(spacing, text="V").pack(side=tk.LEFT)
        self.v_spacing_scale = ttk.Scale(
            spacing, from_=0.50, to=2.50, variable=self.v_spacing_var,
            orient=tk.HORIZONTAL, length=180, command=lambda _value: self._on_spacing_changed(),
        )
        self.v_spacing_scale.pack(side=tk.LEFT, padx=(6, 4))
        ttk.Label(spacing, textvariable=self.v_spacing_label_var, width=6).pack(side=tk.LEFT)
        ttk.Label(
            spacing,
            text="presentation spacing only — no semantic distance",
            style="Status.TLabel",
        ).pack(side=tk.LEFT, padx=(18, 0))

        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 5))

        canvas_frame = ttk.Frame(main)
        main.add(canvas_frame, weight=4)
        self.canvas = tk.Canvas(canvas_frame, highlightthickness=0, background="#10151c")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda event: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-1>", self._on_double_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda event: self._zoom_by(1.12))
        self.canvas.bind("<Button-5>", lambda event: self._zoom_by(0.89))

        detail_frame = ttk.Frame(main, padding=(8, 4, 4, 4))
        main.add(detail_frame, weight=2)
        ttk.Label(detail_frame, text="Case / Node Details", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        self.detail_notebook = ttk.Notebook(detail_frame, style="Graph.TNotebook")
        self.detail_notebook.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        self._create_detail_tab("Summary", wrap=tk.WORD)
        self._create_detail_tab("YAML", wrap=tk.NONE)
        self._create_detail_tab("Markdown", wrap=tk.WORD)
        self._create_detail_tab("Relations", wrap=tk.WORD)
        self._create_detail_tab("Trace", wrap=tk.WORD)
        ttk.Label(self, textvariable=self.status_var, anchor=tk.W, padding=(8, 4), style="Status.TLabel").pack(fill=tk.X)

    def _create_detail_tab(self, label: str, wrap: str) -> None:
        frame = ttk.Frame(self.detail_notebook, padding=0)
        text = tk.Text(frame, wrap=wrap, padx=12, pady=12, state=tk.DISABLED, undo=False)
        yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=yscroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        if wrap == tk.NONE:
            xscroll = AutoHideScrollbar(frame, orient=tk.HORIZONTAL, command=text.xview)
            text.configure(xscrollcommand=xscroll.set)
            xscroll.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.detail_notebook.add(frame, text=label)
        self._detail_texts[label] = text
        self._configure_detail_tags(text)

    def _maximize(self) -> None:
        try:
            self.state("zoomed")
            return
        except tk.TclError:
            pass
        try:
            self.attributes("-zoomed", True)
            return
        except tk.TclError:
            pass

    def _hide(self) -> None:
        if self.browser_dialog is not None and self.browser_dialog.winfo_exists():
            self.browser_dialog.withdraw()
        self.withdraw()

    def browse_files(self) -> None:
        if self.browser_dialog is None or not self.browser_dialog.winfo_exists():
            self.browser_dialog = BrowseFilesDialog(self)
        else:
            self.browser_dialog.deiconify()
            self.browser_dialog.lift()
            self.browser_dialog.refresh_from_graph()
            self.browser_dialog.apply_theme()
            self.browser_dialog.after_idle(self.browser_dialog._center_over_parent)

    def set_current_path(self, rel_path: Optional[str]) -> None:
        corpus = self.app.corpus
        if corpus is not None:
            case = corpus.case_for_path(rel_path)
            if case is not None:
                self.trace_case_id = case.case_id
        if self.view_var.get() == self.VIEW_CASE:
            self.refresh()

    def _on_spacing_changed(self) -> None:
        self.h_spacing_label_var.set(f"{self.h_spacing_var.get():.2f}×")
        self.v_spacing_label_var.set(f"{self.v_spacing_var.get():.2f}×")
        self.redraw()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.h_spacing_var.set(1.0)
        self.v_spacing_var.set(1.0)
        self._on_spacing_changed()

    def select_case_trace(self, case_id: str) -> bool:
        corpus = self.app.corpus
        if corpus is None or case_id not in corpus.case_by_id:
            return False
        self.trace_case_id = case_id
        self.view_var.set(self.VIEW_CASE)
        self.refresh()
        # Select the first trace node after rebuilding so the case is visibly active.
        if self.nodes:
            case_node = next((node for node in self.nodes if node.record_id == case_id), self.nodes[0])
            self.select_node_by_id(case_node.node_id)
        self.status_var.set(f"Selected Case Trace: {case_id}")
        return True

    def select_node_by_id(self, node_id: str) -> bool:
        node = self.node_by_id.get(node_id)
        if node is None:
            return False
        if node.record_id and self.app.corpus is not None and node.record_id in self.app.corpus.case_by_id:
            self.trace_case_id = node.record_id
        self.selected_node_id = node.node_id
        self._show_node_details(node)
        self.status_var.set(f"Selected: {node.label.replace(chr(10), ' / ')} • drag to pan • wheel to zoom")
        self.redraw()
        return True

    def refresh(self) -> None:
        corpus = self.app.corpus
        if corpus is None:
            return
        pressures = sorted({p for case in corpus.cases for p in case.pressure_families})
        results = sorted({case.result_status for case in corpus.cases if case.result_status})
        self.pressure_box.configure(values=["ALL"] + pressures)
        self.result_box.configure(values=["ALL"] + results)
        if self.pressure_var.get() not in (["ALL"] + pressures):
            self.pressure_var.set("ALL")
        if self.result_var.get() not in (["ALL"] + results):
            self.result_var.set("ALL")

        view = self.view_var.get()
        self.edge_kinds = {}
        if view == self.VIEW_ARCHITECTURE:
            self.nodes, self.edges = self._build_architecture_status()
        elif view == self.VIEW_DEPENDENCY:
            self.nodes, self.edges = self._build_dependency_graph()
        elif view == self.VIEW_PRESSURE:
            self.nodes, self.edges = self._build_case_pressure_map()
        elif view == self.VIEW_CASE:
            self.nodes, self.edges = self._build_case_trace()
        else:
            self.nodes, self.edges = self._build_reduction_graph()

        self.node_by_id = {node.node_id: node for node in self.nodes}
        self.selected_node_id = ""
        self.hovered_node_id = ""
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._show_general_details(self._view_description(view))
        self.redraw()
        if self.browser_dialog is not None and self.browser_dialog.winfo_exists():
            self.browser_dialog.refresh_from_graph()

    def _filtered_cases(self) -> List[CaseSummary]:
        assert self.app.corpus is not None
        p = self.pressure_var.get()
        r = self.result_var.get()
        return [
            case for case in self.app.corpus.cases
            if (p == "ALL" or p in case.pressure_families)
            and (r == "ALL" or case.result_status == r)
        ]

    def _build_architecture_status(self) -> Tuple[List[GraphNode], List[Tuple[str, str]]]:
        corpus = self.app.corpus
        assert corpus is not None
        model = corpus.model_data
        identity = as_dict(model.get("vector_identity"))
        core = as_dict(identity.get("canonical_core"))
        state = as_dict(identity.get("concrete_state"))
        current = as_dict(identity.get("current_theory_status"))
        nodes: List[GraphNode] = [GraphNode("arch:root", "PMS-VECTOR", "root", 0, -300, 0, "model/PMS-VECTOR.yaml", scalar_text(core.get("notation")))]
        edges: List[Tuple[str, str]] = []
        groups = [
            ("core", "CORE", -260, -120),
            ("state", "STATE", 0, -120),
            ("support", "SUPPORTING", 260, -120),
            ("theory", "THEORY STATUS", 0, 110),
        ]
        for gid, label, x, y in groups:
            nid=f"arch:{gid}"
            nodes.append(GraphNode(nid, label, "group", x, y, 0, "model/PMS-VECTOR.yaml"))
            edges.append(("arch:root", nid))
        core_components=[str(x) for x in as_list(core.get("components"))] or ["DIR", "ROT"]
        for i,c in enumerate(core_components):
            status = scalar_text(current.get(c.lower()))
            nid=f"arch:construct:{c}"
            nodes.append(GraphNode(nid, f"{c}\n{status or 'core'}", "core", -330 + i*140, 40, 0, "model/PMS-VECTOR.yaml", f"Current status: {status or 'core'}"))
            edges.append(("arch:core", nid))
        for i,c in enumerate([str(x) for x in as_list(state.get("state_fields"))]):
            nid=f"arch:state:{c}"
            nodes.append(GraphNode(nid, c, "state", -60+i*120, 40, 0, "model/PMS-VECTOR.yaml", scalar_text(state.get("operation_state_boundary"))))
            edges.append(("arch:state", nid))
        support_specs=[("DIST", scalar_text(current.get("dist"))), ("AdultOrientation", scalar_text(current.get("adult_orientation")))]
        for i,(c,status) in enumerate(support_specs):
            nid=f"arch:support:{c}"
            nodes.append(GraphNode(nid, f"{c}\n{status}", "supporting", 200+i*160, 40, 0, "model/PMS-VECTOR.yaml", f"Current status: {status}"))
            edges.append(("arch:support", nid))
        status=scalar_text(current.get("standalone_vector"))
        nodes.append(GraphNode("arch:standalone", f"Standalone VECTOR\n{status}", "reduced", 0, 260, 0, "model/PMS-VECTOR.yaml", f"Current status: {status}"))
        edges.append(("arch:theory", "arch:standalone"))
        return nodes, edges

    def _build_dependency_graph(self) -> Tuple[List[GraphNode], List[Tuple[str, str]]]:
        corpus = self.app.corpus
        assert corpus is not None
        root = as_dict(corpus.dependency_data.get("dependency_map"))
        node_specs = as_dict(root.get("dependency_nodes"))
        edge_specs = [as_dict(x) for x in as_list(root.get("dependency_edges"))]
        prohibited = [as_dict(x) for x in as_list(root.get("prohibited_inferences"))]
        roots = [str(x) for x in as_list(root.get("load_bearing_roots"))]

        all_ids=set(node_specs)
        for e in edge_specs:
            all_ids.update([str(e.get("dependent","")), str(e.get("dependency",""))])
        for e in prohibited:
            all_ids.update([str(e.get("source","")), str(e.get("target",""))])
        all_ids.discard("")

        dependencies: Dict[str,List[str]]={nid:[] for nid in all_ids}
        for e in edge_specs:
            d=str(e.get("dependent","")); dep=str(e.get("dependency",""))
            if d and dep: dependencies.setdefault(d,[]).append(dep)
        layer: Dict[str,int]={r:0 for r in roots}
        queue_ids=list(roots)
        while queue_ids:
            d=queue_ids.pop(0)
            for dep in dependencies.get(d,[]):
                nl=layer.get(d,0)+1
                if dep not in layer or nl<layer[dep]:
                    layer[dep]=nl; queue_ids.append(dep)
        for nid in all_ids:
            layer.setdefault(nid, max(layer.values(), default=0)+1)
        by_layer: Dict[int,List[str]]={}
        for nid,lvl in layer.items(): by_layer.setdefault(lvl,[]).append(nid)

        nodes=[]; edges=[]
        for lvl in sorted(by_layer):
            ids=sorted(by_layer[lvl])
            for i,nid in enumerate(ids):
                spec=as_dict(node_specs.get(nid))
                kind=str(spec.get("kind") or "dependency")
                desc=scalar_text(spec.get("description"))
                x=(i-(len(ids)-1)/2)*170
                y=-300+lvl*125
                nodes.append(GraphNode(f"dep:{nid}", nid, kind, x,y,0, "reference/Dependency Map.yaml", desc))
        node_ids={n.node_id for n in nodes}
        for e in edge_specs:
            a=f"dep:{e.get('dependent','')}"; b=f"dep:{e.get('dependency','')}"
            if a in node_ids and b in node_ids:
                edges.append((a,b)); self.edge_kinds[(a,b)]="depends_on"
        for e in prohibited:
            a=f"dep:{e.get('source','')}"; b=f"dep:{e.get('target','')}"
            if a in node_ids and b in node_ids:
                edges.append((a,b)); self.edge_kinds[(a,b)]="blocked"
        return nodes, edges

    def _build_case_pressure_map(self) -> Tuple[List[GraphNode], List[Tuple[str, str]]]:
        cases=self._filtered_cases()
        nodes=[]; edges=[]
        pressures=sorted({p for c in cases for p in c.pressure_families})
        results=sorted({c.result_status for c in cases if c.result_status})
        for i,p in enumerate(pressures):
            y=(i-(len(pressures)-1)/2)*78
            nodes.append(GraphNode(f"pressure:{p}", p, "pressure", -390,y,0,"cases/index.yaml","Navigation label only; not a VECTOR operator taxonomy."))
        for i,c in enumerate(cases):
            y=(i-(len(cases)-1)/2)*68
            nodes.append(GraphNode(f"case:{c.case_id}", c.case_id, "case", -40,y,0,c.yaml_path,self._case_summary(c),c.case_id))
            for p in c.pressure_families:
                if f"pressure:{p}" in {n.node_id for n in nodes}:
                    edges.append((f"pressure:{p}", f"case:{c.case_id}"))
        for i,r in enumerate(results):
            y=(i-(len(results)-1)/2)*105
            nodes.append(GraphNode(f"result:{r}", r, "result", 300,y,0,"model/PMS-VECTOR.yaml","Typed local case result; not pass/fail."))
        for c in cases:
            if c.result_status:
                edges.append((f"case:{c.case_id}", f"result:{c.result_status}"))
            if c.architecture_applies:
                rid=f"reduction:{c.case_id}"
                nodes.append(GraphNode(rid, "Architecture\nEffect", "reduced", 520, next(n.y for n in nodes if n.node_id==f"case:{c.case_id}"),0,c.yaml_path,c.architecture_effect,c.case_id))
                edges.append((f"case:{c.case_id}", rid))
        return nodes, edges

    def _current_case(self) -> Optional[CaseSummary]:
        if self.app.corpus is None:
            return None
        if self.trace_case_id:
            selected = self.app.corpus.case_by_id.get(self.trace_case_id)
            if selected is not None:
                return selected
        return self.app.corpus.case_for_path(self.app.current_path)

    def _build_case_trace(self) -> Tuple[List[GraphNode], List[Tuple[str, str]]]:
        case=self._current_case()
        if case is None:
            return [GraphNode("none", "Select an E## YAML or Markdown case", "warning", 0,0,0)], []
        record=as_dict(case.raw.get("vector_record"))
        reference=as_dict(record.get("reference")); frame=as_dict(record.get("frame")); question=as_dict(record.get("question"))
        dirb=as_dict(record.get("dir")); warrant=as_dict(dirb.get("warrant")); calibration=as_dict(record.get("calibration")); rot=as_dict(record.get("rot_profile")); pressure=as_dict(record.get("pressure")); result=as_dict(record.get("result"))
        items=[
            ("baseline","Baseline Claim","claim",case.baseline_claim),
            ("conditions","Reference / Frame / Question","condition",join_nonempty([scalar_text(reference.get("object")), scalar_text(question.get("scope")), scalar_text(question.get("claim_ceiling")), "Included: "+", ".join(map(str,as_list(frame.get("included"))))])),
            ("warrant","Warrant / Calibration","warrant",join_nonempty(["Source types: "+", ".join(map(str,as_list(warrant.get("source_types")))), "External premises: "+", ".join(map(str,as_list(warrant.get("external_premises")))), scalar_text(calibration.get("basis")), scalar_text(calibration.get("unresolved"))])),
            ("vulnerability","Vulnerability / ROT","rotation",join_nonempty([summarize_rot(rot), "Critical regressions: "+", ".join(map(str,as_list(dirb.get("critical_regressions"))))])),
            ("pressure","Rival / Evidence / Option Pressure","pressure",summarize_mapping(pressure)),
            ("result","Result", "result", join_nonempty([scalar_text(result.get("status")), scalar_text(result.get("surviving_claim")), scalar_text(result.get("scope")), scalar_text(result.get("stop_reason"))])),
            ("nonimp","What Does Not Follow","boundary","\n".join(f"- {x}" for x in case.non_implications)),
        ]
        if case.architecture_applies:
            items.insert(-1,("architecture","Architecture Effect","reduced",case.architecture_effect))
        nodes=[]; edges=[]
        for i,(nid,label,kind,details) in enumerate(items):
            x=(i-(len(items)-1)/2)*190
            y=0 if i%2==0 else 110
            nodes.append(GraphNode(f"trace:{nid}",label,kind,x,y,0,case.yaml_path,details,case.case_id))
            if i: edges.append((f"trace:{items[i-1][0]}", f"trace:{nid}"))
        return nodes, edges

    def _build_reduction_graph(self) -> Tuple[List[GraphNode], List[Tuple[str, str]]]:
        corpus=self.app.corpus; assert corpus is not None
        root=as_dict(corpus.dependency_data.get("dependency_map"))
        reductions=[as_dict(x) for x in as_list(root.get("enacted_reductions"))]
        nodes=[GraphNode("red:root","PMS-VECTOR\nLoss History","root",0,-260,0,"reference/Dependency Map.yaml","Declared enacted reductions only.")]
        edges=[]
        for i,r in enumerate(reductions):
            x=(i-(len(reductions)-1)/2)*235
            target=str(r.get("target") or "")
            before=scalar_text(r.get("from")); after=scalar_text(r.get("to")); pref=as_dict(r.get("pressure_ref")); case_id=scalar_text(pref.get("case")); paper=scalar_text(pref.get("paper"))
            pre=f"red:{i}:pre"; pressure=f"red:{i}:pressure"; post=f"red:{i}:post"
            nodes.append(GraphNode(pre,f"{target}\n{before}","prior",x,-80,0,"reference/Dependency Map.yaml",f"Previous candidate status: {before}"))
            rel=corpus.case_by_id.get(case_id).yaml_path if case_id in corpus.case_by_id else "reference/Dependency Map.yaml"
            nodes.append(GraphNode(pressure, case_id or "Deletion /\nDiscrimination", "pressure",x,80,0,rel,paper,case_id))
            nodes.append(GraphNode(post,after,"reduced",x,240,0,"model/PMS-VECTOR.yaml",f"{target}: {after}"))
            edges.extend([("red:root",pre),(pre,pressure),(pressure,post)])
        return nodes, edges

    @staticmethod
    def _view_description(view: str) -> str:
        return {
            GraphLab.VIEW_ARCHITECTURE: "Declared model architecture and current status. Core, state, supporting constructs, and scope reductions remain type-distinct.",
            GraphLab.VIEW_DEPENDENCY: "Declared dependencies plus prohibited inference edges. Dependency transmits vulnerability; it does not create warrant.",
            GraphLab.VIEW_PRESSURE: "Pressure-family navigation → case fixture → typed local result, with declared architecture effects shown separately.",
            GraphLab.VIEW_CASE: "Selected case trace from baseline through declared warrant/pressure to result and non-implications.",
            GraphLab.VIEW_REDUCTION: "Declared loss history from prior candidate status through pressure/test to current reduced status.",
        }.get(view, "") + "\n\nNo graph edge is inferred from co-occurrence or visual proximity."

    @staticmethod
    def _case_summary(case: CaseSummary) -> str:
        return (
            f"{case.case_id} — {case.title}\n\n"
            f"Paper: {case.paper_ref}\nFormat: {case.case_format or '—'}\n"
            f"Adversarial: {'yes' if case.adversarial else 'no'}{(' — '+case.paper_label) if case.paper_label else ''}\n"
            f"Load-bearing: {'yes' if case.load_bearing else 'no'}\n\n"
            f"Pressure families\n" + ("\n".join(f"- {x}" for x in case.pressure_families) or "—") + "\n\n"
            f"Constructs under test\n" + ("\n".join(f"- {x}" for x in case.constructs_under_test) or "—") + "\n\n"
            f"Theoretical function\n{case.theoretical_function or '—'}\n\n"
            f"Local result\n{case.result_status or '—'}\n\n"
            f"Architecture effect\n{case.architecture_effect if case.architecture_applies else 'none enacted'}\n\n"
            f"Artifacts\nYAML: {case.yaml_path}\nMarkdown: {case.markdown_path or '—'}"
        )

    def _case_for_node(self, node: GraphNode) -> Optional[CaseSummary]:
        corpus=self.app.corpus
        if corpus is None or not node.record_id:
            return None
        return corpus.case_by_id.get(node.record_id)

    def _show_general_details(self, summary: str) -> None:
        self._set_detail_text("Summary", summary + "\n\nNo node is selected.")
        self._set_detail_text("YAML", "No YAML artifact is selected.")
        self._set_detail_text("Markdown", "No Markdown artifact is selected.")
        self._set_detail_text("Relations", "Select a node to inspect declared relations.")
        self._set_detail_text("Trace", "Select a case-linked node to inspect its trace.")
        self.detail_notebook.select(0)

    def _show_node_details(self, node: GraphNode) -> None:
        case=self._case_for_node(node); corpus=self.app.corpus
        if case is None:
            self._set_detail_text("Summary", f"{node.label}\n\nNode kind: {node.kind}\n\n{node.details or 'No additional declared details.'}\n\nArtifact: {node.rel_path or '—'}")
            yaml_text="No YAML artifact is linked to this node."; md_text="No Markdown artifact is linked to this node."
            if corpus is not None and node.rel_path in corpus.documents:
                doc=corpus.documents[node.rel_path]
                if node.rel_path.lower().endswith((".yaml",".yml")): yaml_text=doc.text
                elif doc.file_type=="md": md_text=doc.text
            self._set_detail_text("YAML",yaml_text,node.rel_path if node.rel_path.lower().endswith((".yaml",".yml")) else None)
            self._set_detail_text("Markdown",md_text,node.rel_path if node.rel_path.lower().endswith((".md",".html",".htm")) else None)
            relation_note = "Blocked edge" if any(node.node_id in pair and kind=="blocked" for pair,kind in self.edge_kinds.items()) else "Declared graph node."
            self._set_detail_text("Relations", relation_note + "\n\nVisual proximity creates no relation.")
            self._set_detail_text("Trace", "No case trace is attached to this node.")
            self.detail_notebook.select(0); return
        self._set_detail_text("Summary", self._case_summary(case))
        yaml_text=corpus.documents[case.yaml_path].text if corpus and case.yaml_path in corpus.documents else "Artifact unavailable."
        md_text=corpus.documents[case.markdown_path].text if corpus and case.markdown_path in corpus.documents else "No Markdown companion is available."
        self._set_detail_text("YAML",yaml_text,case.yaml_path)
        self._set_detail_text("Markdown",md_text,case.markdown_path)
        self._set_detail_text("Relations", self._relations_text(case))
        self._set_detail_text("Trace", self._trace_text(case))

    @staticmethod
    def _relations_text(case: CaseSummary) -> str:
        return (
            f"Pressure families (navigation only)\n" + ("\n".join(f"- {x}" for x in case.pressure_families) or "—") + "\n\n"
            f"Constructs under test\n" + ("\n".join(f"- {x}" for x in case.constructs_under_test) or "—") + "\n\n"
            f"Architecture effect\n{case.architecture_effect if case.architecture_applies else 'none enacted'}\n\n"
            "pressure_families != VECTOR operator taxonomy\nconstructs_under_test != constructs validated\nlocal result != architecture effect"
        )

    @staticmethod
    def _trace_text(case: CaseSummary) -> str:
        record=as_dict(case.raw.get("vector_record")); pressure=as_dict(record.get("pressure")); result=as_dict(record.get("result"))
        return (
            f"1. Baseline Claim\n   {case.baseline_claim or '—'}\n\n"
            f"2. DIR / bounded relation\n   {case.dir_relation or '—'}\n\n"
            f"3. Pressure\n   {summarize_mapping(pressure) or '—'}\n\n"
            f"4. Result\n   {scalar_text(result.get('status')) or '—'}\n\n"
            f"5. Surviving claim\n   {scalar_text(result.get('surviving_claim')) or '—'}\n\n"
            f"6. Scope\n   {scalar_text(result.get('scope')) or '—'}\n\n"
            f"7. Stop reason\n   {scalar_text(result.get('stop_reason')) or '—'}\n\n"
            "8. What does not follow\n   " + ("\n   ".join(f"- {x}" for x in case.non_implications) or "—")
        )

    def _configure_detail_tags(self, widget: tk.Text) -> None:
        palette=self.theme_palette()
        widget.tag_configure("detail_body", font=("Segoe UI",10), foreground=palette["text_fg"], spacing1=1, spacing3=1)
        widget.tag_configure("detail_title", font=("Segoe UI",14,"bold"), foreground=palette["title_fg"], spacing1=2, spacing3=10)
        widget.tag_configure("detail_h2", font=("Segoe UI",12,"bold"), foreground=palette["title_fg"], spacing1=9, spacing3=4)
        widget.tag_configure("detail_h3", font=("Segoe UI",10,"bold"), foreground=palette["title_fg"], spacing1=6, spacing3=2)
        widget.tag_configure("detail_bold", font=("Segoe UI",10,"bold"), foreground=palette["text_fg"])
        widget.tag_configure("detail_italic", font=("Segoe UI",10,"italic"), foreground=palette["text_fg"])
        widget.tag_configure("detail_code", font=("Consolas",9), foreground=palette["text_fg"], background=palette["label_bg"], lmargin1=8,lmargin2=8, spacing1=2, spacing3=2)
        widget.tag_configure("detail_inline_code", font=("Consolas",9), foreground=palette["selected_ring"], background=palette["label_bg"])
        widget.tag_configure("detail_quote", font=("Segoe UI",10,"italic"), foreground=palette["muted_fg"], lmargin1=16,lmargin2=16)
        widget.tag_configure("detail_list", font=("Segoe UI",10), foreground=palette["text_fg"], lmargin1=18,lmargin2=34)
        widget.tag_configure("detail_rule", foreground=palette["edge"], spacing1=5, spacing3=5)
        widget.tag_configure("detail_table", font=("Consolas",9), foreground=palette["text_fg"], background=palette["label_bg"])
        widget.tag_configure("detail_link", font=("Segoe UI",10,"underline"), foreground=palette["hover_ring"])
        widget.tag_configure("yaml_key", font=("Consolas",9,"bold"), foreground=palette["hover_ring"])
        widget.tag_configure("yaml_value", font=("Consolas",9), foreground=palette["text_fg"])
        widget.tag_configure("yaml_comment", font=("Consolas",9,"italic"), foreground=palette["muted_fg"])
        widget.tag_configure("yaml_code", font=("Consolas",9), foreground=palette["text_fg"])

    def _set_detail_text(self, label: str, text: str, rel_path: Optional[str]=None) -> None:
        widget=self._detail_texts[label]
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0",tk.END)
        self._configure_detail_tags(widget)
        if label=="YAML" and not text.startswith("No YAML"):
            self._render_detail_yaml(widget, text)
        elif label=="Markdown" and not text.startswith("No Markdown"):
            self._render_detail_markdown(widget, text, rel_path)
        else:
            widget.insert("1.0", text, ("detail_body",))
        widget.configure(state=tk.DISABLED)
        widget.yview_moveto(0.0)
        widget.xview_moveto(0.0)

    def _render_detail_yaml(self, widget: tk.Text, text: str) -> None:
        for raw in text.splitlines():
            if raw.lstrip().startswith("#"):
                widget.insert(tk.END,raw+"\n",("yaml_comment",))
                continue
            m=re.match(r"^(\s*)(-\s+)?([A-Za-z0-9_.-]+)(\s*:\s*)(.*)$",raw)
            if m:
                indent,bullet,key,sep,val=m.groups()
                widget.insert(tk.END,indent+(bullet or ""),("yaml_code",))
                widget.insert(tk.END,key,("yaml_key",))
                widget.insert(tk.END,sep,("yaml_code",))
                widget.insert(tk.END,val+"\n",("yaml_value",))
            else:
                widget.insert(tk.END,raw+"\n",("yaml_code",))

    def _render_detail_markdown(self, widget: tk.Text, text: str, rel_path: Optional[str]) -> None:
        body = strip_frontmatter(text)
        lines = body.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]
            fence = FENCE_RE.match(raw)
            if fence:
                language=(fence.group(2) or "").casefold()
                block=[]; i+=1
                while i < len(lines) and not FENCE_RE.match(lines[i]):
                    block.append(lines[i]); i+=1
                if i < len(lines): i+=1
                if language in {"yaml","yml"}:
                    self._render_detail_yaml(widget,"\n".join(block))
                else:
                    widget.insert(tk.END,"\n".join(block)+"\n",("detail_code",))
                continue
            heading=HEADING_RE.match(raw)
            if heading:
                level=len(heading.group(1))
                tag="detail_title" if level==1 else "detail_h2" if level<=3 else "detail_h3"
                self._insert_detail_inline_markdown(widget, clean_heading_text(heading.group(2)), (tag,), rel_path)
                widget.insert(tk.END,"\n",(tag,)); i+=1; continue
            if looks_like_table_line(raw):
                table=[]
                while i < len(lines) and looks_like_table_line(lines[i]):
                    table.append(lines[i]); i+=1
                for line in table:
                    cells=split_markdown_table_row(line)
                    if cells and all(re.fullmatch(r":?-{3,}:?", cell or "---") for cell in cells):
                        continue
                    widget.insert(tk.END,"  │  ".join(cells)+"\n",("detail_table",))
                widget.insert(tk.END,"\n",("detail_body",)); continue
            lm=LIST_RE.match(raw)
            if lm:
                indent,_bullet,content=lm.groups(); level=max(0,len(indent.replace("\t","    "))//2)
                widget.insert(tk.END,"  "*level+"• ",( "detail_list",))
                self._insert_detail_inline_markdown(widget,content,("detail_list",),rel_path)
                widget.insert(tk.END,"\n",("detail_list",)); i+=1; continue
            om=ORDERED_LIST_RE.match(raw)
            if om:
                indent,num,content=om.groups(); level=max(0,len(indent.replace("\t","    "))//2)
                widget.insert(tk.END,"  "*level+num+". ",( "detail_list",))
                self._insert_detail_inline_markdown(widget,content,("detail_list",),rel_path)
                widget.insert(tk.END,"\n",("detail_list",)); i+=1; continue
            if raw.strip().startswith(">"):
                quote=re.sub(r"^\s*>\s?","",raw)
                self._insert_detail_inline_markdown(widget,quote,("detail_quote",),rel_path)
                widget.insert(tk.END,"\n",("detail_quote",)); i+=1; continue
            if raw.strip() in {"---","***","___"}:
                widget.insert(tk.END,"─"*72+"\n",("detail_rule",)); i+=1; continue
            self._insert_detail_inline_markdown(widget,raw,("detail_body",),rel_path)
            widget.insert(tk.END,"\n",("detail_body",)); i+=1

    def _insert_detail_inline_markdown(self, widget: tk.Text, text: str, base_tags: Tuple[str,...], rel_path: Optional[str]) -> None:
        token_re=re.compile(r"(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*\n]+\*)")
        pos=0
        for match in token_re.finditer(text):
            if match.start()>pos:
                widget.insert(tk.END,text[pos:match.start()],base_tags)
            token=match.group(0); link=MARKDOWN_LINK_RE.fullmatch(token)
            if link:
                label,target=link.groups(); tag=f"detail_link_{id(widget)}_{widget.index(tk.END).replace('.', '_')}"
                widget.insert(tk.END,label,base_tags+("detail_link",tag))
                widget.tag_bind(tag,"<Button-1>",lambda _event,t=target,p=rel_path:self._open_detail_link(t,p))
                widget.tag_bind(tag,"<Enter>",lambda _event,w=widget:w.configure(cursor="hand2"))
                widget.tag_bind(tag,"<Leave>",lambda _event,w=widget:w.configure(cursor="xterm"))
            elif token.startswith("`"):
                widget.insert(tk.END,token[1:-1],base_tags+("detail_inline_code",))
            elif token.startswith("***"):
                widget.insert(tk.END,token[3:-3],base_tags+("detail_bold","detail_italic"))
            elif token.startswith("**"):
                widget.insert(tk.END,token[2:-2],base_tags+("detail_bold",))
            elif token.startswith("*"):
                widget.insert(tk.END,token[1:-1],base_tags+("detail_italic",))
            pos=match.end()
        if pos<len(text):
            widget.insert(tk.END,text[pos:],base_tags)

    def _open_detail_link(self, raw_target: str, source_path: Optional[str]) -> None:
        target=raw_target.strip(); parsed=urlparse(target)
        if parsed.scheme in {"http","https","mailto"}:
            webbrowser.open(target); return
        path_part,_,anchor=target.partition("#")
        if path_part:
            base_dir=posixpath.dirname(source_path or "")
            resolved=normalize_rel_path(posixpath.normpath(posixpath.join(base_dir,unquote(path_part))))
        else:
            resolved=source_path or ""
        if resolved and self.app.corpus is not None and resolved in self.app.corpus.documents:
            self.app.open_document(resolved,anchor_name=unquote(anchor) if anchor else None)
            self.app.deiconify(); self.app.lift()

    def redraw(self) -> None:
        if not hasattr(self,"canvas"): return
        self.canvas.delete("all"); width=max(1,self.canvas.winfo_width()); height=max(1,self.canvas.winfo_height()); self.projected.clear()
        for node in self.nodes: self.projected[node.node_id]=self._project(node,width,height)
        palette=self.theme_palette()
        for a,b in self.edges:
            if a not in self.projected or b not in self.projected: continue
            sx,sy,_,_=self.projected[a]; tx,ty,_,_=self.projected[b]; kind=self.edge_kinds.get((a,b),"declared")
            kwargs={"fill":palette["edge"],"width":1.4,"arrow":tk.LAST,"arrowshape":(7,9,3)}
            if kind=="blocked": kwargs.update({"dash":(5,4),"width":2})
            self.canvas.create_line(sx,sy,tx,ty,**kwargs)
        for node in self.nodes:
            sx,sy,_,scale=self.projected[node.node_id]; radius=self._node_radius(node.kind)*max(.7,min(1.35,scale)); fill,outline=self._node_colors(node)
            if node.node_id==self.selected_node_id: self.canvas.create_oval(sx-radius-5,sy-radius-5,sx+radius+5,sy+radius+5,outline=palette["selected_ring"],width=3)
            elif node.node_id==self.hovered_node_id: self.canvas.create_oval(sx-radius-4,sy-radius-4,sx+radius+4,sy+radius+4,outline=palette["hover_ring"],width=2)
            self.canvas.create_oval(sx-radius,sy-radius,sx+radius,sy+radius,fill=fill,outline=outline,width=1)
            if self.labels_var.get(): self._draw_node_label(node,sx,sy+radius+10)
        self.canvas.create_text(12,12,anchor=tk.NW,text=self.view_var.get(),fill=palette["title_fg"],font=("Segoe UI",12,"bold"))
        self.canvas.create_text(12,34,anchor=tk.NW,text=f"{len(self.nodes)} nodes • {len(self.edges)} declared edges",fill=palette["muted_fg"],font=("Segoe UI",9))
        self.canvas.create_text(12,54,anchor=tk.NW,text="2D audit view — no geometric VECTOR semantics",fill=palette["muted_fg"],font=("Segoe UI",9,"italic"))

    def _draw_node_label(self,node:GraphNode,x:float,y:float)->None:
        palette=self.theme_palette(); font_size=8 if node.kind=="case" else 9
        tid=self.canvas.create_text(x,y,text=node.label,fill=palette["label_fg"],font=("Segoe UI",font_size,"bold"),justify=tk.CENTER,width=170)
        bbox=self.canvas.bbox(tid)
        if bbox:
            l,t,r,b=bbox; rid=self.canvas.create_rectangle(l-5,t-3,r+5,b+3,fill=palette["label_bg"],outline=palette["label_border"],width=1); self.canvas.tag_lower(rid,tid)

    def _project(self,node:GraphNode,width:int,height:int)->Tuple[float,float,float,float]:
        h_spacing = self.h_spacing_var.get()
        v_spacing = self.v_spacing_var.get()
        return (
            width/2 + self.pan_x + node.x * self.zoom * h_spacing,
            height/2 + self.pan_y + node.y * self.zoom * v_spacing,
            0.0,
            self.zoom,
        )

    @staticmethod
    def _node_radius(kind:str)->float:
        return {"root":27,"group":22,"core":18,"state":17,"supporting":17,"reduced":18,"pressure":13,"case":9,"result":15,"claim":17,"condition":16,"warrant":16,"rotation":16,"boundary":15,"prior":16,"warning":24,"falsifier":15,"external_relation":14,"derived_relation":14,"relation":14}.get(kind,14)

    @staticmethod
    def _node_colors(node:GraphNode)->Tuple[str,str]:
        palette={"root":("#7357b5","#d8c9ff"),"group":("#526b7a","#c8d8e2"),"core":("#2376c9","#9dcfff"),"state":("#4c7f9f","#b8dcf2"),"supporting":("#8f6b32","#e8cf9b"),"reduced":("#a45c3c","#f2b699"),"pressure":("#8d4a9c","#e1b5eb"),"case":("#2b9b70","#9ce7c8"),"result":("#b67816","#ffd38a"),"claim":("#8f5e3b","#f2c6a3"),"condition":("#4c7f9f","#b8dcf2"),"warrant":("#7357b5","#c9b6f7"),"rotation":("#2376c9","#9dcfff"),"boundary":("#b64646","#ffb0b0"),"prior":("#68798b","#c7d2de"),"warning":("#b64646","#ffb0b0")}
        return palette.get(node.kind,("#68798b","#c7d2de"))

    def _nearest_node(self,x:int,y:int,threshold:float=28)->Optional[GraphNode]:
        nearest=None; best=threshold*threshold
        for nid,(sx,sy,_,_) in self.projected.items():
            d=(sx-x)**2+(sy-y)**2
            if d<best: best=d; nearest=self.node_by_id.get(nid)
        return nearest

    def _on_press(self,event:tk.Event)->None:
        self._drag_start=(event.x,event.y); self._drag_moved=False
    def _on_drag(self,event:tk.Event)->None:
        if self._drag_start is None:return
        dx=event.x-self._drag_start[0]; dy=event.y-self._drag_start[1]
        if abs(dx)+abs(dy)>2:self._drag_moved=True
        self.pan_x+=dx; self.pan_y+=dy; self._drag_start=(event.x,event.y); self.canvas.configure(cursor="fleur"); self.redraw()
    def _on_release(self,event:tk.Event)->None:
        self.canvas.configure(cursor="arrow")
        if not self._drag_moved:
            node=self._nearest_node(event.x,event.y)
            if node:self.select_node_by_id(node.node_id)
            else:self.selected_node_id=""; self._show_general_details(self._view_description(self.view_var.get())); self.redraw()
        self._drag_start=None
    def _on_motion(self,event:tk.Event)->None:
        if self._drag_start is not None and self._drag_moved:return
        node=self._nearest_node(event.x,event.y); nid=node.node_id if node else ""
        if nid!=self.hovered_node_id:
            self.hovered_node_id=nid; self.canvas.configure(cursor="hand2" if node else "arrow")
            self.status_var.set((f"{node.label.replace(chr(10),' / ')} • click for details" if node else "Left-drag to pan • wheel to zoom • click a node for details")); self.redraw()
    def _on_leave(self,event:tk.Event)->None:
        if self.hovered_node_id:self.hovered_node_id=""; self.canvas.configure(cursor="arrow"); self.redraw()
    def _on_double_click(self,event:tk.Event)->None:
        node=self._nearest_node(event.x,event.y)
        if node and node.rel_path and self.app.corpus and node.rel_path in self.app.corpus.documents:
            self.app.open_document(node.rel_path); self.app.deiconify(); self.app.lift()
    def _on_wheel(self,event:tk.Event)->str:
        self._zoom_by(1.12 if event.delta>0 else .89); return "break"
    def _zoom_by(self,factor:float)->None:
        self.zoom=max(.35,min(3.0,self.zoom*factor)); self.redraw()

    def theme_palette(self)->Dict[str,str]:
        if self.app.dark_mode:
            return {"window_bg":"#151617","panel_bg":"#191c1f","fg":"#d4d4d4","muted_fg":"#98a6b5","canvas_bg":"#0d1117","text_bg":"#101214","text_fg":"#d4d4d4","input_bg":"#202428","button_bg":"#30343a","button_hover_bg":"#3b4148","selection_bg":"#3a5068","edge":"#526173","selected_ring":"#f4c95d","hover_ring":"#7cc7ff","label_bg":"#1b222b","label_border":"#556273","label_fg":"#eef3f8","title_fg":"#d7e0ea"}
        return {"window_bg":"#f0f0f0","panel_bg":"#ffffff","fg":"#111111","muted_fg":"#5a6875","canvas_bg":"#f5f7fa","text_bg":"#ffffff","text_fg":"#111111","input_bg":"#ffffff","button_bg":"#eceff2","button_hover_bg":"#dfe5ea","selection_bg":"#cde8ff","edge":"#8997a5","selected_ring":"#a46a00","hover_ring":"#1769aa","label_bg":"#f7f9fb","label_border":"#9aa8b6","label_fg":"#15202b","title_fg":"#15202b"}

    def apply_theme(self)->None:
        palette=self.theme_palette(); self.configure(background=palette["window_bg"]); self.canvas.configure(background=palette["canvas_bg"])
        style=ttk.Style(self); style.configure("Graph.TCombobox",fieldbackground=palette["input_bg"],background=palette["button_bg"],foreground=palette["fg"],arrowcolor=palette["fg"],selectbackground=palette["selection_bg"],selectforeground=palette["fg"])
        style.map("Graph.TCombobox",fieldbackground=[("readonly",palette["input_bg"])],foreground=[("readonly",palette["fg"])],background=[("active",palette["button_hover_bg"]),("readonly",palette["button_bg"])])
        style.configure("Graph.TNotebook",background=palette["window_bg"],borderwidth=0); style.configure("Graph.TNotebook.Tab",background=palette["button_bg"],foreground=palette["fg"],padding=(10,6))
        style.map("Graph.TNotebook.Tab",background=[("selected",palette["panel_bg"]),("active",palette["button_hover_bg"])])
        for widget in self._detail_texts.values(): widget.configure(background=palette["text_bg"],foreground=palette["text_fg"],insertbackground=palette["text_fg"],selectbackground=palette["selection_bg"]); self._configure_detail_tags(widget)
        if self.browser_dialog is not None and self.browser_dialog.winfo_exists(): self.browser_dialog.apply_theme()
        self.redraw()


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class PmsVectorReaderApp(tk.Tk):
    """Tkinter desktop app for browsing the PMS-VECTOR corpus."""

    def __init__(self, initial_source: Optional[Path] = None):
        super().__init__()
        dbg("App: __init__ start")
        self.title(f"{APP_TITLE} {APP_VERSION}")
        self.geometry("1380x860")
        self.minsize(960, 600)

        # Ignore SIGINT so Ctrl+C in the console cannot interrupt Tk callbacks.
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        self.corpus: Optional[Corpus] = None
        self.current_path: Optional[str] = None
        self.heading_indices: Dict[str, str] = {}
        self.search_results: List[Tuple[str, int, str]] = []
        self._file_item_to_path: Dict[str, str] = {}
        self._heading_item_to_anchor: Dict[str, str] = {}
        self._heading_anchor_to_item: Dict[str, str] = {}
        self._document_anchor_indices: Dict[str, str] = {}
        self._heading_positions: List[Tuple[int, str, str]] = []
        self._search_entry: Optional[ttk.Entry] = None
        self.dark_mode = False
        self.graph_lab: Optional[GraphLab] = None

        self.reader_font_size = 10
        self.reader_fullscreen = False
        self._normal_geometry = ""

        # Guard against recursive Treeview callbacks:
        # programmatic selection_set() also emits <<TreeviewSelect>>.
        self._suppress_file_select_event = False
        self._suppress_heading_select_event = False
        self._heading_sync_after_id: Optional[str] = None
        self._manual_heading_until = 0.0

        self._link_tags: List[str] = []
        self._next_link_id = 0
        self._embedded_tables: List[tk.Widget] = []
        self._table_resize_after_id: Optional[str] = None
        self._embedded_images: List[EmbeddedImage] = []
        self._image_resize_after_id: Optional[str] = None

        # Queue for results coming back from the background loader thread.
        self._load_queue: queue.Queue = queue.Queue()

        # Separate queue and generation counter for document rendering. Opening
        # another file invalidates any delayed blocks from the previous file.
        self._render_queue: queue.Queue = queue.Queue()
        self._render_generation = 0
        self._render_poll_after_id: Optional[str] = None
        self._active_render_doc: Optional[Document] = None
        self._active_render_mode = ""
        self._active_render_total = 0
        self._active_render_inserted = 0
        self._chunk_heading_counter = 0
        self._pending_line_number: Optional[int] = None
        self._pending_anchor: Optional[str] = None

        self._configure_style()
        self._build_ui()
        self._bind_shortcuts()
        self.apply_theme()
        self._configure_clickable_cursors()
        self._center_window()
        self.after_idle(self._set_initial_pane_positions)

        dbg("App: UI built, scheduling background load")

        # Kick off corpus loading in a background thread after the window paints.
        if initial_source is not None:
            self.after(100, lambda: self._start_load_thread(initial_source))
        else:
            self.after(100, self._start_discover_thread)

        dbg("App: __init__ done")

    # ------------------------------------------------------------------ #
    # Background loading — worker thread, NO Tk calls allowed here       #
    # ------------------------------------------------------------------ #

    def _start_discover_thread(self) -> None:
        dbg("App: starting discover thread")
        self.status_var.set("Searching for PMS-VECTOR corpus ...")
        t = threading.Thread(target=self._bg_discover, daemon=True)
        t.start()
        self.after(100, self._poll_load_queue)

    def _start_load_thread(self, source_path: Path) -> None:
        dbg(f"App: starting load thread for {source_path}")
        self.status_var.set(f"Loading corpus from {source_path} ...")
        t = threading.Thread(target=self._bg_load, args=(source_path,), daemon=True)
        t.start()
        self.after(100, self._poll_load_queue)

    def _bg_discover(self) -> None:
        """Runs in background thread: discover source path, then load."""
        try:
            dbg("bg_discover: searching ...")
            source_path = discover_default_source()
            if source_path is None:
                dbg("bg_discover: no source found")
                self._load_queue.put(("no_source", None))
                return
            dbg(f"bg_discover: found {source_path}, loading ...")
            self._bg_load(source_path)
        except Exception as exc:
            dbg(f"bg_discover: exception: {exc}")
            self._load_queue.put(("error", str(exc)))

    def _bg_load(self, source_path: Path) -> None:
        """Runs in background thread: create CorpusSource + Corpus."""
        try:
            dbg(f"bg_load: creating CorpusSource({source_path})")
            source = CorpusSource(source_path)
            dbg("bg_load: creating Corpus")
            corpus = Corpus(source)
            dbg("bg_load: done, posting result to queue")
            self._load_queue.put(("ok", corpus))
        except Exception as exc:
            dbg(f"bg_load: exception: {exc}")
            self._load_queue.put(("error", str(exc)))

    def _poll_load_queue(self) -> None:
        """Called periodically in the Tk thread to receive background results."""
        try:
            msg, payload = self._load_queue.get_nowait()
        except queue.Empty:
            # Nothing yet — reschedule.
            self.after(100, self._poll_load_queue)
            return

        dbg(f"poll_load_queue: received '{msg}'")

        if msg == "ok":
            corpus: Corpus = payload
            if self.corpus is not None:
                self.corpus.source.close()
            self.corpus = corpus
            self.current_path = None
            self.populate_file_tree()
            self.clear_search()
            self.status_var.set(
                f"Loaded {corpus.document_count} documents, "
                f"{corpus.total_line_count:,} lines, "
                f"{corpus.total_word_count:,} words — {corpus.source.describe()}"
            )
            self.open_home()

        elif msg == "no_source":
            self.status_var.set("Open a PMS-VECTOR folder or zip file to begin.")

        elif msg == "error":
            error_msg: str = payload
            self.status_var.set(f"Error: {error_msg}")
            messagebox.showerror(APP_TITLE, error_msg)

        else:
            dbg(f"poll_load_queue: unknown message '{msg}'")

    # ------------------------------------------------------------------ #
    # Style / UI construction                                            #
    # ------------------------------------------------------------------ #

    def _configure_style(self) -> None:
        self.base_font = tkfont.Font(family="Segoe UI", size=10)
        self.bold_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.italic_font = tkfont.Font(family="Segoe UI", size=10, slant="italic")
        self.bold_italic_font = tkfont.Font(family="Segoe UI", size=10, weight="bold", slant="italic")
        self.mono_font = tkfont.Font(family="Consolas", size=10)
        self.heading_font_1 = tkfont.Font(family="Segoe UI", size=18, weight="bold")
        self.heading_font_2 = tkfont.Font(family="Segoe UI", size=15, weight="bold")
        self.heading_font_3 = tkfont.Font(family="Segoe UI", size=13, weight="bold")
        self.heading_font_4 = tkfont.Font(family="Segoe UI", size=11, weight="bold")

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)
        style.configure("TButton", padding=(8, 4))
        style.configure("TEntry", padding=(4, 4))

    def _build_ui(self) -> None:
        self._build_toolbar()
        self._build_fullscreen_toolbar()

        self.main_pane = tk.PanedWindow(
            self,
            orient=tk.HORIZONTAL,
            borderwidth=0,
            sashwidth=7,
            sashrelief=tk.RAISED,
            showhandle=True,
            handlesize=11,
            handlepad=2,
            opaqueresize=True,
        )
        self.main_pane.pack(fill=tk.BOTH, expand=True)

        self.left_pane = tk.PanedWindow(
            self.main_pane,
            orient=tk.VERTICAL,
            borderwidth=0,
            sashwidth=7,
            sashrelief=tk.RAISED,
            showhandle=True,
            handlesize=11,
            handlepad=2,
            opaqueresize=True,
        )
        self.main_pane.add(self.left_pane, minsize=230, stretch="always")

        # File tree
        self.file_frame = ttk.Frame(self.left_pane, padding=(6, 6, 6, 3), style="Navigation.TFrame")
        self.left_pane.add(self.file_frame, minsize=160, stretch="always")
        self.file_title_label = ttk.Label(
            self.file_frame,
            text="Corpus",
            font=("Segoe UI", 10, "bold"),
            style="NavigationTitle.TLabel",
        )
        self.file_title_label.pack(anchor=tk.W)

        file_tree_wrap = ttk.Frame(self.file_frame, style="Navigation.TFrame")
        file_tree_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.file_tree = ttk.Treeview(file_tree_wrap, show="tree", style="Corpus.Treeview")
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        file_scrollbar = ttk.Scrollbar(file_tree_wrap, orient=tk.VERTICAL, command=self.file_tree.yview)
        file_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_tree.configure(yscrollcommand=file_scrollbar.set)

        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_selected)
        self._bind_mousewheel_scroll(self.file_tree)

        # Search results
        self.search_frame = ttk.Frame(self.left_pane, padding=(6, 3, 6, 6), style="Navigation.TFrame")
        self.left_pane.add(self.search_frame, minsize=120, stretch="always")
        self.search_title_label = ttk.Label(
            self.search_frame,
            text="Search Results",
            font=("Segoe UI", 10, "bold"),
            style="NavigationTitle.TLabel",
        )
        self.search_title_label.pack(anchor=tk.W)

        search_tree_wrap = ttk.Frame(self.search_frame, style="Navigation.TFrame")
        search_tree_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.search_tree = ttk.Treeview(
            search_tree_wrap,
            show="headings",
            columns=("file", "line", "text"),
            height=9,
            style="Search.Treeview",
        )
        self.search_tree.heading("file", text="File")
        self.search_tree.heading("line", text="Line")
        self.search_tree.heading("text", text="Text")
        self.search_tree.column("file", width=130, stretch=False)
        self.search_tree.column("line", width=48, stretch=False, anchor=tk.E)
        self.search_tree.column("text", width=260, stretch=True)
        self.search_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        search_scrollbar = ttk.Scrollbar(search_tree_wrap, orient=tk.VERTICAL, command=self.search_tree.yview)
        search_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.search_tree.configure(yscrollcommand=search_scrollbar.set)

        self.search_tree.bind("<Double-1>", self._on_search_result_open)
        self.search_tree.bind("<Return>", self._on_search_result_open)
        self._bind_mousewheel_scroll(self.search_tree)

        # Heading tree
        self.heading_frame = ttk.Frame(
            self.main_pane,
            padding=(6, 6, 6, 6),
            style="HeadingPane.TFrame",
        )
        self.main_pane.add(self.heading_frame, minsize=200, stretch="always")
        self.heading_title_label = ttk.Label(
            self.heading_frame,
            text="Headings",
            font=("Segoe UI", 10, "bold"),
            style="HeadingTitle.TLabel",
        )
        self.heading_title_label.pack(anchor=tk.W)

        heading_tree_wrap = ttk.Frame(self.heading_frame, style="HeadingPane.TFrame")
        heading_tree_wrap.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.heading_tree = ttk.Treeview(heading_tree_wrap, show="tree", style="Heading.Treeview")
        self.heading_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        heading_scrollbar = ttk.Scrollbar(heading_tree_wrap, orient=tk.VERTICAL, command=self.heading_tree.yview)
        heading_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.heading_tree.configure(yscrollcommand=heading_scrollbar.set)

        self.heading_tree.bind("<<TreeviewSelect>>", self._on_heading_selected)
        self._bind_mousewheel_scroll(self.heading_tree)

        # Document content
        self.content_frame = ttk.Frame(
            self.main_pane,
            padding=(8, 6, 8, 6),
            style="DocumentPane.TFrame",
        )
        self.main_pane.add(self.content_frame, minsize=420, stretch="always")

        self.document_label_var = tk.StringVar(value="No document loaded")
        self.document_label = ttk.Label(
            self.content_frame,
            textvariable=self.document_label_var,
            font=("Segoe UI", 11, "bold"),
            style="DocumentTitle.TLabel",
        )
        self.document_label.pack(anchor=tk.W, padx=(2, 0), pady=(0, 4))

        # Reader border: same visual discipline as the side panes.
        self.reader_border = tk.Frame(
            self.content_frame,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#cfcfcf",
            highlightcolor="#cfcfcf",
        )
        self.reader_border.pack(fill=tk.BOTH, expand=True)

        text_wrap = tk.Frame(self.reader_border, borderwidth=0, highlightthickness=0)
        self.text_wrap = text_wrap
        text_wrap.pack(fill=tk.BOTH, expand=True)

        self.text = tk.Text(
            text_wrap,
            wrap=tk.WORD,
            undo=False,
            padx=18,
            pady=14,
            font=self.base_font,
            borderwidth=0,
            highlightthickness=0,
        )
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.text_yscroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.text.yview)
        self.text_yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.configure(yscrollcommand=self._on_text_yscroll)
        self.text.bind("<Configure>", self._on_text_configure)

        self._configure_text_tags()

        self.status_var = tk.StringVar(value="Starting ...")
        self.status_label = ttk.Label(
            self,
            textvariable=self.status_var,
            anchor=tk.W,
            padding=(6, 3),
            style="Status.TLabel",
        )
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)

    def _build_toolbar(self) -> None:
        self.toolbar = ttk.Frame(self, padding=(6, 6, 6, 3))
        self.toolbar.pack(fill=tk.X)

        ttk.Label(self.toolbar, text="Search").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self._search_entry = ttk.Entry(self.toolbar, textvariable=self.search_var, width=42)
        self._search_entry.pack(side=tk.LEFT, padx=(6, 4))
        self._search_entry.bind("<Return>", lambda event: self.run_search())
        ttk.Button(self.toolbar, text="Search", command=self.run_search).pack(side=tk.LEFT)
        ttk.Button(self.toolbar, text="Clear", command=self.clear_search).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Button(self.toolbar, text="Reload", command=self.reload_source).pack(side=tk.LEFT)
        ttk.Button(self.toolbar, text="Home", command=self.open_home).pack(side=tk.LEFT, padx=(6, 12))

        ttk.Button(self.toolbar, text="A−", command=self.decrease_reader_font).pack(side=tk.LEFT)
        ttk.Button(self.toolbar, text="A+", command=self.increase_reader_font).pack(side=tk.LEFT, padx=(4, 12))

        self.fullscreen_button = ttk.Button(
            self.toolbar,
            text="Reader Fullscreen",
            command=self.toggle_reader_fullscreen,
        )
        self.fullscreen_button.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(self.toolbar, text="Graph Lab", command=self.open_graph_lab).pack(side=tk.LEFT, padx=(0, 8))

        self.theme_button = ttk.Button(self.toolbar, text="Dark Mode", command=self.toggle_dark_mode)
        self.theme_button.pack(side=tk.LEFT)

        ttk.Button(self.toolbar, text="Exit", command=self.destroy).pack(side=tk.RIGHT)
        ttk.Button(self.toolbar, text="Help", command=self.show_help).pack(side=tk.RIGHT, padx=(0, 6))

    def _build_fullscreen_toolbar(self) -> None:
        self.fullscreen_toolbar = ttk.Frame(self, padding=(8, 6, 8, 4))

        ttk.Button(
            self.fullscreen_toolbar,
            text="A−",
            command=self.decrease_reader_font,
        ).pack(side=tk.LEFT)

        ttk.Button(
            self.fullscreen_toolbar,
            text="A+",
            command=self.increase_reader_font,
        ).pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(
            self.fullscreen_toolbar,
            text="Reader Fullscreen",
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.LEFT)

        ttk.Button(
            self.fullscreen_toolbar,
            text="Exit Fullscreen",
            command=self.exit_reader_fullscreen,
        ).pack(side=tk.RIGHT)

        ttk.Button(
            self.fullscreen_toolbar,
            text="Help",
            command=self.show_help,
        ).pack(side=tk.RIGHT, padx=(0, 6))

    def _configure_text_tags(self) -> None:
        self.text.tag_configure("h1", font=self.heading_font_1, spacing1=22, spacing3=12, lmargin1=14, lmargin2=14)
        self.text.tag_configure("h2", font=self.heading_font_2, spacing1=20, spacing3=10, lmargin1=14, lmargin2=14)
        self.text.tag_configure("h3", font=self.heading_font_3, spacing1=16, spacing3=8, lmargin1=14, lmargin2=14)
        self.text.tag_configure("h4", font=self.heading_font_4, spacing1=14, spacing3=7, lmargin1=14, lmargin2=14)
        self.text.tag_configure("h5", font=self.heading_font_4, spacing1=12, spacing3=6, lmargin1=14, lmargin2=14)
        self.text.tag_configure("h6", font=self.heading_font_4, spacing1=12, spacing3=6, lmargin1=14, lmargin2=14)

        self.text.tag_configure("body", font=self.base_font, lmargin1=8, lmargin2=8)
        self.text.tag_configure("bold", font=self.bold_font)
        self.text.tag_configure("italic", font=self.italic_font)
        self.text.tag_configure("bold_italic", font=self.bold_italic_font)
        self.text.tag_configure("list", font=self.base_font, lmargin1=28, lmargin2=46, spacing1=1, spacing3=1)
        self.text.tag_configure("code", font=self.mono_font, background="#f4f4f4", lmargin1=28, lmargin2=28, spacing1=8, spacing3=8)
        self.text.tag_configure("inline_code", font=self.mono_font, background="#f4f4f4")
        self.text.tag_configure("yaml_key", font=self.mono_font, background="#f4f4f4", foreground="#7a3e9d", lmargin1=28, lmargin2=28)
        self.text.tag_configure("yaml_value", font=self.mono_font, background="#f4f4f4", foreground="#555555", lmargin1=28, lmargin2=28)
        self.text.tag_configure("quote", lmargin1=24, lmargin2=24, foreground="#555555")
        self.text.tag_configure("table", font=self.mono_font, lmargin1=24, lmargin2=24, spacing1=4, spacing3=4)
        self.text.tag_configure("rule", foreground="#777777")
        self.text.tag_configure("image_block", justify=tk.CENTER, spacing1=8, spacing3=8)
        self.text.tag_configure("search", background="#fff2a8")
        self.text.tag_configure("current_line", background="#eef5ff")
        self.text.tag_configure("link", foreground="#0563c1", underline=True)
        self.text.tag_configure("link_hover", background="#dceeff")
        self.text.tag_configure(
            "loading",
            font=("Segoe UI", 14, "bold"),
            justify=tk.CENTER,
            spacing1=180,
            spacing3=20,
        )

    def toggle_dark_mode(self) -> None:
        self.dark_mode = not self.dark_mode
        self.apply_theme()

    def apply_theme(self) -> None:
        if self.dark_mode:
            bg = "#151617"
            navigation_bg = "#1c1e20"
            heading_bg = "#202326"
            document_panel_bg = "#17191b"
            panel_bg = navigation_bg
            fg = "#d4d4d4"
            muted_fg = "#a0a0a0"
            text_bg = "#101214"
            text_fg = "#d4d4d4"
            code_bg = "#1b1e21"
            button_bg = "#333333"
            button_hover_bg = "#404040"
            selection_bg = "#3a3d41"
            current_line_bg = "#2a2d2e"
            search_bg = "#665c00"
            rule_fg = "#777777"
            yaml_key_fg = "#ce9178"
            yaml_value_fg = "#b5cea8"
            reader_border_fg = "#34383c"
            link_fg = "#6cb6ff"
            link_hover_bg = "#24384a"
            self.theme_button.configure(text="Light Mode")
        else:
            bg = "#f0f0f0"
            navigation_bg = "#f2f4f6"
            heading_bg = "#f7f8fa"
            document_panel_bg = "#ffffff"
            panel_bg = navigation_bg
            fg = "#000000"
            muted_fg = "#555555"
            text_bg = "#ffffff"
            text_fg = "#000000"
            code_bg = "#f4f4f4"
            button_bg = "#f0f0f0"
            button_hover_bg = "#e5e5e5"
            selection_bg = "#cde8ff"
            current_line_bg = "#eef5ff"
            search_bg = "#fff2a8"
            rule_fg = "#777777"
            yaml_key_fg = "#7a3e9d"
            yaml_value_fg = "#555555"
            reader_border_fg = "#cfcfcf"
            link_fg = "#0563c1"
            link_hover_bg = "#dceeff"
            self.theme_button.configure(text="Dark Mode")

        self.configure(background=bg)

        # Native paned windows keep resize handles visible in both themes.
        sash_bg = "#55595e" if self.dark_mode else "#9aa1a8"
        for pane in (getattr(self, "main_pane", None), getattr(self, "left_pane", None)):
            if pane is not None:
                pane.configure(
                    background=sash_bg,
                    sashrelief=tk.RAISED,
                    sashwidth=7,
                    showhandle=True,
                    handlesize=11,
                    handlepad=2,
                )

        style = ttk.Style(self)
        style.configure(".", background=bg, foreground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Navigation.TFrame", background=navigation_bg)
        style.configure("HeadingPane.TFrame", background=heading_bg)
        style.configure("DocumentPane.TFrame", background=document_panel_bg)
        style.configure("NavigationTitle.TLabel", background=navigation_bg, foreground=fg)
        style.configure("HeadingTitle.TLabel", background=heading_bg, foreground=fg)
        style.configure("DocumentTitle.TLabel", background=document_panel_bg, foreground=fg)
        style.configure("Status.TLabel", background=bg, foreground=muted_fg)
        style.configure("Table.TFrame", background=text_bg)
        style.configure("TButton", background=button_bg, foreground=fg)
        style.map(
            "TButton",
            background=[
                ("active", button_hover_bg),
                ("pressed", selection_bg),
                ("!active", button_bg),
            ],
            foreground=[
                ("active", fg),
                ("pressed", fg),
                ("!active", fg),
            ],
        )
        style.configure("TEntry", fieldbackground=text_bg, foreground=fg)
        style.configure(
            "Treeview",
            background=panel_bg,
            fieldbackground=panel_bg,
            foreground=fg,
        )
        style.map(
            "Treeview",
            background=[("selected", selection_bg)],
            foreground=[("selected", fg)],
        )
        style.configure("Corpus.Treeview", background=navigation_bg, fieldbackground=navigation_bg, foreground=fg)
        style.configure("Search.Treeview", background=navigation_bg, fieldbackground=navigation_bg, foreground=fg)
        style.configure("Heading.Treeview", background=heading_bg, fieldbackground=heading_bg, foreground=fg)
        style.configure("Data.Treeview", background=text_bg, fieldbackground=text_bg, foreground=text_fg, rowheight=26)
        for tree_style in ("Corpus.Treeview", "Search.Treeview", "Heading.Treeview", "Data.Treeview"):
            style.map(
                tree_style,
                background=[("selected", selection_bg)],
                foreground=[("selected", fg)],
            )
        style.configure("Data.Treeview.Heading", background=button_bg, foreground=fg, relief="flat")
        style.map("Data.Treeview.Heading", background=[("active", button_hover_bg)])

        if hasattr(self, "reader_border"):
            self.reader_border.configure(
                background=reader_border_fg,
                highlightbackground=reader_border_fg,
                highlightcolor=reader_border_fg,
            )

        self.text.configure(
            background=text_bg,
            foreground=text_fg,
            insertbackground=text_fg,
        )
        if hasattr(self, "text_wrap"):
            self.text_wrap.configure(background=text_bg)

        for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.text.tag_configure(tag, background=text_bg, foreground=text_fg)

        self.text.tag_configure("body", background=text_bg, foreground=text_fg)
        self.text.tag_configure("bold", background=text_bg, foreground=text_fg)
        self.text.tag_configure("italic", background=text_bg, foreground=text_fg)
        self.text.tag_configure("bold_italic", background=text_bg, foreground=text_fg)
        self.text.tag_configure("list", background=text_bg, foreground=text_fg)
        self.text.tag_configure("code", background=code_bg, foreground=text_fg)
        self.text.tag_configure("inline_code", background=code_bg, foreground=text_fg)
        self.text.tag_configure("yaml_key", background=code_bg, foreground=yaml_key_fg)
        self.text.tag_configure("yaml_value", background=code_bg, foreground=yaml_value_fg)
        self.text.tag_configure("quote", background=text_bg, foreground=muted_fg)
        self.text.tag_configure("table", background=text_bg, foreground=text_fg)
        self.text.tag_configure("rule", background=text_bg, foreground=rule_fg)
        self.text.tag_configure("search", background=search_bg, foreground=text_fg)
        self.text.tag_configure("current_line", background=current_line_bg)
        self.text.tag_configure("link", background=text_bg, foreground=link_fg)
        self.text.tag_configure("link_hover", background=link_hover_bg, foreground=link_fg)
        self.text.tag_configure("loading", background=text_bg, foreground=text_fg)

        for embedded in self._embedded_images:
            try:
                embedded.frame.configure(background=text_bg)
                embedded.image_label.configure(background=text_bg)
                if embedded.caption_label is not None:
                    embedded.caption_label.configure(background=text_bg, foreground=muted_fg)
            except tk.TclError:
                continue

        if self.graph_lab is not None and self.graph_lab.winfo_exists():
            self.graph_lab.apply_theme()

    def _on_text_configure(self, _event: tk.Event) -> None:
        self._schedule_heading_sync()
        if self._table_resize_after_id is not None:
            try:
                self.after_cancel(self._table_resize_after_id)
            except tk.TclError:
                pass
        self._table_resize_after_id = self.after(40, self._resize_embedded_tables)
        if self._image_resize_after_id is not None:
            try:
                self.after_cancel(self._image_resize_after_id)
            except tk.TclError:
                pass
        self._image_resize_after_id = self.after(80, self._resize_embedded_images)

    def _resize_embedded_tables(self) -> None:
        self._table_resize_after_id = None
        available_width = max(320, self.text.winfo_width() - 44)
        live_frames: List[tk.Widget] = []
        for frame in self._embedded_tables:
            try:
                if not frame.winfo_exists():
                    continue
                live_frames.append(frame)
                tree = getattr(frame, "_table_tree", None)
                xscroll = getattr(frame, "_table_xscroll", None)
                if tree is None or xscroll is None:
                    continue
                frame.configure(width=available_width)
                tree.update_idletasks()
                scroll_height = xscroll.winfo_reqheight() if xscroll.winfo_manager() == "grid" else 0
                frame.configure(height=tree.winfo_reqheight() + scroll_height + 4)
            except tk.TclError:
                continue
        self._embedded_tables = live_frames

    def _resize_embedded_images(self) -> None:
        self._image_resize_after_id = None
        available_width = min(
            IMAGE_MAX_DISPLAY_WIDTH,
            max(320, self.text.winfo_width() - 64),
        )
        live_images: List[EmbeddedImage] = []
        for embedded in self._embedded_images:
            try:
                if not embedded.frame.winfo_exists():
                    continue
                live_images.append(embedded)
                factor = max(1, math.ceil(embedded.source_width / available_width))
                if factor != embedded.scale_factor or embedded.displayed_image is None:
                    source_image = tk.PhotoImage(data=embedded.encoded_data)
                    display_image = (
                        source_image
                        if factor == 1
                        else source_image.subsample(factor, factor)
                    )
                    embedded.displayed_image = display_image
                    embedded.scale_factor = factor
                    embedded.image_label.configure(image=display_image)
                if embedded.caption_label is not None:
                    embedded.caption_label.configure(
                        wraplength=max(240, min(available_width, embedded.displayed_image.width()))
                    )
            except tk.TclError as exc:
                dbg(f"image resize skipped for {embedded.rel_path}: {exc}")
        self._embedded_images = live_images

    def _configure_clickable_cursors(self) -> None:
        """Apply consistent interaction cursors without changing semantics."""
        for widget in walk_widgets(self):
            try:
                if isinstance(widget, ttk.Button):
                    widget.configure(cursor="hand2")
                elif isinstance(widget, ttk.Treeview):
                    widget.configure(cursor="hand2")
            except tk.TclError:
                pass
        try:
            self.text.configure(cursor="xterm")
        except tk.TclError:
            pass

    def _set_initial_pane_positions(self) -> None:
        """Keep Corpus, Search Results, Headings, and Reader visible at startup."""
        try:
            self.update_idletasks()
            width = max(self.main_pane.winfo_width(), 960)
            left_width = max(230, min(int(width * 0.19), width - 650))
            heading_width = max(200, min(int(width * 0.17), width - left_width - 440))
            self.main_pane.sash_place(0, left_width, 1)
            self.main_pane.sash_place(1, left_width + heading_width, 1)

            height = max(self.left_pane.winfo_height(), 500)
            corpus_height = max(180, min(int(height * 0.58), height - 140))
            self.left_pane.sash_place(0, 1, corpus_height)
        except (tk.TclError, IndexError):
            pass

    def _bind_mousewheel_scroll(self, widget: tk.Widget) -> None:
        """Make mouse-wheel scrolling work reliably for Treeview-like widgets.

        Tk/ttk scrolling behavior differs between Windows, macOS, and Linux.
        Binding directly to each navigation tree keeps Corpus, Search Results,
        and Headings scrollable even before they have keyboard focus.
        """
        widget.bind("<MouseWheel>", lambda event, w=widget: self._on_mousewheel_scroll(event, w))
        widget.bind("<Button-4>", lambda event, w=widget: self._on_linux_mousewheel_scroll(event, w, -1))
        widget.bind("<Button-5>", lambda event, w=widget: self._on_linux_mousewheel_scroll(event, w, 1))

    def _on_mousewheel_scroll(self, event: tk.Event, widget: tk.Widget) -> str:
        delta = getattr(event, "delta", 0)

        if delta == 0:
            return "break"

        # Windows usually sends +/-120. macOS can send smaller values.
        if abs(delta) >= 120:
            units = -int(delta / 120)
        else:
            units = -1 if delta > 0 else 1

        try:
            widget.yview_scroll(units, "units")
        except tk.TclError:
            pass

        return "break"

    def _on_linux_mousewheel_scroll(self, event: tk.Event, widget: tk.Widget, units: int) -> str:
        try:
            widget.yview_scroll(units, "units")
        except tk.TclError:
            pass

        return "break"

    def _on_text_yscroll(self, first: str, last: str) -> None:
        self.text_yscroll.set(first, last)
        self._schedule_heading_sync()

    def _schedule_heading_sync(self) -> None:
        if self._heading_sync_after_id is not None:
            try:
                self.after_cancel(self._heading_sync_after_id)
            except tk.TclError:
                pass
        self._heading_sync_after_id = self.after(120, self._sync_heading_from_scroll)

    def _refresh_heading_positions(self) -> None:
        positions: List[Tuple[int, str, str]] = []
        for anchor, index in self.heading_indices.items():
            item = self._heading_anchor_to_item.get(anchor)
            if not item:
                continue
            try:
                line_number = int(self.text.index(index).split(".", 1)[0])
            except (tk.TclError, ValueError):
                continue
            positions.append((line_number, item, anchor))
        self._heading_positions = sorted(positions, key=lambda entry: entry[0])

    def _sync_heading_from_scroll(self) -> None:
        self._heading_sync_after_id = None
        if (
            not self._heading_positions
            or self._active_render_doc is not None
            or time.monotonic() < self._manual_heading_until
        ):
            return
        try:
            top_line = int(self.text.index("@0,0").split(".", 1)[0]) + 2
        except (tk.TclError, ValueError):
            return
        lines = [entry[0] for entry in self._heading_positions]
        position = max(0, bisect.bisect_right(lines, top_line) - 1)
        _line, item, _anchor = self._heading_positions[position]
        if self.heading_tree.selection() == (item,):
            return
        self._suppress_heading_select_event = True
        try:
            self.heading_tree.selection_set(item)
            self.heading_tree.focus(item)
            self.heading_tree.see(item)
        finally:
            self.after_idle(self._enable_heading_select_events)

    def _center_window(self) -> None:
        """Center the main window on the current screen after widgets exist."""
        self.update_idletasks()

        width = self.winfo_width()
        height = self.winfo_height()

        if width <= 1 or height <= 1:
            geometry = self.geometry().split("+", 1)[0]
            width_text, height_text = geometry.split("x", 1)
            width = int(width_text)
            height = int(height_text)

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)

        self.geometry(f"{width}x{height}+{x}+{y}")

    def show_help(self) -> None:
        messagebox.showinfo(
            f"{APP_TITLE} Help",
            "PMS-VECTOR Reader controls\n\n"
            "Navigation:\n"
            "  Home                    Open the preferred start document\n"
            "  Reload                  Reload the current corpus\n\n"
            "Search:\n"
            "  Ctrl+F                  Focus search field\n"
            "  Enter                   Search from search field\n"
            "  Double-click result     Open search result\n\n"
            "Reader:\n"
            "  Click link              Open internal links in Reader\n"
            "  External link           Confirm before browser opening\n"
            "  Scroll document         Synchronize active heading\n"
            "  A+ / Ctrl++             Increase reader font size\n"
            "  A− / Ctrl+-             Decrease reader font size\n"
            "  Ctrl+0                  Reset reader font size\n"
            "  F11                     Toggle reader fullscreen\n"
            "  Esc                     Exit reader fullscreen\n\n"
            "Graph Lab:\n"
            "  Architecture & Status   Core / state / supporting / reduced status\n"
            "  Dependency / Warrant    Declared dependencies and blocked jumps\n"
            "  Case Pressure Map       Pressure family → case → result / reduction\n"
            "  Selected Case Trace     Baseline → warrant → pressure → result\n"
            "  Reduction Graph         Declared loss history\n"
            "  Drag                     Pan the 2D audit view\n"
            "  Mouse wheel              Zoom\n"
            "  Double-click node        Open its repository artifact\n\n"
            "Theme:\n"
            "  Dark Mode               Toggle light / dark mode\n\n"
            "The graph layer visualizes declared repository relations only. It does not create theory, evidence, classification, dependencies, warrant, authority, or geometry. Reader self-test checks repository consistency, not VECTOR validity.",
        )

    def increase_reader_font(self) -> None:
        self.set_reader_font_size(self.reader_font_size + 1)

    def decrease_reader_font(self) -> None:
        self.set_reader_font_size(self.reader_font_size - 1)

    def reset_reader_font(self) -> None:
        self.set_reader_font_size(10)

    def set_reader_font_size(self, size: int) -> None:
        self.reader_font_size = max(8, min(24, size))
        self._apply_reader_font_sizes()
        self.status_var.set(f"Reader font size: {self.reader_font_size}")

    def _apply_reader_font_sizes(self) -> None:
        size = self.reader_font_size

        self.base_font.configure(size=size)
        self.bold_font.configure(size=size)
        self.italic_font.configure(size=size)
        self.bold_italic_font.configure(size=size)
        self.mono_font.configure(size=size)

        self.heading_font_1.configure(size=size + 8)
        self.heading_font_2.configure(size=size + 5)
        self.heading_font_3.configure(size=size + 3)
        self.heading_font_4.configure(size=size + 1)

        self.text.configure(font=self.base_font)

    def toggle_reader_fullscreen(self) -> None:
        if self.reader_fullscreen:
            self.exit_reader_fullscreen()
        else:
            self.enter_reader_fullscreen()

    def enter_reader_fullscreen(self) -> None:
        if self.reader_fullscreen:
            return

        self.reader_fullscreen = True
        self._normal_geometry = self.geometry()

        self.toolbar.pack_forget()
        self.status_label.pack_forget()
        self.fullscreen_toolbar.pack(fill=tk.X, before=self.main_pane)

        try:
            self.main_pane.forget(self.left_pane)
        except tk.TclError:
            pass

        try:
            self.main_pane.forget(self.heading_frame)
        except tk.TclError:
            pass

        self.attributes("-fullscreen", True)
        self.fullscreen_button.configure(text="Exit Fullscreen")
        self.text.focus_set()

    def exit_reader_fullscreen(self) -> None:
        if not self.reader_fullscreen:
            return

        self.reader_fullscreen = False
        self.attributes("-fullscreen", False)

        self.fullscreen_toolbar.pack_forget()

        try:
            self.main_pane.forget(self.content_frame)
        except tk.TclError:
            pass

        self.main_pane.add(self.left_pane, minsize=230, stretch="always")
        self.main_pane.add(self.heading_frame, minsize=200, stretch="always")
        self.main_pane.add(self.content_frame, minsize=420, stretch="always")

        self.toolbar.pack(fill=tk.X, before=self.main_pane)
        self.status_label.pack(fill=tk.X, side=tk.BOTTOM)

        if self._normal_geometry:
            self.geometry(self._normal_geometry)

        self.after_idle(self._set_initial_pane_positions)

        self.fullscreen_button.configure(text="Reader Fullscreen")
        self.text.focus_set()

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda event: self._open_folder())
        self.bind("<Control-f>", self._focus_search)
        self.bind("<F5>", lambda event: self.reload_source())
        self.bind("<F1>", lambda event: self.show_help())
        self.bind("<Control-g>", lambda event: self.open_graph_lab())

        self.bind("<Control-plus>", lambda event: self.increase_reader_font())
        self.bind("<Control-equal>", lambda event: self.increase_reader_font())
        self.bind("<Control-KP_Add>", lambda event: self.increase_reader_font())

        self.bind("<Control-minus>", lambda event: self.decrease_reader_font())
        self.bind("<Control-KP_Subtract>", lambda event: self.decrease_reader_font())

        self.bind("<Control-0>", lambda event: self.reset_reader_font())
        self.bind("<F11>", lambda event: self.toggle_reader_fullscreen())
        self.bind("<Escape>", lambda event: self.exit_reader_fullscreen())

    def _focus_search(self, event: tk.Event) -> str:
        if self._search_entry is not None:
            self._search_entry.focus_set()
            self._search_entry.selection_range(0, tk.END)
        return "break"

    # ------------------------------------------------------------------ #
    # Source loading (UI-thread side)                                    #
    # ------------------------------------------------------------------ #

    def load_source(self, source_path: Path) -> None:
        """Called from the UI thread; kicks off a background load."""
        self._start_load_thread(source_path)

    def reload_source(self) -> None:
        if self.corpus is None:
            return
        source_path = self.corpus.source.source_path
        self.load_source(source_path)

    def open_home(self) -> None:
        if self.corpus is None:
            return
        for candidate in PREFERRED_HOME_FILES:
            doc = self.corpus.documents.get(candidate)
            if doc is not None and doc.text.strip():
                self.open_document(candidate)
                return

    def open_graph_lab(self) -> None:
        if self.corpus is None:
            return
        if self.graph_lab is None or not self.graph_lab.winfo_exists():
            self.graph_lab = GraphLab(self)
        else:
            self.graph_lab.deiconify()
            self.graph_lab.lift()
            self.graph_lab.apply_theme()
            self.graph_lab.refresh()
            self.graph_lab.after_idle(self.graph_lab._maximize)

    # ------------------------------------------------------------------ #
    # Tree population                                                    #
    # ------------------------------------------------------------------ #

    def populate_file_tree(self) -> None:
        dbg("populate_file_tree: start")
        self.file_tree.delete(*self.file_tree.get_children())
        self._file_item_to_path.clear()
        if self.corpus is None:
            return

        section_items: Dict[str, str] = {}
        folder_items: Dict[Tuple[str, str], str] = {}

        def nav_sort_key(rel_path: str) -> Tuple[int, Tuple[object, ...]]:
            if rel_path == "cases/README.md":
                return (0, natural_sort_key(rel_path))
            if rel_path == "cases/index.yaml":
                return (1, natural_sort_key(rel_path))
            if rel_path.startswith("cases/"):
                return (2, natural_sort_key(rel_path))
            return (3, corpus_sort_key(rel_path))

        nav_paths = [
            rel_path for rel_path in self.corpus.ordered_paths
            if not rel_path.startswith("reader/")
            and rel_path != "PMS-VECTOR.html"
        ]
        nav_paths.sort(key=nav_sort_key)

        for rel_path in nav_paths:
            section_key = rel_path.split("/", 1)[0] if "/" in rel_path else rel_path
            section_label = SECTION_LABELS.get(section_key, section_key)
            if section_key not in section_items:
                section_items[section_key] = self.file_tree.insert("", tk.END, text=section_label, open=True)

            parent = section_items[section_key]
            parts = rel_path.split("/")
            for depth, folder in enumerate(parts[1:-1], start=1):
                key = (section_key, "/".join(parts[1:depth + 1]))
                if key not in folder_items:
                    folder_items[key] = self.file_tree.insert(parent, tk.END, text=folder, open=(depth < 2))
                parent = folder_items[key]

            doc = self.corpus.documents[rel_path]
            display_title = CANONICAL_BLOCK_LABELS.get(rel_path, doc.title)
            item = self.file_tree.insert(parent, tk.END, text=display_title, open=False)
            self._file_item_to_path[item] = rel_path

        dbg(f"populate_file_tree: inserted {len(self._file_item_to_path)} file items")

    def populate_heading_tree(self, doc: Document) -> None:
        self.heading_tree.delete(*self.heading_tree.get_children())
        self._heading_item_to_anchor.clear()
        self._heading_anchor_to_item.clear()

        parent_by_level: Dict[int, str] = {0: ""}
        for heading in doc.headings:
            parent_level = heading.level - 1
            while parent_level > 0 and parent_level not in parent_by_level:
                parent_level -= 1
            parent = parent_by_level.get(parent_level, "")
            label = f"{'  ' * max(0, heading.level - 1)}{heading.text}"
            item = self.heading_tree.insert(parent, tk.END, text=label, open=True)
            self._heading_item_to_anchor[item] = heading.anchor
            self._heading_anchor_to_item[heading.anchor] = item
            parent_by_level[heading.level] = item
            for deeper in list(parent_by_level):
                if deeper > heading.level:
                    del parent_by_level[deeper]

    # ------------------------------------------------------------------ #
    # Document rendering                                                 #
    # ------------------------------------------------------------------ #

    def open_document(
        self,
        rel_path: str,
        line_number: Optional[int] = None,
        anchor_name: Optional[str] = None,
    ) -> None:
        if self.corpus is None or rel_path not in self.corpus.documents:
            return

        if self.current_path == rel_path:
            if anchor_name:
                self.scroll_to_anchor(anchor_name)
            elif line_number is not None:
                self.scroll_to_source_line(line_number)
            return

        dbg(f"open_document: {rel_path}")
        self._cancel_active_render()
        doc = self.corpus.documents[rel_path]
        self.current_path = rel_path
        self.document_label_var.set(f"{doc.title} — {rel_path}")
        self.populate_heading_tree(doc)
        self._select_file_tree_item(rel_path)
        self._pending_line_number = line_number
        self._pending_anchor = anchor_name

        rendered_now = self.render_document(doc)
        if rendered_now:
            self._finish_document_render(doc)

        if self.graph_lab is not None and self.graph_lab.winfo_exists():
            self.graph_lab.set_current_path(rel_path)

    def _document_status(self, doc: Document) -> str:
        case = self.corpus.case_for_path(doc.rel_path) if self.corpus is not None else None
        case_suffix = f" • {case.case_id} → {case.result_status}" if case else ""
        return (
            f"{doc.rel_path} — {doc.line_count:,} lines, {doc.word_count:,} words, "
            f"{len(doc.headings):,} headings{case_suffix}"
        )

    def _should_chunk_render(self, doc: Document) -> bool:
        return (
            doc.line_count > CHUNKED_RENDER_LINE_THRESHOLD
            or doc.byte_count > CHUNKED_RENDER_BYTE_THRESHOLD
        ) and doc.file_type in {"md", "yaml", "yml", "json", "txt", "py"}

    def render_document(self, doc: Document) -> bool:
        """Render a document and return True when rendering finished synchronously."""
        if self._should_chunk_render(doc):
            self._start_chunked_render(doc)
            return False
        if doc.file_type == "md":
            self.render_markdown(doc)
        elif doc.file_type in {"yaml", "yml"}:
            self.render_yaml(doc)
        elif doc.file_type == "json":
            self.render_json(doc)
        elif doc.file_type == "csv":
            self.render_csv(doc)
        else:
            self.render_plain(doc)
        return True

    def _reset_document_surface(self) -> None:
        for widget in self._embedded_tables:
            try:
                widget.destroy()
            except tk.TclError:
                pass
        self._embedded_tables.clear()

        if self._image_resize_after_id is not None:
            try:
                self.after_cancel(self._image_resize_after_id)
            except tk.TclError:
                pass
            self._image_resize_after_id = None
        for embedded in self._embedded_images:
            try:
                embedded.frame.destroy()
            except tk.TclError:
                pass
        self._embedded_images.clear()

        for tag_name in self._link_tags:
            try:
                self.text.tag_delete(tag_name)
            except tk.TclError:
                pass
        self._link_tags.clear()
        self._next_link_id = 0

        self.heading_indices.clear()
        self._document_anchor_indices.clear()
        self._heading_positions.clear()
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)

    def _cancel_active_render(self) -> None:
        self._render_generation += 1
        self._active_render_doc = None
        self._active_render_mode = ""
        self._active_render_total = 0
        self._active_render_inserted = 0
        self._pending_line_number = None
        self._pending_anchor = None
        if self._render_poll_after_id is not None:
            try:
                self.after_cancel(self._render_poll_after_id)
            except tk.TclError:
                pass
            self._render_poll_after_id = None

    def _show_loading(self, doc: Document) -> None:
        self._reset_document_surface()
        self.heading_tree.state(["disabled"])
        self.text.insert(
            "1.0",
            f"Loading {Path(doc.rel_path).name}…\n\nPreparing seamless audit view.",
            ("loading",),
        )
        self.text.configure(state=tk.DISABLED)
        self.status_var.set(f"Loading {doc.rel_path}…")

    def _start_chunked_render(self, doc: Document) -> None:
        generation = self._render_generation
        self._active_render_doc = doc
        self._active_render_mode = ""
        self._active_render_total = 0
        self._active_render_inserted = 0
        self._chunk_heading_counter = 0
        self._show_loading(doc)
        worker = threading.Thread(
            target=self._prepare_document_chunks,
            args=(generation, doc),
            daemon=True,
        )
        worker.start()
        self._schedule_render_poll()

    def _prepare_document_chunks(self, generation: int, doc: Document) -> None:
        """Prepare document chunks in a worker. Never touches Tk widgets."""
        try:
            if doc.file_type == "md":
                mode = "markdown"
                chunks = chunk_markdown_text(strip_frontmatter(doc.text), CHUNK_TARGET_BYTES)
            elif doc.file_type in {"yaml", "yml"}:
                mode = "yaml_plain"
                chunks = chunk_text_by_bytes(doc.text, CHUNK_TARGET_BYTES)
            else:
                mode = "code" if doc.file_type in {"json", "py"} else "body"
                chunks = chunk_text_by_bytes(doc.text, CHUNK_TARGET_BYTES)

            self._render_queue.put(("start", generation, doc.rel_path, mode, len(chunks)))
            for block_number, chunk in enumerate(chunks, start=1):
                self._render_queue.put(("chunk", generation, doc.rel_path, block_number, chunk))
            self._render_queue.put(("done", generation, doc.rel_path))
        except Exception as exc:
            self._render_queue.put(("error", generation, doc.rel_path, str(exc)))

    def _schedule_render_poll(self) -> None:
        if self._render_poll_after_id is None:
            self._render_poll_after_id = self.after(20, self._poll_render_queue)

    def _poll_render_queue(self) -> None:
        self._render_poll_after_id = None
        processed = 0
        while processed < 4:
            try:
                message = self._render_queue.get_nowait()
            except queue.Empty:
                break
            processed += 1
            kind = message[0]
            generation = message[1]
            rel_path = message[2]
            if generation != self._render_generation or rel_path != self.current_path:
                continue

            if kind == "start":
                _kind, _generation, _path, mode, total = message
                self._active_render_mode = mode
                self._active_render_total = total
                self._active_render_inserted = 0
                self._reset_document_surface()
                self.text.configure(state=tk.NORMAL)
            elif kind == "chunk":
                _kind, _generation, _path, block_number, chunk = message
                self._insert_prepared_chunk(chunk)
                self._active_render_inserted = block_number
                total = max(1, self._active_render_total)
                self.status_var.set(
                    f"Loading {rel_path}… {block_number:,} / {total:,} blocks"
                )
            elif kind == "done":
                self.text.configure(state=tk.DISABLED)
                doc = self._active_render_doc
                self._active_render_doc = None
                self.heading_tree.state(["!disabled"])
                if doc is not None:
                    if self._active_render_mode == "yaml_plain":
                        self._install_outline_indices(doc)
                    self._finish_document_render(doc)
                return
            elif kind == "error":
                _kind, _generation, _path, error_message = message
                self.text.configure(state=tk.DISABLED)
                self.heading_tree.state(["!disabled"])
                self._active_render_doc = None
                self.status_var.set(f"Render error: {error_message}")
                messagebox.showerror(APP_TITLE, f"Could not render {rel_path}:\n\n{error_message}")
                return

        if self._active_render_doc is not None:
            self._schedule_render_poll()

    def _insert_prepared_chunk(self, chunk: RenderChunk) -> None:
        mode = self._active_render_mode
        if mode == "markdown":
            self._chunk_heading_counter = self._render_markdown_blocks(
                self._active_render_doc,
                chunk.text,
                use_source_marks=False,
                source_line_offset=chunk.start_line - 1,
                heading_counter_start=self._chunk_heading_counter,
            )
        else:
            tag = "code" if mode in {"yaml_plain", "code"} else "body"
            self.text.insert(tk.END, chunk.text, (tag,))

    def _finish_document_render(self, doc: Document) -> None:
        self.text.configure(state=tk.DISABLED)
        if doc.file_type in {"yaml", "yml"} and not self.heading_indices:
            self._install_outline_indices(doc)
        self._refresh_heading_positions()
        self.highlight_query()

        if self._pending_anchor:
            pending = self._pending_anchor
            self._pending_anchor = None
            self.scroll_to_anchor(pending)
        elif self._pending_line_number is not None:
            pending_line = self._pending_line_number
            self._pending_line_number = None
            self.scroll_to_source_line(pending_line)
        else:
            self.text.yview_moveto(0)

        self.status_var.set(self._document_status(doc))
        self._schedule_heading_sync()

    def _install_outline_indices(self, doc: Document) -> None:
        for heading in doc.headings:
            index = f"{max(1, heading.line_number)}.0"
            self.heading_indices[heading.anchor] = index
            self._document_anchor_indices.setdefault(slugify(heading.text), index)
            self._document_anchor_indices[heading.anchor] = index

    def render_yaml(self, doc: Document) -> None:
        self._reset_document_surface()
        for line_number, line in enumerate(doc.text.splitlines(), start=1):
            if doc.line_count <= LARGE_DOC_LINE_THRESHOLD:
                self.text.mark_set(f"source_line_{line_number}", self.text.index(tk.INSERT))
            self._insert_yaml_line(line)
        self.text.configure(state=tk.DISABLED)
        self._install_outline_indices(doc)

    def render_json(self, doc: Document) -> None:
        try:
            rendered = json.dumps(json.loads(doc.text), indent=2, ensure_ascii=False)
        except Exception:
            rendered = doc.text
        self._render_plain_text(rendered, "code")

    def render_csv(self, doc: Document) -> None:
        self._reset_document_surface()
        try:
            rows = list(csv.reader(doc.text.splitlines()))
            self._insert_table_widget(rows, sortable=True)
        except Exception as exc:
            dbg(f"render_csv: table fallback ({exc})")
            self.text.insert("1.0", doc.text, ("table",))
        self.text.configure(state=tk.DISABLED)

    def render_plain(self, doc: Document) -> None:
        self._render_plain_text(doc.text, "code" if doc.file_type == "py" else "body")

    def _render_plain_text(self, text: str, tag: str) -> None:
        self._reset_document_surface()
        self.text.insert("1.0", text, (tag,))
        self.text.configure(state=tk.DISABLED)

    def render_markdown(self, doc: Document) -> None:
        """Render Markdown without changing the source artifact."""
        dbg(f"render_markdown: {doc.rel_path} ({doc.line_count} lines)")
        body = strip_frontmatter(doc.text)
        self._reset_document_surface()
        try:
            use_source_marks = doc.line_count <= LARGE_DOC_LINE_THRESHOLD
            self._render_markdown_blocks(
                doc,
                body,
                use_source_marks=use_source_marks,
                source_line_offset=0,
                heading_counter_start=0,
            )
        except Exception as exc:
            dbg(f"render_markdown: exception: {exc}")
            self.status_var.set(f"Render error: {exc}")
        finally:
            self.text.configure(state=tk.DISABLED)

    def _render_markdown_blocks(
        self,
        doc: Optional[Document],
        body: str,
        use_source_marks: bool,
        source_line_offset: int = 0,
        heading_counter_start: int = 0,
    ) -> int:
        """Markdown-light block renderer with links, anchors, and table widgets."""
        lines = body.splitlines()
        i = 0
        heading_counter = heading_counter_start

        while i < len(lines):
            raw_line = lines[i]
            source_line = source_line_offset + i + 1
            line_start = self.text.index(tk.INSERT)

            if use_source_marks:
                self.text.mark_set(f"source_line_{source_line}", line_start)

            anchor_match = HTML_ANCHOR_RE.match(raw_line)
            if anchor_match:
                anchor_id = unquote(anchor_match.group(1).strip())
                self._document_anchor_indices[anchor_id] = line_start
                self._document_anchor_indices[slugify(anchor_id)] = line_start
                i += 1
                continue

            image_match = MARKDOWN_IMAGE_RE.fullmatch(raw_line.strip())
            if image_match:
                alt_text, image_target = image_match.groups()
                self._insert_markdown_image(doc, alt_text.strip(), image_target)
                i += 1
                continue

            fence_match = FENCE_RE.match(raw_line)
            if fence_match:
                language = (fence_match.group(2) or "").lower()
                block_lines: List[str] = []
                i += 1
                while i < len(lines):
                    close_match = FENCE_RE.match(lines[i])
                    if close_match:
                        break
                    block_lines.append(lines[i])
                    i += 1
                if i < len(lines) and FENCE_RE.match(lines[i]):
                    i += 1
                self._insert_code_block(block_lines, language)
                continue

            heading_match = HEADING_RE.match(raw_line)
            if heading_match:
                level = min(len(heading_match.group(1)), 6)
                heading_text = clean_heading_text(heading_match.group(2))
                anchor = f"h-{heading_counter}-{slugify(heading_text)}"
                heading_counter += 1
                self.heading_indices[anchor] = line_start
                self._document_anchor_indices.setdefault(slugify(heading_text), line_start)
                self._document_anchor_indices[anchor] = line_start
                self._insert_inline_markdown(heading_text, (f"h{level}",))
                self.text.insert(tk.END, "\n", (f"h{level}",))
                i += 1
                continue

            if looks_like_table_line(raw_line):
                table_lines: List[str] = []
                while i < len(lines) and looks_like_table_line(lines[i]):
                    table_lines.append(lines[i])
                    i += 1
                self._insert_table_block(table_lines)
                continue

            list_match = LIST_RE.match(raw_line)
            if list_match:
                indent, _bullet, content = list_match.groups()
                level = max(0, len(indent.replace("\t", "    ")) // 2)
                self.text.insert(tk.END, "  " * level + "• ", ("list",))
                self._insert_inline_markdown(content, ("list",))
                self.text.insert(tk.END, "\n", ("list",))
                i += 1
                continue

            ordered_match = ORDERED_LIST_RE.match(raw_line)
            if ordered_match:
                indent, number, content = ordered_match.groups()
                level = max(0, len(indent.replace("\t", "    ")) // 2)
                self.text.insert(tk.END, "  " * level + f"{number}. ", ("list",))
                self._insert_inline_markdown(content, ("list",))
                self.text.insert(tk.END, "\n", ("list",))
                i += 1
                continue

            if raw_line.strip().startswith(">"):
                quote_text = re.sub(r"^\s*>\s?", "", raw_line)
                self._insert_inline_markdown(quote_text, ("quote",))
                self.text.insert(tk.END, "\n", ("quote",))
                i += 1
                continue

            if raw_line.strip() in {"---", "***", "___"}:
                self.text.insert(tk.END, "─" * 80 + "\n", ("rule",))
                i += 1
                continue

            self._insert_inline_markdown(raw_line, ("body",))
            self.text.insert(tk.END, "\n", ("body",))
            i += 1

        dbg(f"_render_markdown_blocks: done ({heading_counter} headings rendered)")
        return heading_counter

    def _insert_markdown_image(
        self,
        doc: Optional[Document],
        alt_text: str,
        raw_target: str,
    ) -> None:
        """Render one local Markdown image in the main document surface."""
        if self.corpus is None or doc is None:
            self._insert_image_fallback(alt_text, raw_target, "No active corpus is available.")
            return

        target = clean_markdown_destination(raw_target)
        parsed = urlparse(target)
        if parsed.scheme.lower() in {"http", "https"}:
            self._insert_image_fallback(
                alt_text,
                target,
                "External images are not downloaded by the Reader.",
                link_target=target,
            )
            return

        file_part = target.partition("#")[0]
        candidate = resolve_repository_relative_path(doc.rel_path, file_part)
        if candidate is None:
            self._insert_image_fallback(alt_text, target, "The image path leaves the active repository root.")
            return
        if not self.corpus.source.asset_exists(candidate):
            self._insert_image_fallback(alt_text, target, f"No repository image exists at {candidate}.")
            return

        try:
            raw = self.corpus.source.read_bytes(candidate)
            encoded = base64.b64encode(raw).decode("ascii")
            probe = tk.PhotoImage(data=encoded)
            source_width = probe.width()
            source_height = probe.height()
            if source_width <= 0 or source_height <= 0:
                raise tk.TclError("image has no displayable dimensions")
        except (OSError, CorpusError, tk.TclError) as exc:
            self._insert_image_fallback(
                alt_text,
                target,
                f"The dependency-free image renderer could not decode this asset: {exc}",
            )
            return

        text_bg = self.text.cget("background")
        caption_fg = "#a0a0a0" if self.dark_mode else "#555555"
        frame = tk.Frame(self.text, background=text_bg, borderwidth=0, highlightthickness=0)
        image_label = tk.Label(frame, background=text_bg, borderwidth=0, highlightthickness=0)
        image_label.pack(anchor=tk.CENTER)
        caption_label: Optional[tk.Label] = None
        if alt_text:
            caption_label = tk.Label(
                frame,
                text=alt_text,
                background=text_bg,
                foreground=caption_fg,
                font=("Segoe UI", 9, "italic"),
                justify=tk.CENTER,
                pady=4,
            )
            caption_label.pack(anchor=tk.CENTER)

        embedded = EmbeddedImage(
            rel_path=candidate,
            alt_text=alt_text,
            encoded_data=encoded,
            source_width=source_width,
            source_height=source_height,
            frame=frame,
            image_label=image_label,
            caption_label=caption_label,
        )
        self._embedded_images.append(embedded)
        self._resize_embedded_images()

        start = self.text.index(tk.INSERT)
        self.text.window_create(tk.END, window=frame, padx=8, pady=8, align=tk.CENTER)
        end = self.text.index(tk.INSERT)
        self.text.tag_add("image_block", start, end)
        self.text.insert(tk.END, "\n", ("body",))

    def _insert_image_fallback(
        self,
        alt_text: str,
        target: str,
        reason: str,
        link_target: Optional[str] = None,
    ) -> None:
        label = alt_text or Path(unquote(target.partition("#")[0])).name or "Image"
        self.text.insert(tk.END, f"[Image: {label}] ", ("quote",))
        if link_target:
            self._insert_markdown_link("Open source", link_target, ("quote",))
            self.text.insert(tk.END, " — ", ("quote",))
        self.text.insert(tk.END, reason + "\n", ("quote",))

    def _insert_code_block(self, block_lines: List[str], language: str) -> None:
        """Insert a fenced code block without showing the fence markers."""
        if not block_lines:
            self.text.insert(tk.END, "\n", ("code",))
            return

        # Top margin.
        self.text.insert(tk.END, "\n", ("body",))

        if language in {"yaml", "yml"}:
            for raw_line in block_lines:
                self._insert_yaml_line(raw_line)
        else:
            block = "\n".join(block_lines)
            self.text.insert(tk.END, block + "\n", ("code",))

        # Bottom margin.
        self.text.insert(tk.END, "\n", ("body",))

    def _insert_yaml_line(self, raw_line: str) -> None:
        """Insert one YAML line with lightweight syntax coloring."""
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+)(\s*:\s*)(.*)$", raw_line)

        if not match:
            self.text.insert(tk.END, raw_line + "\n", ("code",))
            return

        indent, key, separator, value = match.groups()

        self.text.insert(tk.END, indent, ("code",))
        self.text.insert(tk.END, key, ("yaml_key",))
        self.text.insert(tk.END, separator, ("code",))
        self.text.insert(tk.END, value + "\n", ("yaml_value",))

    def _insert_inline_markdown(self, text: str, base_tags: Tuple[str, ...]) -> None:
        """Insert inline Markdown, including navigable internal/external links."""
        token_re = re.compile(
            r"(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*\n]+\*)"
        )
        pos = 0
        for match in token_re.finditer(text):
            if match.start() > pos:
                self.text.insert(tk.END, text[pos:match.start()], base_tags)

            token = match.group(0)
            link_match = MARKDOWN_LINK_RE.fullmatch(token)
            if link_match:
                label, target = link_match.groups()
                self._insert_markdown_link(label, target, base_tags)
            elif token.startswith("`") and token.endswith("`"):
                self.text.insert(tk.END, token[1:-1], base_tags + ("inline_code",))
            elif token.startswith("***") and token.endswith("***"):
                self.text.insert(tk.END, token[3:-3], base_tags + ("bold_italic",))
            elif token.startswith("**") and token.endswith("**"):
                self.text.insert(tk.END, token[2:-2], base_tags + ("bold",))
            elif token.startswith("*") and token.endswith("*"):
                self.text.insert(tk.END, token[1:-1], base_tags + ("italic",))
            else:
                self.text.insert(tk.END, token, base_tags)
            pos = match.end()

        if pos < len(text):
            self.text.insert(tk.END, text[pos:], base_tags)

    def _insert_markdown_link(
        self,
        label: str,
        target: str,
        base_tags: Tuple[str, ...],
    ) -> None:
        tag_name = f"document_link_{self._next_link_id}"
        self._next_link_id += 1
        self._link_tags.append(tag_name)
        self.text.insert(tk.END, label, base_tags + ("link", tag_name))
        self.text.tag_bind(
            tag_name,
            "<Button-1>",
            lambda _event, link_target=target: self._open_markdown_link(link_target),
        )
        self.text.tag_bind(
            tag_name,
            "<Enter>",
            lambda _event, name=tag_name: self._set_link_hover(name, True),
        )
        self.text.tag_bind(
            tag_name,
            "<Leave>",
            lambda _event, name=tag_name: self._set_link_hover(name, False),
        )

    def _set_link_hover(self, tag_name: str, active: bool) -> None:
        ranges = self.text.tag_ranges(tag_name)
        if len(ranges) < 2:
            return
        if active:
            self.text.configure(cursor="hand2")
            self.text.tag_add("link_hover", ranges[0], ranges[1])
        else:
            self.text.configure(cursor="xterm")
            self.text.tag_remove("link_hover", ranges[0], ranges[1])

    def _open_markdown_link(self, raw_target: str) -> None:
        target = raw_target.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        title_match = re.match(r'^(\S+?)(?:\s+["\'].*["\'])?$', target)
        if title_match:
            target = title_match.group(1)
        target = unquote(target)

        parsed = urlparse(target)
        if parsed.scheme.lower() in {"http", "https", "mailto"}:
            if messagebox.askyesno(
                "Open external link",
                f"Open this external destination in the default browser?\n\n{target}",
                parent=self,
            ):
                webbrowser.open_new_tab(target)
            return

        file_part, _separator, anchor = target.partition("#")
        if not file_part:
            self.scroll_to_anchor(anchor)
            return
        if self.corpus is None or self.current_path is None:
            return

        if file_part.startswith("/"):
            candidate = normalize_rel_path(posixpath.normpath(file_part))
        else:
            base_dir = posixpath.dirname(self.current_path)
            candidate = normalize_rel_path(posixpath.normpath(posixpath.join(base_dir, file_part)))

        if candidate.startswith("../") or candidate == "..":
            self._report_missing_link(target, "The target leaves the active repository root.")
            return
        if candidate not in self.corpus.documents:
            readme_candidate = normalize_rel_path(posixpath.join(candidate, "README.md"))
            if readme_candidate in self.corpus.documents:
                candidate = readme_candidate
            else:
                self._report_missing_link(target, f"No active Reader artifact exists at {candidate}.")
                return

        self.open_document(candidate, anchor_name=anchor or None)

    def _report_missing_link(self, target: str, reason: str) -> None:
        self.status_var.set(f"Link target unavailable: {target}")
        messagebox.showwarning(
            "Link target unavailable",
            f"The Reader could not open this link:\n\n{target}\n\n{reason}",
            parent=self,
        )

    def _insert_table_block(self, table_lines: List[str]) -> None:
        """Render a Markdown table as a real scrollable cell grid."""
        rows: List[List[str]] = []
        for line in table_lines:
            cells = split_markdown_table_row(line)
            if cells and all(re.fullmatch(r":?-{3,}:?", cell or "---") for cell in cells):
                continue
            rows.append(cells)
        self._insert_table_widget(rows, sortable=False)

    def _insert_table_widget(self, rows: List[List[str]], sortable: bool) -> None:
        if not rows:
            return
        column_count = max(len(row) for row in rows)
        normalized = [row + [""] * (column_count - len(row)) for row in rows]
        display_rows: List[List[str]] = []
        cell_links: Dict[Tuple[str, int], str] = {}
        for row_index, row in enumerate(normalized):
            display_row: List[str] = []
            for column_index, value in enumerate(row):
                label, link_target = markdown_link_cell(value)
                display_row.append(f"↗ {label}" if link_target else label)
                if row_index > 0 and link_target:
                    cell_links[(str(row_index - 1), column_index)] = link_target
            display_rows.append(display_row)

        headers = display_rows[0]
        data_rows = display_rows[1:]
        columns = tuple(f"c{index}" for index in range(column_count))

        frame = ttk.Frame(self.text, style="Table.TFrame", padding=(0, 2, 0, 2))
        tree_height = min(max(len(data_rows), 1), 16)
        tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=tree_height,
            style="Data.Treeview",
            selectmode="browse",
        )
        tree.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        xscroll = AutoHideScrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        tree.configure(xscrollcommand=xscroll.set)

        if len(data_rows) > tree_height:
            yscroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
            yscroll.grid(row=0, column=1, sticky="ns")
            tree.configure(yscrollcommand=yscroll.set)

        sort_state: Dict[int, bool] = {}
        for index, column in enumerate(columns):
            header = headers[index].strip() or f"Column {index + 1}"
            values = [str(row[index]) for row in display_rows]
            max_chars = max((len(value) for value in values), default=8)
            width = max(90, min(420, max_chars * 8 + 24))
            command = None
            if sortable:
                command = lambda col=index: self._sort_table_column(tree, col, sort_state)
            if command is None:
                tree.heading(column, text=header)
            else:
                tree.heading(column, text=header, command=command)
            tree.column(column, width=width, minwidth=70, stretch=False, anchor=tk.W)

        for row_index, row in enumerate(data_rows):
            tree.insert("", tk.END, iid=str(row_index), values=row)

        def table_link_at(event: tk.Event) -> Optional[str]:
            item = tree.identify_row(event.y)
            column = tree.identify_column(event.x)
            if not item or not column.startswith("#"):
                return None
            try:
                column_index = int(column[1:]) - 1
            except ValueError:
                return None
            return cell_links.get((item, column_index))

        def on_table_motion(event: tk.Event) -> None:
            tree.configure(cursor="hand2" if table_link_at(event) else ("hand2" if sortable else "arrow"))

        def on_table_click(event: tk.Event) -> None:
            link_target = table_link_at(event)
            if link_target:
                self._open_markdown_link(link_target)

        tree.bind("<Motion>", on_table_motion)
        tree.bind("<ButtonRelease-1>", on_table_click)
        tree.configure(cursor="hand2" if sortable else "arrow")

        frame._table_tree = tree
        frame._table_xscroll = xscroll
        frame.grid_propagate(False)
        xscroll.visibility_callback = lambda _visible: self._resize_embedded_tables()
        self._embedded_tables.append(frame)
        self.text.insert(tk.END, "\n", ("body",))
        self.text.window_create(tk.END, window=frame, padx=8, pady=6, stretch=True)
        self.text.insert(tk.END, "\n\n", ("body",))
        self.after_idle(self._resize_embedded_tables)

    def _sort_table_column(
        self,
        tree: ttk.Treeview,
        column_index: int,
        sort_state: Dict[int, bool],
    ) -> None:
        descending = sort_state.get(column_index, False)
        rows = []
        for item in tree.get_children(""):
            values = tree.item(item, "values")
            value = values[column_index] if column_index < len(values) else ""
            rows.append((table_sort_value(str(value)), item))
        rows.sort(key=lambda pair: pair[0], reverse=descending)
        for position, (_value, item) in enumerate(rows):
            tree.move(item, "", position)
        sort_state[column_index] = not descending

    # ------------------------------------------------------------------ #
    # Search                                                             #
    # ------------------------------------------------------------------ #

    def run_search(self) -> None:
        if self.corpus is None:
            return
        query = self.search_var.get().strip()
        self.search_tree.delete(*self.search_tree.get_children())
        self.search_results = self.corpus.search(query)

        for index, (rel_path, line_no, snippet) in enumerate(self.search_results):
            title = self.corpus.documents[rel_path].title
            self.search_tree.insert("", tk.END, iid=str(index), values=(title, line_no, snippet))

        self.highlight_query()
        if query:
            self.status_var.set(f"Search '{query}': {len(self.search_results):,} result(s).")
        else:
            self.status_var.set("Search cleared.")

    def clear_search(self) -> None:
        self.search_var.set("")
        self.search_tree.delete(*self.search_tree.get_children())
        self.search_results = []
        self.text.configure(state=tk.NORMAL)
        self.text.tag_remove("search", "1.0", tk.END)
        self.text.configure(state=tk.DISABLED)

    def highlight_query(self) -> None:
        if self._active_render_doc is not None:
            return
        query = self.search_var.get().strip()
        self.text.configure(state=tk.NORMAL)
        self.text.tag_remove("search", "1.0", tk.END)
        if query:
            start = "1.0"
            count = 0
            while True:
                pos = self.text.search(query, start, stopindex=tk.END, nocase=True)
                if not pos:
                    break
                end = f"{pos}+{len(query)}c"
                self.text.tag_add("search", pos, end)
                start = end
                count += 1
                if count >= MAX_SEARCH_HIGHLIGHTS:
                    break
        self.text.configure(state=tk.DISABLED)

    def scroll_to_source_line(self, line_number: int) -> None:
        if self._active_render_doc is not None:
            self._pending_line_number = line_number
            return
        mark = f"source_line_{line_number}"

        try:
            index = self.text.index(mark)
        except tk.TclError:
            # Large documents do not create per-line marks.
            # Tk Text line indices are good enough for direct jumps.
            index = f"{max(1, line_number)}.0"

        self.text.configure(state=tk.NORMAL)
        self.text.tag_remove("current_line", "1.0", tk.END)
        self.text.tag_add("current_line", index, f"{index} lineend+1c")
        self.text.configure(state=tk.DISABLED)
        self.text.see(index)

    def scroll_to_anchor(self, anchor_name: str) -> None:
        anchor = unquote((anchor_name or "").lstrip("#").strip())
        if not anchor:
            return
        if self._active_render_doc is not None:
            self._pending_anchor = anchor
            return
        candidates = [anchor, anchor.lower(), slugify(anchor)]
        index = next(
            (self._document_anchor_indices[key] for key in candidates if key in self._document_anchor_indices),
            None,
        )
        if index is None:
            self._report_missing_link(f"#{anchor}", "No matching document anchor was found.")
            return
        self.text.see(index)
        self.text.configure(state=tk.NORMAL)
        self.text.tag_remove("current_line", "1.0", tk.END)
        self.text.tag_add("current_line", index, f"{index} lineend+1c")
        self.text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------ #
    # Toolbar / dialog actions                                           #
    # ------------------------------------------------------------------ #

    def _open_folder(self) -> None:
        path = filedialog.askdirectory(title="Open PMS-VECTOR folder")
        if path:
            self.load_source(Path(path))

    def _open_zip(self) -> None:
        path = filedialog.askopenfilename(
            title="Open PMS-VECTOR zip file",
            filetypes=[("Zip files", "*.zip"), ("All files", "*.*")],
        )
        if path:
            self.load_source(Path(path))

    # ------------------------------------------------------------------ #
    # Event handlers                                                     #
    # ------------------------------------------------------------------ #

    def _on_file_selected(self, event: tk.Event) -> None:
        if self._suppress_file_select_event:
            dbg("_on_file_selected: suppressed programmatic selection")
            return

        selection = self.file_tree.selection()
        if not selection:
            return

        item = selection[0]
        rel_path = self._file_item_to_path.get(item)
        if rel_path:
            self.open_document(rel_path)

    def _on_heading_selected(self, event: tk.Event) -> None:
        if self._suppress_heading_select_event:
            return
        selection = self.heading_tree.selection()
        if not selection:
            return
        item = selection[0]
        anchor = self._heading_item_to_anchor.get(item)
        if anchor and anchor in self.heading_indices:
            self._manual_heading_until = time.monotonic() + 0.65
            index = self.heading_indices[anchor]
            self.text.see(index)
            self.text.configure(state=tk.NORMAL)
            self.text.tag_remove("current_line", "1.0", tk.END)
            self.text.tag_add("current_line", index, f"{index} lineend+1c")
            self.text.configure(state=tk.DISABLED)

    def _enable_heading_select_events(self) -> None:
        self._suppress_heading_select_event = False

    def _on_search_result_open(self, event: tk.Event) -> None:
        selection = self.search_tree.selection()
        if not selection:
            return
        try:
            result_index = int(selection[0])
        except ValueError:
            return
        if result_index >= len(self.search_results):
            return
        rel_path, line_no, _snippet = self.search_results[result_index]
        self.open_document(rel_path, line_number=line_no)

    def _select_file_tree_item(self, rel_path: str) -> None:
        for item, item_path in self._file_item_to_path.items():
            if item_path == rel_path:
                self._suppress_file_select_event = True
                try:
                    self.file_tree.selection_set(item)
                    self.file_tree.see(item)
                finally:
                    self.after_idle(self._enable_file_select_events)
                break

    def _enable_file_select_events(self) -> None:
        self._suppress_file_select_event = False

    # ------------------------------------------------------------------ #
    # Cleanup                                                            #
    # ------------------------------------------------------------------ #

    def destroy(self) -> None:
        self._cancel_active_render()
        if self.graph_lab is not None and self.graph_lab.winfo_exists():
            try:
                self.graph_lab.destroy()
            except tk.TclError:
                pass
        if self.corpus is not None:
            self.corpus.source.close()
        super().destroy()


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def corpus_sort_key(rel_path: str) -> Tuple[int, Tuple[object, ...]]:
    first = rel_path.split("/", 1)[0] if "/" in rel_path else rel_path
    try:
        section_index = SECTION_ORDER.index(first)
    except ValueError:
        section_index = len(SECTION_ORDER)
    return section_index, natural_sort_key(rel_path)


def natural_sort_key(value: str) -> Tuple[Tuple[int, object], ...]:
    parts = re.split(r"(\d+)", value.lower())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)


def as_dict(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> List[object]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return [value]


def scalar_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value).strip()
    if isinstance(value, list):
        return "; ".join(scalar_text(item) for item in value if scalar_text(item))
    if isinstance(value, dict):
        return summarize_mapping(value)
    return str(value).strip()


def join_nonempty(values: Iterable[str]) -> str:
    return "\n".join(value for value in values if value and value.strip())


def summarize_mapping(value: object, max_items: int = 24) -> str:
    mapping = as_dict(value)
    lines: List[str] = []
    for key, item in list(mapping.items())[:max_items]:
        if item in (None, "", [], {}):
            continue
        if isinstance(item, dict):
            nested = summarize_mapping(item, max_items=8)
            if nested:
                lines.append(f"{key}: {nested.replace(chr(10), '; ')}")
        elif isinstance(item, list):
            text = "; ".join(scalar_text(x) for x in item if scalar_text(x))
            if text:
                lines.append(f"{key}: {text}")
        else:
            lines.append(f"{key}: {scalar_text(item)}")
    return "\n".join(lines)


def summarize_rot(rot: object) -> str:
    mapping = as_dict(rot)
    applied = [as_dict(x) for x in as_list(mapping.get("applied"))]
    lines = []
    for item in applied:
        family = scalar_text(item.get("family")) or "ROT"
        result = scalar_text(item.get("result"))
        vulnerability = scalar_text(item.get("vulnerability"))
        lines.append(f"{family}: {result or vulnerability or 'declared applied rotation'}")
    untested = "; ".join(scalar_text(x) for x in as_list(mapping.get("untested_relevant")) if scalar_text(x))
    if untested:
        lines.append(f"Untested relevant: {untested}")
    return "\n".join(lines)


def _split_yaml_key_value(text: str) -> Optional[Tuple[str, str]]:
    quote: Optional[str] = None
    depth = 0
    for i, ch in enumerate(text):
        if ch in "'\"":
            if quote is None:
                quote = ch
            elif quote == ch:
                quote = None
        elif quote is None:
            if ch in "[{(":
                depth += 1
            elif ch in "]})" and depth:
                depth -= 1
            elif ch == ":" and depth == 0:
                key = text[:i].strip()
                if re.fullmatch(r"[\w.-]+", key, re.UNICODE):
                    return key, text[i + 1:].strip()
                return None
    return None


def _yaml_scalar(value: str) -> object:
    value = value.strip()
    if not value:
        return ""
    low = value.casefold()
    if low in {"null", "~"}:
        return None
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    if value.startswith(("'", '"')) and value.endswith(("'", '"')):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                return ast.literal_eval(value)
        except Exception:
            return value[1:-1]
    if value.startswith("[") or value.startswith("{"):
        py = re.sub(r"\btrue\b", "True", value, flags=re.I)
        py = re.sub(r"\bfalse\b", "False", py, flags=re.I)
        py = re.sub(r"\bnull\b", "None", py, flags=re.I)
        try:
            return ast.literal_eval(py)
        except Exception:
            if value == "[]":
                return []
            if value == "{}":
                return {}
            return value
    if re.fullmatch(r"[-+]?\d+", value):
        try:
            return int(value)
        except ValueError:
            pass
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", value):
        try:
            return float(value)
        except ValueError:
            pass
    return value


def parse_simple_yaml(text: str) -> object:
    """Parse the controlled YAML subset used by PMS-VECTOR.

    This is a dependency-free repository lens, not a validating YAML engine.
    It supports nested mappings/lists, flow scalars, quoted scalars, booleans,
    nulls, numbers, block scalars, and indented plain-scalar continuations.
    """
    tokens: List[Tuple[int, str, int]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        expanded = raw.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(" "))
        tokens.append((indent, expanded.strip(), line_no))
    if not tokens:
        return {}

    def block_scalar(i: int, parent_indent: int, folded: bool) -> Tuple[str, int]:
        parts: List[str] = []
        while i < len(tokens) and tokens[i][0] > parent_indent:
            parts.append(tokens[i][1])
            i += 1
        return ((" ".join(parts) if folded else "\n".join(parts)).strip(), i)

    def parse_mapping(i: int, indent: int) -> Tuple[Dict[str, object], int]:
        out: Dict[str, object] = {}
        while i < len(tokens):
            ind, content, _ln = tokens[i]
            if ind < indent:
                break
            if ind > indent:
                # Continuation belongs to the previous scalar and is consumed there.
                break
            if content.startswith("- "):
                break
            kv = _split_yaml_key_value(content)
            if kv is None:
                i += 1
                continue
            key, raw_value = kv
            i += 1
            if raw_value in {">", ">-", ">+", "|", "|-", "|+"}:
                value, i = block_scalar(i, indent, raw_value.startswith(">"))
                out[key] = value
                continue
            if raw_value == "":
                if i < len(tokens) and tokens[i][0] > indent:
                    value, i = parse_block(i, tokens[i][0])
                    out[key] = value
                elif i < len(tokens) and tokens[i][0] == indent and tokens[i][1].startswith("-"):
                    # YAML permits indentless sequences as a mapping value.
                    value, i = parse_sequence(i, indent)
                    out[key] = value
                else:
                    out[key] = None
                continue
            value = _yaml_scalar(raw_value)
            continuation: List[str] = []
            while i < len(tokens) and tokens[i][0] > indent:
                nind, ncontent, _ = tokens[i]
                if ncontent.startswith("- ") or _split_yaml_key_value(ncontent) is not None:
                    break
                continuation.append(ncontent)
                i += 1
            if continuation and isinstance(value, str):
                value = (value + " " + " ".join(continuation)).strip()
            out[key] = value
        return out, i

    def parse_sequence(i: int, indent: int) -> Tuple[List[object], int]:
        out: List[object] = []
        while i < len(tokens):
            ind, content, _ln = tokens[i]
            if ind < indent or ind != indent or not content.startswith("-"):
                break
            rest = content[1:].strip()
            i += 1
            if rest == "":
                if i < len(tokens) and tokens[i][0] > indent:
                    value, i = parse_block(i, tokens[i][0])
                    out.append(value)
                else:
                    out.append(None)
                continue
            kv = _split_yaml_key_value(rest)
            if kv is None:
                value = _yaml_scalar(rest)
                continuation: List[str] = []
                while i < len(tokens) and tokens[i][0] > indent:
                    ncontent = tokens[i][1]
                    if ncontent.startswith("- ") or _split_yaml_key_value(ncontent) is not None:
                        break
                    continuation.append(ncontent)
                    i += 1
                if continuation and isinstance(value, str):
                    value = (value + " " + " ".join(continuation)).strip()
                out.append(value)
                continue
            key, raw_value = kv
            item: Dict[str, object] = {}
            if raw_value in {">", ">-", ">+", "|", "|-", "|+"}:
                value, i = block_scalar(i, indent, raw_value.startswith(">"))
                item[key] = value
            elif raw_value == "":
                if i < len(tokens) and tokens[i][0] > indent:
                    child_indent = tokens[i][0]
                    value, i = parse_block(i, child_indent)
                    item[key] = value
                else:
                    item[key] = None
            else:
                value = _yaml_scalar(raw_value)
                continuation: List[str] = []
                while i < len(tokens) and tokens[i][0] > indent and tokens[i][0] <= indent + 2:
                    ncontent = tokens[i][1]
                    if ncontent.startswith("- ") or _split_yaml_key_value(ncontent) is not None:
                        break
                    continuation.append(ncontent)
                    i += 1
                if continuation and isinstance(value, str):
                    value = (value + " " + " ".join(continuation)).strip()
                item[key] = value
            if i < len(tokens) and tokens[i][0] > indent:
                child_indent = tokens[i][0]
                extra, i = parse_mapping(i, child_indent)
                item.update(extra)
            out.append(item)
        return out, i

    def parse_block(i: int, indent: int) -> Tuple[object, int]:
        if i >= len(tokens):
            return {}, i
        if tokens[i][1].startswith("-") and tokens[i][0] == indent:
            return parse_sequence(i, indent)
        return parse_mapping(i, indent)

    result, _ = parse_block(0, tokens[0][0])
    return result


class _ReadableHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.title_parts: List[str] = []
        self.in_title = False
        self.skip_depth = 0
        self.heading_level = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = True
        if re.fullmatch(r"h[1-6]", tag):
            self.heading_level = int(tag[1])
            self.parts.append("\n" + "#" * self.heading_level + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"p", "div", "section", "article", "header", "footer", "tr"}:
            self.parts.append("\n")
        elif tag in {"br"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag == "pre":
            self.parts.append("\n```\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag == "title":
            self.in_title = False
        if re.fullmatch(r"h[1-6]", tag):
            self.heading_level = 0
            self.parts.append("\n")
        elif tag in {"p", "div", "section", "article", "li", "tr"}:
            self.parts.append("\n")
        elif tag == "pre":
            self.parts.append("\n```\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        if data:
            self.parts.append(data)


def html_to_markdownish(text: str) -> Tuple[str, str]:
    parser = _ReadableHtmlParser()
    parser.feed(text)
    rendered = "".join(parser.parts)
    rendered = re.sub(r"[ \t]+", " ", rendered)
    rendered = re.sub(r" *\n *", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    return rendered, title


def clean_yaml_scalar(value: str) -> str:
    value = value.strip()
    if value in {"null", "~"}:
        return ""
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def flatten_yaml_scalars(text: str) -> Dict[Tuple[str, ...], str]:
    """Best-effort scalar lens for the repository's controlled YAML files.

    It intentionally does not attempt to be a full YAML parser. The reader only
    needs a handful of declared identifiers and labels for navigation and graph
    views. Full validation remains owned by the repository schema tooling.
    """
    result: Dict[Tuple[str, ...], str] = {}
    stack: List[Tuple[int, str]] = []
    current_path: Optional[Tuple[str, ...]] = None
    current_indent = -1

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = YAML_KEY_RE.match(raw_line)
        if match:
            indent_text, key, value = match.groups()
            indent = len(indent_text.replace("\t", "    "))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            path = tuple(item[1] for item in stack) + (key.strip(),)
            stack.append((indent, key.strip()))
            value = value or ""
            if value and value not in {"|", ">"} and not value.startswith("&"):
                result[path] = clean_yaml_scalar(value)
                current_path = path
                current_indent = indent
            else:
                current_path = None
                current_indent = indent
            continue

        if current_path is not None:
            indent = len(raw_line) - len(raw_line.lstrip(" "))
            stripped = raw_line.strip()
            if indent > current_indent and stripped and not stripped.startswith("- "):
                result[current_path] = (result[current_path] + " " + clean_yaml_scalar(stripped)).strip()

    return result


def scalar_from_flat(flat: Dict[Tuple[str, ...], str], paths: List[Tuple[str, ...]], default: str = "") -> str:
    for path in paths:
        value = flat.get(path)
        if value:
            return value
    return default


def scalar_by_leaf(flat: Dict[Tuple[str, ...], str], leaf: str) -> str:
    matches = [(path, value) for path, value in flat.items() if path and path[-1] == leaf and value]
    if not matches:
        return ""
    # Prefer the shortest path, which normally identifies result.routing.selected_class.
    matches.sort(key=lambda item: (len(item[0]), item[0]))
    return matches[0][1]


def parse_case_index(text: str) -> Dict[str, Dict[str, str]]:
    result: Dict[str, Dict[str, str]] = {}
    in_cases = False
    current: Optional[Dict[str, str]] = None
    for raw_line in text.splitlines():
        if raw_line.strip() == "cases:":
            in_cases = True
            continue
        if not in_cases:
            continue
        if raw_line and not raw_line.startswith(" ") and not raw_line.startswith("-"):
            break
        start_match = re.match(r"^-\s+case_id:\s*(.+?)\s*$", raw_line)
        if start_match:
            if current and current.get("case_id"):
                result[current["case_id"]] = current
            current = {"case_id": clean_yaml_scalar(start_match.group(1))}
            continue
        if current is None:
            continue
        field_match = re.match(r"^\s{2}([A-Za-z0-9_]+):\s*(.*?)\s*$", raw_line)
        if field_match:
            key, value = field_match.groups()
            if value and value not in {"|", ">"}:
                current[key] = clean_yaml_scalar(value)
    if current and current.get("case_id"):
        result[current["case_id"]] = current
    return result


def clean_markdown_destination(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    title_match = re.match(r'^(\S+?)(?:\s+["\'].*["\'])?$', target)
    if title_match:
        target = title_match.group(1)
    return unquote(target)


def resolve_repository_relative_path(current_rel_path: str, raw_target: str) -> Optional[str]:
    target = clean_markdown_destination(raw_target)
    if not target:
        return None
    if target.startswith("/"):
        candidate = normalize_rel_path(posixpath.normpath(target))
    else:
        base_dir = posixpath.dirname(current_rel_path)
        candidate = normalize_rel_path(posixpath.normpath(posixpath.join(base_dir, target)))
    if candidate in {"", ".", ".."} or candidate.startswith("../"):
        return None
    return candidate


def normalize_rel_path(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    raw = match.group(1)
    meta: Dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"\'')
        if key:
            meta[key] = value
    return meta, text[match.end():]


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def parse_yaml_outline(text: str) -> List[Heading]:
    """Return a shallow, indentation-based YAML outline.

    This is a navigation aid only. It is deliberately not a YAML parser and
    does not perform schema or semantic validation.
    """
    headings: List[Heading] = []
    stack: List[Tuple[int, str]] = []
    counter = 0
    identifier_keys = {"id", "rule_id", "stage_id", "class_id", "record_id", "name"}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = YAML_OUTLINE_KEY_RE.match(raw_line)
        if not match:
            continue
        indent_text, list_marker, key, value = match.groups()
        indent = len(indent_text.replace("\t", "    ")) + (2 if list_marker else 0)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        level = len(stack) + 1

        include = level <= 2 or (level == 3 and key in YAML_OUTLINE_LEVEL3_KEYS)
        if include:
            label = key
            scalar = clean_yaml_scalar(value)
            if key in identifier_keys and scalar and scalar not in {"|", ">"}:
                label = f"{key}: {scalar[:120]}"
            anchor = f"y-{counter}-{slugify(label)}"
            headings.append(Heading(level=min(level, 3), text=label, line_number=line_number, anchor=anchor))
            counter += 1
        stack.append((indent, key))

    return headings


def chunk_text_by_bytes(text: str, target_bytes: int) -> List[RenderChunk]:
    """Split text on line boundaries while preserving exact source content."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return [RenderChunk("", 1)]
    chunks: List[RenderChunk] = []
    current: List[str] = []
    current_bytes = 0
    start_line = 1
    line_number = 1
    for line in lines:
        line_bytes = len(line.encode("utf-8", errors="replace"))
        if current and current_bytes + line_bytes > target_bytes:
            chunks.append(RenderChunk("".join(current), start_line))
            current = []
            current_bytes = 0
            start_line = line_number
        current.append(line)
        current_bytes += line_bytes
        line_number += 1
    if current:
        chunks.append(RenderChunk("".join(current), start_line))
    return chunks


def chunk_markdown_text(text: str, target_bytes: int) -> List[RenderChunk]:
    """Split Markdown at safe block boundaries, never inside fenced code."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return [RenderChunk("", 1)]
    chunks: List[RenderChunk] = []
    current: List[str] = []
    current_bytes = 0
    start_line = 1
    in_fence = False
    line_number = 1

    for line in lines:
        current.append(line)
        current_bytes += len(line.encode("utf-8", errors="replace"))
        if FENCE_RE.match(line.rstrip("\r\n")):
            in_fence = not in_fence

        safe_boundary = not in_fence and not line.strip()
        forced_boundary = not in_fence and current_bytes >= target_bytes * 2
        if current_bytes >= target_bytes and (safe_boundary or forced_boundary):
            chunks.append(RenderChunk("".join(current), start_line))
            current = []
            current_bytes = 0
            start_line = line_number + 1
        line_number += 1

    if current:
        chunks.append(RenderChunk("".join(current), start_line))
    return chunks


def parse_headings(text: str) -> List[Heading]:
    headings: List[Heading] = []
    in_code = False
    heading_counter = 0

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        try:
            is_fence = bool(FENCE_RE.match(raw_line))
        except Exception:
            is_fence = False

        if is_fence:
            in_code = not in_code
            continue
        if in_code:
            continue

        try:
            match = HEADING_RE.match(raw_line)
        except Exception:
            continue

        if not match:
            continue

        text_value = clean_heading_text(match.group(2))
        anchor = f"h-{heading_counter}-{slugify(text_value)}"
        headings.append(
            Heading(
                level=len(match.group(1)),
                text=text_value,
                line_number=line_number,
                anchor=anchor,
            )
        )
        heading_counter += 1
    return headings


def first_heading_title(headings: List[Heading]) -> Optional[str]:
    return headings[0].text if headings else None


def clean_heading_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+#*$", "", text)
    text = text.replace("`", "")
    return text


def slugify(text: str) -> str:
    value = text.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "heading"


def looks_like_table_line(line: str) -> bool:
    stripped = line.strip()
    return (
        stripped.startswith("|")
        and stripped.endswith("|")
        and stripped.count("|") >= 2
    )


def split_markdown_table_row(line: str) -> List[str]:
    """Split a pipe table row while preserving escaped pipes."""
    stripped = line.strip().strip("|")
    cells: List[str] = []
    current: List[str] = []
    escaped = False
    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def markdown_link_cell(value: str) -> Tuple[str, Optional[str]]:
    """Return display text and target for a cell containing one Markdown link."""
    match = MARKDOWN_LINK_RE.fullmatch(value.strip())
    if not match:
        return value, None
    label, target = match.groups()
    label = re.sub(r"[`*_]", "", label).strip()
    return label, target.strip()


def table_sort_value(value: str) -> Tuple[int, object]:
    cleaned = value.strip().replace(",", "")
    try:
        return 0, float(cleaned)
    except ValueError:
        return 1, value.casefold()


def prettify_file_name(rel_path: str) -> str:
    name = Path(rel_path).name
    if name.lower().endswith(".md"):
        name = name[:-3]
    name = name.replace("_", " ").replace("-", " - ")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def walk_widgets(widget: tk.Widget) -> Iterable[tk.Widget]:
    yield widget
    for child in widget.winfo_children():
        yield from walk_widgets(child)


def discover_default_source() -> Optional[Path]:
    """Return the first valid PMS-VECTOR source path found, or None."""
    candidates: List[Path] = []
    positional = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    if positional:
        candidates.append(Path(positional[0]))
    cwd = Path.cwd()
    script_dir = Path(__file__).resolve().parent
    repo_root_from_tool_dir = script_dir.parent
    candidates.extend([
        cwd,
        cwd / "18. PMS-VECTOR",
        cwd / "PMS-VECTOR",
        cwd / "PMS-VECTOR.zip",
        script_dir,
        script_dir / "18. PMS-VECTOR",
        script_dir / "PMS-VECTOR.zip",
        repo_root_from_tool_dir,
        repo_root_from_tool_dir / "18. PMS-VECTOR",
    ])
    for candidate in candidates:
        dbg(f"discover: checking {candidate}")
        try:
            if candidate.is_dir():
                CorpusSource._detect_folder_root(candidate)
                return candidate
            if candidate.is_file() and candidate.suffix.lower() == ".zip":
                with zipfile.ZipFile(candidate) as zf:
                    CorpusSource._detect_zip_prefix(zf)
                return candidate
        except Exception as exc:
            dbg(f"discover: {candidate} rejected ({exc})")
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_self_test(source_path: Optional[Path]) -> int:
    """Check PMS-VECTOR repository integration, not VECTOR validity."""
    source_path = source_path or discover_default_source()
    if source_path is None:
        print("No PMS-VECTOR corpus found.", file=sys.stderr)
        return 2
    source = CorpusSource(source_path)
    try:
        corpus = Corpus(source)
        expected_ids = [f"E{i:02d}" for i in range(1, 24)]
        actual_ids = [case.case_id for case in corpus.cases]
        missing_cases = sorted(set(expected_ids) - set(actual_ids), key=natural_sort_key)
        extra_cases = sorted(set(actual_ids) - set(expected_ids), key=natural_sort_key)
        missing_pairs = [case.case_id for case in corpus.cases if not case.markdown_path or case.yaml_path not in corpus.documents]
        bad_top_level: List[str] = []
        bad_block_counts: List[str] = []
        canonical_blocks = [
            "reference", "frame", "question", "position", "baseline_claim", "dir", "calibration",
            "rot_profile", "dist_derived", "reversibility", "option_space", "transformability",
            "consequence_topology", "constraint", "orientation_uptake", "pressure", "result",
        ]
        for case in corpus.cases:
            if set(case.raw.keys()) != {"case_metadata", "vector_record"}:
                bad_top_level.append(case.case_id)
            record = as_dict(case.raw.get("vector_record"))
            if list(record.keys()) != canonical_blocks:
                bad_block_counts.append(case.case_id)

        # Index -> case identity and navigation parity.
        index_root = as_dict(corpus.case_index_data.get("case_index"))
        index_cases = {str(as_dict(x).get("case_id") or ""): as_dict(x) for x in as_list(index_root.get("cases"))}
        index_case_mismatches: List[str] = []
        for case in corpus.cases:
            idx = index_cases.get(case.case_id, {})
            checks = {
                "title": (case.title, scalar_text(idx.get("title"))),
                "paper_ref": (case.paper_ref, scalar_text(idx.get("paper_ref"))),
                "case_format": (case.case_format, scalar_text(idx.get("case_format"))),
                "load_bearing": (str(case.load_bearing).lower(), str(bool(idx.get("load_bearing", False))).lower()),
            }
            for field_name, (actual, expected) in checks.items():
                if actual != expected:
                    index_case_mismatches.append(f"{case.case_id}:{field_name}:{actual!r}!={expected!r}")
            idx_pair = as_dict(idx.get("artifact_pair"))
            if scalar_text(idx_pair.get("yaml")) != case.yaml_path:
                index_case_mismatches.append(f"{case.case_id}:yaml_path")
            if scalar_text(idx_pair.get("markdown")) != case.markdown_path:
                index_case_mismatches.append(f"{case.case_id}:markdown_path")
            if [str(x) for x in as_list(idx.get("pressure_families"))] != case.pressure_families:
                index_case_mismatches.append(f"{case.case_id}:pressure_families")
            if [str(x) for x in as_list(idx.get("constructs_under_test"))] != case.constructs_under_test:
                index_case_mismatches.append(f"{case.case_id}:constructs_under_test")

        # Dependency integrity.
        dep_root = as_dict(corpus.dependency_data.get("dependency_map"))
        dep_nodes = as_dict(dep_root.get("dependency_nodes"))
        dep_edges = [as_dict(x) for x in as_list(dep_root.get("dependency_edges"))]
        prohibited = [as_dict(x) for x in as_list(dep_root.get("prohibited_inferences"))]
        roots = [str(x) for x in as_list(dep_root.get("load_bearing_roots"))]
        missing_edge_endpoints: List[str] = []
        declared = set(dep_nodes)
        for edge in dep_edges:
            for key in ("dependent", "dependency"):
                value = str(edge.get(key) or "")
                if value and value not in declared:
                    missing_edge_endpoints.append(f"{edge.get('edge_id','?')}:{key}:{value}")
        for edge in prohibited:
            for key in ("source", "target"):
                value = str(edge.get(key) or "")
                if value and value not in declared:
                    missing_edge_endpoints.append(f"{edge.get('inference_id','?')}:{key}:{value}")

        # Provenance integrity: origin/status only, not epistemic ranking.
        prov_root = as_dict(corpus.provenance_data.get("claim_provenance"))
        source_registry = as_dict(prov_root.get("source_registry"))
        claims = [as_dict(x) for x in as_list(prov_root.get("claims"))]
        provenance_ids = [scalar_text(x.get("claim_id")) for x in claims]
        duplicate_provenance_ids = sorted({cid for cid in provenance_ids if cid and provenance_ids.count(cid) > 1})
        bad_provenance_statuses = sorted({scalar_text(x.get("status")) for x in claims if scalar_text(x.get("status")) not in {"C", "R", "N", "P"}})
        unresolved_provenance_sources: List[str] = []
        for claim in claims:
            cid = scalar_text(claim.get("claim_id")) or "?"
            for sid in [str(x) for x in as_list(claim.get("origin_sources"))]:
                if sid not in source_registry:
                    unresolved_provenance_sources.append(f"{cid}:{sid}")

        # Enacted reduction -> current model status parity.
        reductions = [as_dict(x) for x in as_list(dep_root.get("enacted_reductions"))]
        reduction_cases = sorted({scalar_text(as_dict(r.get("pressure_ref")).get("case")) for r in reductions if scalar_text(as_dict(r.get("pressure_ref")).get("case"))})
        missing_reduction_cases = [cid for cid in reduction_cases if cid not in corpus.case_by_id]
        model_reduction_root = as_dict(corpus.model_data.get("current_reduction_status"))
        model_components = as_dict(model_reduction_root.get("components"))
        reduction_model_mismatches: List[str] = []
        for reduction in reductions:
            target = scalar_text(reduction.get("target"))
            expected = scalar_text(reduction.get("to"))
            actual = scalar_text(as_dict(model_components.get(target)).get("current_status"))
            if target and expected != actual:
                reduction_model_mismatches.append(f"{target}:{expected!r}!={actual!r}")

        # Case-level architecture_effect is separate from local result and must
        # match case-linked enacted reductions where such reductions exist.
        reduction_targets_by_case: Dict[str, set[str]] = {}
        for reduction in reductions:
            cid = scalar_text(as_dict(reduction.get("pressure_ref")).get("case"))
            if cid:
                reduction_targets_by_case.setdefault(cid, set()).add(scalar_text(reduction.get("target")))
        architecture_effect_mismatches: List[str] = []
        for case in corpus.cases:
            declared_targets = set(case.architecture_targets) if case.architecture_applies else set()
            enacted_targets = reduction_targets_by_case.get(case.case_id, set())
            if declared_targets != enacted_targets:
                architecture_effect_mismatches.append(
                    f"{case.case_id}:case={sorted(declared_targets)} dependency_map={sorted(enacted_targets)}"
                )

        # Declared local file references in model load_order. PMS.yaml is an
        # external hard dependency and is therefore not expected inside VECTOR.
        declared_path_warnings: List[str] = []
        load_order = as_dict(corpus.model_data.get("load_order"))
        for step in [as_dict(x) for x in as_list(load_order.get("required_sequence"))]:
            ref = scalar_text(step.get("source"))
            if not ref or ref == "PMS.yaml":
                continue
            if "*" in ref:
                prefix = ref.split("*", 1)[0]
                if not any(path.startswith(prefix) for path in corpus.documents):
                    declared_path_warnings.append(ref)
            elif ref not in corpus.documents:
                declared_path_warnings.append(ref)

        load_bearing = sorted([case.case_id for case in corpus.cases if case.load_bearing], key=natural_sort_key)
        summary = {
            "source": corpus.source.describe(),
            "active_artifacts": corpus.document_count,
            "cases": len(corpus.cases),
            "case_ids_complete": not missing_cases and not extra_cases,
            "missing_cases": missing_cases,
            "extra_cases": extra_cases,
            "yaml_markdown_pairs_complete": not missing_pairs,
            "missing_pairs": missing_pairs,
            "case_top_level_exact": not bad_top_level,
            "bad_top_level": bad_top_level,
            "canonical_17_block_envelope": not bad_block_counts,
            "bad_17_block_cases": bad_block_counts,
            "index_case_parity": not index_case_mismatches,
            "index_case_mismatches": index_case_mismatches,
            "load_bearing_cases": load_bearing,
            "load_bearing_roots": roots,
            "dependency_endpoint_integrity": not missing_edge_endpoints,
            "missing_dependency_endpoints": missing_edge_endpoints,
            "provenance_claims": len(claims),
            "provenance_status_domain": not bad_provenance_statuses,
            "bad_provenance_statuses": bad_provenance_statuses,
            "duplicate_provenance_ids": duplicate_provenance_ids,
            "unresolved_provenance_sources": unresolved_provenance_sources,
            "enacted_reductions": len(reductions),
            "reduction_cases": reduction_cases,
            "missing_reduction_cases": missing_reduction_cases,
            "reduction_model_parity": not reduction_model_mismatches,
            "reduction_model_mismatches": reduction_model_mismatches,
            "architecture_effect_parity": not architecture_effect_mismatches,
            "architecture_effect_mismatches": architecture_effect_mismatches,
            "declared_path_warnings": declared_path_warnings,
            "reader_boundary": "repository consistency != VECTOR validity",
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True))
        ok = (
            len(corpus.cases) == 23
            and not missing_cases and not extra_cases and not missing_pairs
            and not bad_top_level and not bad_block_counts and not index_case_mismatches
            and load_bearing == ["E20", "E21", "E22", "E23"]
            and len(roots) == 6 and not missing_edge_endpoints
            and len(claims) > 0 and not bad_provenance_statuses
            and not duplicate_provenance_ids and not unresolved_provenance_sources
            and not missing_reduction_cases and not reduction_model_mismatches
            and not architecture_effect_mismatches
        )
        # declared_path_warnings are reported but non-fatal because the Reader
        # can remain usable while a downstream repository path is being repaired.
        return 0 if ok else 1
    finally:
        source.close()


def main() -> None:
    if "--self-test" in sys.argv:
        globals()["DEBUG"] = False
    dbg(f"main: argv={sys.argv}")
    positional = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    initial_source = Path(positional[0]) if positional else None
    if "--self-test" in sys.argv:
        raise SystemExit(run_self_test(initial_source))
    app = PmsVectorReaderApp(initial_source=initial_source)
    app.mainloop()
    dbg("main: mainloop exited")


if __name__ == "__main__":
    main()
