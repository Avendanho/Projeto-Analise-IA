#!/usr/bin/env python3
"""Multi-session parallel orchestrator for paper-fetch with clean, elegant terminal UI.

Features:
- Clean, non-flooded console display (suppresses raw JSON/debug traces).
- Informative status per item: attempt number, resolved database, outcome, and PDF filename.
- Visual dynamic progress bar with Unicode smoothing and live counters.
- Pixel-perfect box border alignment with ANSI code stripping and East Asian width handling.
- Instant skip: checks if a valid PDF already exists in cache/disk and skips without network calls.
- Anti-freeze failover: fast timeouts and staggered worker concurrency.
- Unified consolidated reporting: generates 'Relatório.txt' and 'Relatório_artigos_por_nome.txt'.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
import uuid
from collections import Counter
from pathlib import Path

# Add project root to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

import fetch as fetch_module
from .bypass403 import validate_pdf_data
from fetch import (
    _filename,
    _load_dois_and_titles_from_file,
    _resolve_title,
    _slug,
    _write_title_report,
    generate_detailed_report,
    fetch,
)

import logging
for _log_name in ("libgen_api_enhanced", "libgen_api", "urllib3", "pypdf", "requests", "playwright"):
    logging.getLogger(_log_name).setLevel(logging.CRITICAL)

# Silence raw JSON events from fetch.py during parallel runs
fetch_module._format = "silent"


# ---------------------------------------------------------------------------
# Terminal Styling & Width Calculations
# ---------------------------------------------------------------------------

def _use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") == "1":
        return True
    return sys.stdout.isatty() or sys.stderr.isatty()


USE_COLOR = _use_color()


class Style:
    RESET = "[0m" if USE_COLOR else ""
    BOLD = "[1m" if USE_COLOR else ""
    DIM = "[2m" if USE_COLOR else ""
    ITALIC = "[3m" if USE_COLOR else ""
    UNDERLINE = "[4m" if USE_COLOR else ""

    # Foreground Colors
    BLACK = "[30m" if USE_COLOR else ""
    RED = "[31m" if USE_COLOR else ""
    GREEN = "[32m" if USE_COLOR else ""
    YELLOW = "[33m" if USE_COLOR else ""
    BLUE = "[34m" if USE_COLOR else ""
    MAGENTA = "[35m" if USE_COLOR else ""
    CYAN = "[36m" if USE_COLOR else ""
    WHITE = "[37m" if USE_COLOR else ""
    GRAY = "[90m" if USE_COLOR else ""

    # High Intensity Bold Colors
    B_RED = "[1;91m" if USE_COLOR else ""
    B_GREEN = "[1;92m" if USE_COLOR else ""
    B_YELLOW = "[1;93m" if USE_COLOR else ""
    B_BLUE = "[1;94m" if USE_COLOR else ""
    B_MAGENTA = "[1;95m" if USE_COLOR else ""
    B_CYAN = "[1;96m" if USE_COLOR else ""
    B_WHITE = "[1;97m" if USE_COLOR else ""


ANSI_REGEX = re.compile(r"\[[0-9;]*[a-zA-Z]")

# Code points of double-width symbols/emojis
EMOJI_DOUBLE_WIDTH = {0x2705, 0x274C, 0x26A1, 0x1F680, 0x1F4CA, 0x1F4C4, 0x23F1, 0x23F3, 0x2B50, 0x1F4E6, 0x1F4C2}


def visible_width(s: str) -> int:
    """Calculate the exact terminal printable width of a string by stripping ANSI codes."""
    clean = ANSI_REGEX.sub("", s)
    w = 0
    for ch in clean:
        code = ord(ch)
        if unicodedata.east_asian_width(ch) in ("W", "F") or code in EMOJI_DOUBLE_WIDTH:
            w += 2
        else:
            w += 1
    return w


def render_box(lines: list[str], border_color: str = Style.B_MAGENTA, inner_width: int = 66) -> str:
    """Render a perfectly aligned terminal box."""
    out = []
    top = f"{border_color}╭{'─' * (inner_width + 2)}╮{Style.RESET}"
    out.append(top)

    for line in lines:
        if line == "---SEP---":
            sep = f"{border_color}├{'─' * (inner_width + 2)}┤{Style.RESET}"
            out.append(sep)
        else:
            w = visible_width(line)
            pad_len = max(0, inner_width - w)
            formatted = f"{border_color}│{Style.RESET} {line}{' ' * pad_len} {border_color}│{Style.RESET}"
            out.append(formatted)

    bot = f"{border_color}╰{'─' * (inner_width + 2)}╯{Style.RESET}"
    out.append(bot)
    return "\n".join(out)


SOURCE_NAMES = {
    "unpaywall": "Unpaywall",
    "europe_pmc": "Europe PMC",
    "pmc": "PubMed Central",
    "openalex": "OpenAlex",
    "semantic_scholar": "Semantic Scholar",
    "scihub": "Sci-Hub",
    "libgen": "LibGen",
    "annas_archive": "Anna's Archive",
    "arxiv": "arXiv",
    "biorxiv": "bioRxiv",
    "medrxiv": "medRxiv",
    "crossref": "CrossRef",
    "cache": "Cache",
    "publisher_direct": "Publisher Direct",
    "elsevier": "Elsevier",
    "elsevier_api": "Elsevier API",
    "core": "CORE",
}


def _format_source_name(src: str | None) -> str:
    if not src:
        return "Desconhecido"
    return SOURCE_NAMES.get(src.lower(), src.replace("_", " ").title())


def _format_duration(seconds: float) -> str:
    mins, secs = divmod(int(seconds), 60)
    if mins > 0:
        return f"{mins}m {secs:02d}s"
    return f"{seconds:.1f}s"


# ---------------------------------------------------------------------------
# Progress Bar & Thread-Safe UI Renderer
# ---------------------------------------------------------------------------

class TerminalProgressBar:
    def __init__(self, total: int):
        self.total = max(1, total)
        self.current = 0
        self.downloaded = 0
        self.cached = 0
        self.failed = 0
        self.sources_count: Counter[str] = Counter()
        self.start_time = time.monotonic()
        self.active_items: set[str] = set()
        self.lock = threading.Lock()
        self._last_update_time = time.monotonic()
        self._running = True
        self._ticker = threading.Thread(target=self._heartbeat, daemon=True)
        self._ticker.start()

    def _heartbeat(self) -> None:
        spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spin_i = 0
        while self._running:
            time.sleep(2.5)
            with self.lock:
                if not self._running or self.current >= self.total:
                    break
                if time.monotonic() - self._last_update_time >= 3.0:
                    icon = spinner[spin_i % len(spinner)]
                    spin_i += 1
                    active_n = len(self.active_items)
                    elapsed = time.monotonic() - self.start_time
                    act_str = f" ({active_n} threads ativas)" if active_n > 0 else ""
                    print(
                        f"  {Style.B_YELLOW}{icon}{Style.RESET} {Style.DIM}Buscando nas bases de dados científicas{act_str}... "
                        f"[{_format_duration(elapsed)}]{Style.RESET}",
                        flush=True,
                    )
                    self._last_update_time = time.monotonic()

    def stop(self) -> None:
        self._running = False

    def on_start(self, item_id: str) -> None:
        with self.lock:
            short_id = item_id[:35] + "..." if len(item_id) > 38 else item_id
            self.active_items.add(short_id)

    def update(self, result: dict) -> None:
        with self.lock:
            self._last_update_time = time.monotonic()
            self.current += 1
            success = result.get("success", False)
            skipped = result.get("skipped", False)
            source = result.get("source", "desconhecido")
            doi_or_title = result.get("doi") or result.get("searched_title") or "?"
            file_path = result.get("file")
            filename = Path(file_path).name if file_path else ""

            # Format Item Identifier
            if len(doi_or_title) > 36:
                short_id = doi_or_title[:33] + "..."
            else:
                short_id = doi_or_title.ljust(36)

            self.active_items.discard(doi_or_title[:35] + "..." if len(doi_or_title) > 38 else doi_or_title)

            # Update stats
            if success:
                if skipped:
                    self.cached += 1
                    status_badge = f"{Style.B_YELLOW}⚡ [{_format_source_name(source):^12}]{Style.RESET}"
                else:
                    self.downloaded += 1
                    self.sources_count[_format_source_name(source)] += 1
                    status_badge = f"{Style.B_GREEN}✅ [{_format_source_name(source):^12}]{Style.RESET}"
            else:
                self.failed += 1
                status_badge = f"{Style.B_RED}❌ [{'NÃO ENCONTRADO':^12}]{Style.RESET}"

            # Format Filename or Error
            if filename:
                if len(filename) > 38:
                    short_fname = filename[:35] + "..."
                else:
                    short_fname = filename
                target_str = f"{Style.GRAY}→{Style.RESET} {Style.WHITE}{short_fname}{Style.RESET}"
            else:
                target_str = f"{Style.DIM}(Esgotado em todas as bases){Style.RESET}"

            # 1. Print completed item line
            item_num = f"{Style.DIM}[{self.current:03d}/{self.total:03d}]{Style.RESET}"
            print(f"{item_num} {status_badge} {Style.CYAN}{short_id}{Style.RESET} {target_str}", flush=True)

            # 2. Render Live Progress Bar
            self._render_bar()

    def _render_bar(self) -> None:
        pct = (self.current / self.total) * 100.0
        bar_width = 24
        filled = int(bar_width * (self.current / self.total))
        remainder = (bar_width * (self.current / self.total)) - filled
        part_blocks = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
        part_char = part_blocks[int(remainder * 8)] if filled < bar_width else ""
        empty = bar_width - filled - (1 if part_char else 0)
        bar_str = f"{Style.B_CYAN}{'█' * filled}{part_char}{Style.GRAY}{'░' * max(0, empty)}{Style.RESET}"

        elapsed = time.monotonic() - self.start_time
        speed = self.current / elapsed if elapsed > 0 else 0

        bar_line = (
            f"   {Style.BOLD}Progresso:{Style.RESET} [{bar_str}] "
            f"{Style.B_WHITE}{pct:5.1f}%{Style.RESET} ({self.current}/{self.total}) "
            f"│ {Style.GREEN}✅ {self.downloaded}{Style.RESET} "
            f"│ {Style.YELLOW}⚡ {self.cached}{Style.RESET} "
            f"│ {Style.RED}❌ {self.failed}{Style.RESET} "
            f"│ {Style.DIM}{speed:.1f} it/s{Style.RESET}"
        )
        print(f"{bar_line}\n", flush=True)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Cache & Index Management
# ---------------------------------------------------------------------------

import sqlite3

_INDEX_LOCK = threading.Lock()

def _get_db(out_dir: Path):
    db_path = out_dir / ".paper_fetch_index.db"
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS downloads (doi TEXT PRIMARY KEY, filepath TEXT)")
    return conn

def _load_download_index(out_dir: Path) -> dict[str, str]:
    with _INDEX_LOCK:
        try:
            conn = _get_db(out_dir)
            rows = conn.execute("SELECT doi, filepath FROM downloads").fetchall()
            conn.close()
            return {row[0]: row[1] for row in rows}
        except Exception:
            return {}

def _record_download_index(out_dir: Path, doi: str, filepath: str) -> None:
    with _INDEX_LOCK:
        try:
            conn = _get_db(out_dir)
            conn.execute("INSERT OR REPLACE INTO downloads (doi, filepath) VALUES (?, ?)", (doi, filepath))
            conn.close()
            
            # Export Basic BibTeX
            bib_path = out_dir / "bibliografia.bib"
            with open(bib_path, "a", encoding="utf-8") as f:
                f.write(f"@article{{{doi.replace('/', '_')},\n  doi = {{{doi}}},\n  file = {{{filepath}}}\n}}\n\n")
        except Exception:
            pass


def _is_valid_disk_pdf(p: Path) -> bool:
    try:
        if not p.is_file() or p.stat().st_size < 1024:
            return False
        with open(p, "rb") as f:
            header = f.read(10)
            if not header.startswith(b"%PDF"):
                return False
        data = p.read_bytes()
        valid, _, _ = validate_pdf_data(data)
        return valid
    except Exception:
        return False


def _check_already_downloaded(doi: str, out_dir: Path) -> Path | None:
    if not out_dir.exists():
        return None

    # 1. Check index cache first (0ms)
    idx = _load_download_index(out_dir)
    fname = idx.get(doi.strip().lower())
    if fname:
        p = out_dir / Path(fname).name
        if p.is_file() and _is_valid_disk_pdf(p):
            return p

    # 2. Check filename matches in out_dir
    doi_slug = _slug(doi, n=20).lower()
    for existing in out_dir.glob("*.pdf"):
        if doi_slug in existing.name.lower():
            if _is_valid_disk_pdf(existing):
                _record_download_index(out_dir, doi, existing.name)
                return existing
    return None


# ---------------------------------------------------------------------------
# Parallel Worker Handlers with Connection Jitter
# ---------------------------------------------------------------------------

def _process_single_doi(
    doi: str,
    out_dir: Path,
    *,
    progress: TerminalProgressBar | None = None,
    sources: list[str] | None = None,
    overwrite: bool = False,
    timeout: int = 25,
) -> dict:
    if not overwrite:
        existing = _check_already_downloaded(doi, out_dir)
        if existing:
            return {
                "doi": doi,
                "success": True,
                "source": "cache",
                "pdf_url": None,
                "file": str(existing),
                "meta": {"title": doi},
                "sources_tried": [],
                "skipped": True,
                "skip_reason": "file_exists",
            }

    if progress:
        progress.on_start(doi)

    # Small random jitter (0.05s - 0.25s) to avoid burst host collisions
    time.sleep(random.uniform(0.05, 0.25))

    res = fetch(
        doi,
        out_dir,
        dry_run=False,
        overwrite=overwrite,
        timeout=timeout,
        sources=sources,
    )
    if res.get("success") and res.get("file"):
        _record_download_index(out_dir, doi, res["file"])
    return res


def _process_single_title(
    title: str,
    out_dir: Path,
    *,
    progress: TerminalProgressBar | None = None,
    sources: list[str] | None = None,
    overwrite: bool = False,
    timeout: int = 25,
) -> dict:
    if progress:
        progress.on_start(title)

    time.sleep(random.uniform(0.05, 0.25))

    resolved_doi, title_resolution = _resolve_title(title, timeout=timeout)
    if not resolved_doi:
        # New direct-title recovery path: do NOT require a DOI to exist.
        # This is especially important for legacy proceedings, reports,
        # repository records, and articles whose DOI metadata is incomplete.
        try:
            direct_recovery = getattr(fetch_module, "fetch_title_direct", None)
            if callable(direct_recovery):
                recovery = direct_recovery(
                    title,
                    out_dir,
                    timeout=timeout,
                    overwrite=overwrite,
                    sources=sources,
                )
                if recovery.get("success"):
                    recovery["searched_title"] = title
                    recovery["title_resolution"] = title_resolution
                    if recovery.get("file"):
                        direct_doi = recovery.get("resolved_doi")
                        if direct_doi:
                            _record_download_index(out_dir, direct_doi, recovery["file"])
                    return recovery
            # Se falhar nas bases acadêmicas, tenta nas patentes!
            try:
                import patent_fetch
                patent_result = patent_fetch.download_patent_by_title(title, out_dir, timeout=timeout)
                if patent_result.get("success"):
                    return {
                        "searched_title": title,
                        "resolved_doi": patent_result.get("patent_id"),
                        "success": True,
                        "source": "google_patents",
                        "pdf_url": "DuckDuckGo + Google Patents",
                        "file": patent_result.get("filepath"),
                        "meta": {"title": title, "patent_id": patent_result.get("patent_id")},
                        "sources_tried": title_resolution.get("resolvers_tried", []) + ["google_patents"],
                        "title_resolution": title_resolution,
                    }
            except Exception as pat_exc:
                pass

            return {
                "searched_title": title,
                "resolved_doi": None,
                "success": False,
                "source": "title_recovery",
                "pdf_url": None,
                "file": None,
                "meta": {"title": title},
                "sources_tried": title_resolution.get("resolvers_tried", []),
                "title_resolution": title_resolution,
                "error": {
                    "code": "title_resolve_failed",
                    "message": f"Could not resolve title to DOI or recover a PDF directly: {title!r}",
                    "retryable": True,
                },
            }
        except Exception as exc:
            return {
                "searched_title": title,
                "resolved_doi": None,
                "success": False,
                "source": "title_recovery",
                "pdf_url": None,
                "file": None,
                "meta": {"title": title},
                "sources_tried": title_resolution.get("resolvers_tried", []),
                "title_resolution": title_resolution,
                "error": {
                    "code": "title_recovery_error",
                    "message": str(exc),
                    "retryable": True,
                },
            }

    if not overwrite:
        existing = _check_already_downloaded(resolved_doi, out_dir)
        if existing:
            return {
                "searched_title": title,
                "resolved_doi": resolved_doi,
                "success": True,
                "source": "cache",
                "pdf_url": None,
                "file": str(existing),
                "meta": {"title": title},
                "sources_tried": [],
                "skipped": True,
                "skip_reason": "file_exists",
                "title_resolution": title_resolution,
            }

    result = fetch(
        resolved_doi,
        out_dir,
        dry_run=False,
        overwrite=overwrite,
        timeout=timeout,
        sources=sources,
    )
    result["searched_title"] = title
    result["resolved_doi"] = resolved_doi
    result["title_resolution"] = title_resolution
    if result.get("success") and result.get("file"):
        _record_download_index(out_dir, resolved_doi, result["file"])
    return result


# ---------------------------------------------------------------------------
# Main Parallel Orchestrator
# ---------------------------------------------------------------------------

def run_parallel_workers(
    dois: list[str],
    titles: list[str],
    out_dir: Path,
    *,
    max_workers: int = 4,
    sources: list[str] | None = None,
    overwrite: bool = False,
    timeout: int = 25,
) -> tuple[list[dict], list[dict]]:
    doi_results: list[dict] = []
    title_results: list[dict] = []
    total_items = len(dois) + len(titles)
    start_time = time.monotonic()

    # 1. Print Welcome Banner
    banner_lines = [
        f"{Style.B_WHITE}🚀 DOWNLOAD PARALELO DE ARTIGOS CIENTÍFICOS{Style.RESET}",
        f"{Style.DIM}📦 Total de itens:{Style.RESET} {Style.BOLD}{total_items}{Style.RESET} ({len(dois)} DOIs | {len(titles)} Títulos)",
        f"{Style.DIM}⚡ Sessões paralelas:{Style.RESET} {Style.BOLD}{max_workers} workers{Style.RESET}",
        f"{Style.DIM}📂 Destino:{Style.RESET} {Style.CYAN}{str(out_dir.resolve())[:46]}{Style.RESET}",
    ]
    print("\n" + render_box(banner_lines, border_color=Style.B_CYAN, inner_width=66) + "\n")

    progress = TerminalProgressBar(total_items)

    # 2. Process All Items Concurrently in Unified Worker Pool
    items: list[tuple[str, str]] = [("doi", d) for d in dois] + [("title", t) for t in titles]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {}
        for it_type, it_val in items:
            if it_type == "doi":
                fut = executor.submit(
                    _process_single_doi,
                    it_val,
                    out_dir,
                    progress=progress,
                    sources=sources,
                    overwrite=overwrite,
                    timeout=timeout,
                )
            else:
                fut = executor.submit(
                    _process_single_title,
                    it_val,
                    out_dir,
                    progress=progress,
                    sources=sources,
                    overwrite=overwrite,
                    timeout=timeout,
                )
            future_to_item[fut] = (it_type, it_val)

        for future in concurrent.futures.as_completed(future_to_item):
            it_type, it_val = future_to_item[future]
            try:
                res = future.result()
            except Exception as exc:
                res = {
                    ("doi" if it_type == "doi" else "searched_title"): it_val,
                    "success": False,
                    "source": "error",
                    "error": {"code": "worker_exception", "message": str(exc)},
                }
            if it_type == "doi":
                doi_results.append(res)
            else:
                title_results.append(res)
            progress.update(res)

    progress.stop()

    # 3. Generate Reports
    report_dois_path = SCRIPT_DIR / "Relatório.txt"
    report_titles_path = SCRIPT_DIR / "Relatório_artigos_por_nome.txt"

    for rp in (report_dois_path, report_titles_path):
        if rp.is_dir():
            try:
                import shutil
                shutil.rmtree(rp, ignore_errors=True)
            except Exception:
                pass

    if doi_results:
        try:
            report_content = generate_detailed_report(doi_results, dois)
            report_dois_path.write_text(report_content, encoding="utf-8")
        except Exception:
            pass

    if title_results:
        try:
            _write_title_report(title_results, titles, report_titles_path)
        except Exception:
            pass

    # 5. Print Final Summary Box
    total_elapsed = time.monotonic() - start_time
    total_success = progress.downloaded + progress.cached

    summary_lines = [
        f"{Style.B_WHITE}                 📊 RESUMO FINAL DA EXECUÇÃO{Style.RESET}",
        "---SEP---",
        f"{Style.BOLD}Total Processado:{Style.RESET}        {total_items} itens",
        f"{Style.B_GREEN}✅ Sucessos:{Style.RESET}               {total_success} artigos ({progress.downloaded} novos + {progress.cached} no cache)",
    ]

    for src_name, count in progress.sources_count.most_common():
        summary_lines.append(f"   • {src_name}: {count}")

    err_style = Style.B_RED if progress.failed > 0 else Style.DIM
    summary_lines.append(f"{err_style}❌ Não Encontrados:{Style.RESET}        {progress.failed} artigos")
    summary_lines.append(f"{Style.BOLD}⏱️  Tempo Total:{Style.RESET}            {_format_duration(total_elapsed)}")
    summary_lines.append("---SEP---")
    if doi_results:
        summary_lines.append(f"{Style.GREEN}📄 Relatório DOIs:{Style.RESET}       Relatório.txt")
    if title_results:
        summary_lines.append(f"{Style.GREEN}📄 Relatório Títulos:{Style.RESET}    Relatório_artigos_por_nome.txt")

    
    print("\n" + render_box(summary_lines, border_color=Style.B_MAGENTA, inner_width=66) + "\n")
    
    if progress.failed > 0:
        print(f"{Style.B_YELLOW}Há {progress.failed} itens não encontrados. Deseja realizar uma Busca Profunda Complementar (PubMed & NTRS)? [s/N]{Style.RESET}")
        if sys.stdin.isatty():
            try:
                # Cross-platform input with timeout (select.select crashes on Windows)
                _input_result = [None]
                def _read_input():
                    try: _input_result[0] = input().strip().lower()
                    except (EOFError, OSError): pass
                _t = threading.Thread(target=_read_input, daemon=True)
                _t.start()
                _t.join(timeout=30)
                if _input_result[0] == 's':
                    deep_search_path = SCRIPT_DIR / "deep_search.py"
                    subprocess.run([sys.executable, str(deep_search_path)])
            except Exception:
                pass
        else:
            print(f"{Style.DIM}(Modo não-interativo detectado. Para iniciar, use o botão na interface Web ou rode deep_search.py manualmente.){Style.RESET}\n")


    return doi_results, title_results



# ===========================================================================
# ADDITIVE ORDER-SAFE PARALLEL ORCHESTRATOR
# ===========================================================================
# The original implementation above is intentionally preserved. This
# replacement is resolved by Python's late global lookup when main() calls
# run_parallel_workers(). Each job carries a stable input_index, and every
# result is stored back into its original slot before reporting.

SOURCE_NAMES.update({
    "openaire": "OpenAIRE",
    "hal": "HAL",
    "zenodo": "Zenodo",
    "datacite": "DataCite",
    "doaj": "DOAJ",
})


def _safe_result_item_id(result: dict) -> str:
    return str(result.get("doi") or result.get("searched_title") or result.get("title") or "?")


_run_parallel_workers_original = run_parallel_workers


def run_parallel_workers(
    dois: list[str],
    titles: list[str],
    out_dir: Path,
    *,
    max_workers: int = 4,
    sources: list[str] | None = None,
    overwrite: bool = False,
    timeout: int = 25,
) -> tuple[list[dict], list[dict]]:
    total_items = len(dois) + len(titles)
    start_time = time.monotonic()

    banner_lines = [
        f"{Style.B_WHITE}🚀 DOWNLOAD PARALELO DE ARTIGOS CIENTÍFICOS (EXPANDIDO){Style.RESET}",
        f"{Style.DIM}📦 Total de itens:{Style.RESET} {Style.BOLD}{total_items}{Style.RESET} ({len(dois)} DOIs | {len(titles)} Títulos)",
        f"{Style.DIM}🔎 Fontes adicionais:{Style.RESET} OpenAIRE • HAL • Zenodo • DataCite • DOAJ",
        f"{Style.DIM}⚡ Sessões paralelas:{Style.RESET} {Style.BOLD}{max_workers} workers{Style.RESET}",
        f"{Style.DIM}📂 Destino:{Style.RESET} {Style.CYAN}{str(out_dir.resolve())[:46]}{Style.RESET}",
    ]
    print("\n" + render_box(banner_lines, border_color=Style.B_CYAN, inner_width=66) + "\n")

    progress = TerminalProgressBar(total_items)

    # Stable index is the central fix: completion order is irrelevant.
    indexed_items: list[tuple[int, str, str]] = []
    idx = 0
    for doi in dois:
        indexed_items.append((idx, "doi", doi))
        idx += 1
    for title in titles:
        indexed_items.append((idx, "title", title))
        idx += 1

    results_by_index: dict[int, dict] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_job: dict[concurrent.futures.Future, tuple[int, str, str]] = {}
        for input_index, item_type, item_value in indexed_items:
            if item_type == "doi":
                future = executor.submit(
                    _process_single_doi,
                    item_value,
                    out_dir,
                    progress=progress,
                    sources=sources,
                    overwrite=overwrite,
                    timeout=timeout,
                )
            else:
                future = executor.submit(
                    _process_single_title,
                    item_value,
                    out_dir,
                    progress=progress,
                    sources=sources,
                    overwrite=overwrite,
                    timeout=timeout,
                )
            future_to_job[future] = (input_index, item_type, item_value)

        for future in concurrent.futures.as_completed(future_to_job):
            input_index, item_type, item_value = future_to_job[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    ("doi" if item_type == "doi" else "searched_title"): item_value,
                    "success": False,
                    "source": "error",
                    "error": {
                        "code": "worker_exception",
                        "message": str(exc),
                        "retryable": True,
                    },
                }
            result["input_index"] = input_index
            result["input_type"] = item_type
            result["input_value"] = item_value
            results_by_index[input_index] = result
            progress.update(result)

    progress.stop()

    # Reconstruct results in ORIGINAL input order. No zip(as_completed())
    # mapping is ever used.
    ordered_results = [results_by_index[i] for i in range(total_items) if i in results_by_index]
    doi_results = [r for r in ordered_results if r.get("input_type") == "doi"]
    title_results = [r for r in ordered_results if r.get("input_type") == "title"]

    # Within each class, preserve original relative order.
    doi_results.sort(key=lambda r: r.get("input_index", 10**9))
    title_results.sort(key=lambda r: r.get("input_index", 10**9))

    report_dois_path = SCRIPT_DIR / "Relatório.txt"
    report_titles_path = SCRIPT_DIR / "Relatório_artigos_por_nome.txt"

    if doi_results:
        try:
            report_dois_path.write_text(
                generate_detailed_report(doi_results, [r.get("doi", "") for r in doi_results]),
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"{Style.B_YELLOW}⚠️ Falha ao gerar Relatório.txt: {exc}{Style.RESET}")

    if title_results:
        try:
            ordered_titles = [r.get("searched_title") or r.get("title") or "" for r in title_results]
            _write_title_report(title_results, ordered_titles, report_titles_path)
        except Exception as exc:
            print(f"{Style.B_YELLOW}⚠️ Falha ao gerar relatório de títulos: {exc}{Style.RESET}")

    total_elapsed = time.monotonic() - start_time
    total_success = progress.downloaded + progress.cached
    manual_or_recoverable = sum(
        1
        for r in ordered_results
        if isinstance(r.get("expanded_discovery"), dict)
        and r.get("expanded_discovery").get("status") == "exhausted"
        and not r.get("success")
    )

    summary_lines = [
        f"{Style.B_WHITE}                 📊 RESUMO FINAL DA EXECUÇÃO{Style.RESET}",
        "---SEP---",
        f"{Style.BOLD}Total Processado:{Style.RESET}        {total_items} itens",
        f"{Style.B_GREEN}✅ Sucessos:{Style.RESET}               {total_success} artigos ({progress.downloaded} novos + {progress.cached} no cache)",
    ]
    for src_name, count in progress.sources_count.most_common():
        summary_lines.append(f"   • {src_name}: {count}")
    summary_lines.append(f"{Style.B_RED if progress.failed else Style.DIM}❌ Falhas finais:{Style.RESET}          {progress.failed} artigos")
    summary_lines.append(f"{Style.B_YELLOW}🔎 Falhas com discovery expandido:{Style.RESET} {manual_or_recoverable}")
    summary_lines.append(f"{Style.BOLD}⏱️  Tempo Total:{Style.RESET}            {_format_duration(total_elapsed)}")
    summary_lines.append("---SEP---")
    if doi_results:
        summary_lines.append(f"{Style.GREEN}📄 Relatório DOIs:{Style.RESET}       Relatório.txt")
    if title_results:
        summary_lines.append(f"{Style.GREEN}📄 Relatório Títulos:{Style.RESET}    Relatório_artigos_por_nome.txt")

    
    print("\n" + render_box(summary_lines, border_color=Style.B_MAGENTA, inner_width=66) + "\n")
    
    if progress.failed > 0:
        print(f"{Style.B_YELLOW}Há {progress.failed} itens não encontrados. Deseja realizar uma Busca Profunda Complementar (PubMed & NTRS)? [s/N]{Style.RESET}")
        if sys.stdin.isatty():
            try:
                _input_result = [None]
                def _read_input():
                    try: _input_result[0] = input().strip().lower()
                    except (EOFError, OSError): pass
                _t = threading.Thread(target=_read_input, daemon=True)
                _t.start()
                _t.join(timeout=30)
                if _input_result[0] == 's':
                    deep_search_path = SCRIPT_DIR / "deep_search.py"
                    subprocess.run([sys.executable, str(deep_search_path)])
            except Exception:
                pass
        else:
            print(f"{Style.DIM}(Modo não-interativo detectado. Para iniciar, use o botão na interface Web ou rode deep_search.py manualmente.){Style.RESET}\n")

    return doi_results, title_results

# ===========================================================================
# END ADDITIVE ORDER-SAFE ORCHESTRATOR
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Multi-session parallel paper fetcher with clean terminal dashboard."
    )
    parser.add_argument(
        "--file",
        "-f",
        default="DOI's.txt",
        help="Input file containing DOIs and/or article titles (default: DOI's.txt)",
    )
    parser.add_argument(
        "--out",
        "-o",
        default="pdfs",
        help="Output directory for downloaded PDFs (default: pdfs)",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=4,
        help="Number of concurrent worker sessions (default: 4)",
    )
    parser.add_argument(
        "--sources",
        "-s",
        default=None,
        help="Comma-separated database sources to query (default: all sources)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download even if the PDF already exists in the output directory",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25,
        help="Timeout in seconds per HTTP request (default: 25)",
    )

    args = parser.parse_args()

    input_file = Path(args.file)
    if not input_file.is_file():
        candidate = SCRIPT_DIR / args.file
        if candidate.is_file():
            input_file = candidate
        else:
            print(f"{Style.B_RED}❌ Erro:{Style.RESET} Arquivo de entrada não encontrado: {args.file}")
            sys.exit(1)

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = SCRIPT_DIR / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    sources_list = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None

    dois, titles = _load_dois_and_titles_from_file(input_file)
    if not dois and not titles:
        print(f"{Style.B_YELLOW}⚠️ Aviso:{Style.RESET} Nenhum DOI ou título válido encontrado em {input_file}")
        sys.exit(0)

    run_parallel_workers(
        dois,
        titles,
        out_dir,
        max_workers=args.workers,
        sources=sources_list,
        overwrite=args.overwrite,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
