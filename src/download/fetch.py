#!/usr/bin/env python3
"""Fetch legal open-access PDFs by DOI.

Resolution order: Unpaywall -> Semantic Scholar openAccessPdf ->
arXiv -> PMC OA -> bioRxiv/medRxiv.

Exit codes:
  0  success (all DOIs resolved and downloaded / dry-run previewed)
  1  unresolved — one or more DOIs had no OA copy; no transport failure
  2  reserved for auth errors (currently unused; Unpaywall gracefully degrades)
  3  validation error (bad arguments, missing input)
  4  transport error — network / download / IO failure (retryable class)

If UNPAYWALL_EMAIL is not set, the Unpaywall source is skipped
and the remaining 4 sources are still tried.

Machine contract:
  stdout — one JSON object per invocation (or NDJSON with --stream)
  stderr — NDJSON progress events when --format json; prose when --format text

Contract-changing version of this file. The schema_version below is what the
`schema` subcommand reports and what appears in every response's `meta` slot;
agents that cache schema should compare against it to detect drift.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import html.parser
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from bypass403 import bypass_download_pdf, bypass_get, default_engine as bypass_engine, validate_pdf_data

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

CLI_VERSION = "0.16.0"
SCHEMA_VERSION = "1.12.0"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EMAIL = os.environ.get("UNPAYWALL_EMAIL", "baduarte@sga.pucminas.br").strip()
CORE_API_KEY = os.environ.get("CORE_API_KEY", "").strip()
# UA for API calls (Unpaywall requires contact email in the UA per their ToS).
UA = f"paper-fetch/{CLI_VERSION} (mailto:{EMAIL or 'anonymous'})"
# UA for PDF downloads — some publishers (e.g., iiarjournals.org) return
# HTTP 403 for non-browser User-Agents even on OA PDFs. Uses a generic
# modern browser identifier; the per-request Accept header still declares
# we want a PDF, and the host allowlist still restricts where we fetch.
DOWNLOAD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
DEFAULT_TIMEOUT = 30
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB

# Canonical DOI shape — kept here so build_schema() and runtime validation
# share one source of truth. Schema-side this is exposed as the regex below
# without the surrounding anchors.
DOI_PATTERN = r"^10\..+/.+$"
_DOI_RE = re.compile(DOI_PATTERN)


def normalize_doi(doi: str) -> str:
    """Normalize a DOI string to the canonical form: 10.xxxx/xxxx.

    Removes:
      - leading/trailing whitespace
      - protocol (http://, https://)
      - domain (doi.org, dx.doi.org)
      - the string "doi:" (case-insensitive) at the start
    """
    if not doi:
        return ""
    doi = doi.strip()
    # Remove protocol and domain
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    # Remove "doi:" prefix (case-insensitive)
    if doi.lower().startswith("doi:"):
        doi = doi[4:]
    return doi.lower()


def record_dois(record: dict) -> list[str]:
    """Extract DOI(s) from a CORE API record.

    Looks at:
      - record["doi"]
      - record["identifiers"] (if present and is a list) for entries that have a "doi" key or are DOIs themselves.
    Returns a list of DOI strings (may be empty).
    """
    dois = []
    # Direct doi field
    if record.get("doi"):
        dois.append(str(record["doi"]))
    # Identifiers field
    identifiers = record.get("identifiers")
    if isinstance(identifiers, list):
        for ident in identifiers:
            if isinstance(ident, dict) and ident.get("doi"):
                dois.append(str(ident["doi"]))
            elif isinstance(ident, str):
                # If the identifier is a string, check if it looks like a DOI
                ident_stripped = ident.strip()
                if ident_stripped.lower().startswith("doi:"):
                    identifico = ident_stripped[4:].strip()
                    if identifico:
                        dois.append(identifico)
                elif ident_stripped.lower().startswith("http://doi.org/") or ident_stripped.lower().startswith("https://doi.org/"):
                    # Extract the DOI part
                    if ident_stripped.lower().startswith("http://doi.org/"):
                        identifico = ident_stripped[17:].strip()
                    else:
                        identifico = ident_stripped[19:].strip()
                    if identifico:
                        dois.append(identifico)
    return dois

EXIT_SUCCESS = 0
EXIT_UNRESOLVED = 1
EXIT_AUTH = 2  # reserved
EXIT_VALIDATION = 3
EXIT_TRANSPORT = 4

# Per-error retry backoff hints surfaced to agents. Only set on retryable=True
# codes. Values are recommendations, not guarantees: an orchestrator that
# ignores them and retries sooner will at worst re-hit the same failure.
RETRY_AFTER_HOURS = {
    "not_found": 168,              # OA availability changes on embargo / preprint timescale
    "resolve_network_error": 1,    # resolver APIs unreachable; availability unknown
    "download_network_error": 1,   # transient network / upstream hiccup
    "download_size_exceeded": 24,  # publisher posted a >50 MB PDF; revisit in a day
    "download_io_error": 1,        # local disk full / permission blip
}

# ---------------------------------------------------------------------------
# Institutional mode
# ---------------------------------------------------------------------------

# Rate limit (institutional mode only — public OA sources are unmetered by
# their operators and do not need client-side pacing).
INSTITUTIONAL_RATE_PER_SEC = 1.0

# ---------------------------------------------------------------------------
# Sci-Hub fallback
# ---------------------------------------------------------------------------

# Default mirror list (snapshot from https://www.sci-hub.pub/ on 2026-04-26).
# Operator can override with PAPER_FETCH_SCIHUB_MIRRORS=sci-hub.ru,sci-hub.st,...
# When all configured mirrors miss, we re-scan SCIHUB_DISCOVERY_URL once per
# process for a fresh list.
SCIHUB_DEFAULT_MIRRORS = (
    "sci-hub.ru",
    "sci-hub.st",
    "sci-hub.su",
    "sci-hub.box",
    "sci-hub.red",
    "sci-hub.al",
    "sci-hub.mk",
    "sci-hub.ee",
)
SCIHUB_DISCOVERY_URL = "https://www.sci-hub.pub/"

# Mobile Safari UA for Sci-Hub HTML page fetches. Mobile clients tend to
# get a simpler page layout less likely to trigger CAPTCHA. Technique
# borrowed from ethanwillis/zotero-scihub.
SCIHUB_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 11_3_1 like Mac OS X) AppleWebKit/603.1.30 (KHTML, like Gecko) Version/10.0 Mobile/14E304 Safari/602.1"

# Polite per-host pacing for Sci-Hub mirror requests. Public OA APIs are
# unmetered; Sci-Hub mirrors throttle and CAPTCHA aggressively, so we pace
# Sci-Hub fetches independently of institutional mode.
SCIHUB_RATE_PER_SEC = 1.0
_last_scihub_request_monotonic: float = 0.0

# ---------------------------------------------------------------------------
# Libgen fallback
# ---------------------------------------------------------------------------

LIBGEN_DEFAULT_MIRRORS = (
    "https://libgen.li",
)
LIBGEN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
LIBGEN_RATE_PER_SEC = 2.0
_last_libgen_request_monotonic: float = 0.0


# Hostnames blocked in every mode. Covers two threat classes:
#   - loopback aliases that resolve to 127.0.0.1 / ::1 but pass the IP literal
#     check (the ip literal check only fires when the URL host IS an IP)
#   - cloud metadata endpoints that can leak IAM credentials if an SSRF
#     target pivoted into fetching from them
# A hostname that resolves into private space (whether by intent or DNS
# rebinding) is caught separately by _host_addrs_safe(), which getaddrinfo()s
# every fetch target and rejects private/loopback/link-local/reserved/
# unspecified answers. This set only short-circuits the well-known aliases.
#
# KEEP IN SYNC with the identical _BLOCKED_HOSTS set in cloak_pdf.py — the cloak
# companion runs as its own process and re-implements the SSRF gate rather than
# importing it, so a host added here must be added there too.
_BLOCKED_HOSTS = {
    # Loopback aliases
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    # Cloud metadata
    "metadata.google.internal",
    "metadata.aws.internal",
    "metadata",  # some cloud SDKs resolve bare 'metadata'
}


def _is_institutional() -> bool:
    """True iff the operator has opted the process into institutional mode."""
    val = os.environ.get("PAPER_FETCH_INSTITUTIONAL", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _auth_mode() -> str:
    return "institutional" if _is_institutional() else "public"


# ---------------------------------------------------------------------------
# CloakBrowser fallback (operator-controlled, off by default)
# ---------------------------------------------------------------------------

# When PAPER_FETCH_CLOAK is set, a download blocked by Cloudflare (HTTP 403/429
# or an HTML interstitial served in place of the PDF) is retried through
# CloakBrowser — a stealth Chromium that can pass the JS challenge. fetch.py
# shells out to the companion `cloak_pdf.py` via a cloakbrowser-importable
# Python, so this file keeps its stdlib-only footprint. Bytes returned by the
# helper are re-validated through the same %PDF + size checks as any other
# download; the agent cannot opt in (env var is an operator action).

# URLs that were ultimately downloaded via CloakBrowser, so the result envelope
# can flag `via: cloak` for orchestrator visibility.
_CLOAK_DOWNLOADS: set[str] = set()


def _is_cloak_enabled() -> bool:
    """True iff the operator opted into the CloakBrowser fallback."""
    return bool(os.environ.get("PAPER_FETCH_CLOAK"))


def _resolve_cloak_python() -> str | None:
    """Find a Python interpreter that can import the installed CloakBrowser package."""
    candidates = [
        os.environ.get("CLOAKBROWSER_PYTHON", "").strip(),
        sys.executable,
    ]
    seen: set[str] = set()

    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        if not (os.path.isfile(candidate) or shutil.which(candidate)):
            continue

        try:
            result = subprocess.run(
                [candidate, "-c", "import cloakbrowser"],
                capture_output=True,
                timeout=20,
            )
            if result.returncode == 0:
                return candidate
        except Exception:
            continue

    return None


def _cloak_fetch_pdf(url: str, *, timeout: int) -> bytes | None:
    """Fetch PDF bytes through CloakBrowser. Returns bytes, or None on failure.

    Shells out to the companion cloak_pdf.py via a cloakbrowser-importable
    Python. Fails closed: a missing dependency or any error returns None so the
    caller falls through to the next source.
    """
    py = _resolve_cloak_python()
    if not py:
        _progress("download_cloak_skip", url=url, reason="no_cloakbrowser_python")
        return None
    helper = Path(__file__).resolve().with_name("cloak_pdf.py")
    if not helper.exists():
        _progress("download_cloak_skip", url=url, reason="helper_missing")
        return None
    try:
        # Fast 12s cap so headless browser challenges never block worker threads
        cloak_timeout = max(5, min(timeout, 12))
        r = subprocess.run(
            [py, str(helper), url, str(cloak_timeout)],
            capture_output=True,
            timeout=cloak_timeout + 3,
        )
    except Exception as e:
        _progress("download_cloak_error", url=url, error=str(e))
        return None
    if r.returncode != 0 or not r.stdout:
        tail = r.stderr.decode("utf-8", "replace")[-200:] if r.stderr else ""
        _progress("download_cloak_error", url=url, reason="helper_failed", stderr=tail)
        return None
    return r.stdout


def _is_safe_url(url: str) -> tuple[bool, str]:
    """Universal URL safety check — applied in every mode.

    Returns (ok, reason). Blocks SSRF vectors regardless of whether the
    hostname would pass the allowlist check:
      - non-http(s) schemes (file://, ftp://, gopher://, etc.)
      - non-80/443 ports
      - IP literals in private / loopback / link-local / reserved /
        unspecified space
      - known cloud metadata hostnames
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False, "malformed_url"
    if parsed.scheme not in ("http", "https"):
        return False, "scheme_not_allowed"
    if parsed.port is not None and parsed.port not in (80, 443):
        return False, "port_not_allowed"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "empty_host"
    try:
        ip = ipaddress.ip_address(host)
        # Keep this address-class set in sync with _host_addrs_safe(); is_unspecified
        # blocks the 0.0.0.0 / :: literals, which route to localhost on many stacks.
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "private_ip"
    except ValueError:
        pass  # hostname is a name, not a literal — fine
    if host in _BLOCKED_HOSTS:
        return False, "blocked_host"
    return True, ""


def _host_addrs_safe(host: str) -> tuple[bool, str]:
    """Resolve `host` and reject if any address is in private space.

    `_is_safe_url` only inspects the literal host, so a public-looking
    hostname that resolves to a loopback / private / link-local / reserved
    address (the classic DNS-based SSRF vector) slips through. This closes
    that gap by checking every address the name resolves to. Fails closed:
    a name we cannot resolve is treated as not allowed.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False, "dns_error"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "private_ip"
    return True, ""


def _url_fetch_allowed(url: str) -> tuple[bool, str]:
    """Full pre-fetch gate: syntactic SSRF check + DNS-resolution check."""
    ok, reason = _is_safe_url(url)
    if not ok:
        return False, reason
    return _host_addrs_safe(urllib.parse.urlparse(url).hostname or "")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the SSRF gate on every redirect target.

    urllib follows 3xx redirects transparently, so a download URL that passes
    the initial check can still be bounced to loopback / a private host / cloud
    metadata. Validate each hop and refuse to follow an unsafe one.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, reason = _url_fetch_allowed(newurl)
        if not ok:
            raise urllib.error.HTTPError(newurl, code, f"unsafe_redirect:{reason}", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Install process-wide so every urllib.request.urlopen() — PDF downloads and
# API calls alike — validates redirect hops. Tests patch urlopen directly, so
# this opener is bypassed there and stays inert in hermetic runs.
urllib.request.install_opener(urllib.request.build_opener(_SafeRedirectHandler()))


# Simple per-process token bucket. Single-threaded, so no locking needed.
_last_request_monotonic: float = 0.0


def _rate_limit_gate() -> None:
    """Enforce INSTITUTIONAL_RATE_PER_SEC pacing. No-op in public mode.

    Runs before every outbound HTTP request in institutional mode so
    that a single process cannot inadvertently hammer a publisher's
    servers beyond the configured rate.
    """
    global _last_request_monotonic
    if not _is_institutional():
        return
    min_interval = 1.0 / INSTITUTIONAL_RATE_PER_SEC
    now = time.monotonic()
    wait = _last_request_monotonic + min_interval - now
    if wait > 0:
        time.sleep(wait)
        now = time.monotonic()
    _last_request_monotonic = now


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

# Global output state (set by main()).
_format = "json"
_pretty = False
_stream = False
_request_id = ""
_started_monotonic = 0.0


def _now_ms() -> int:
    return int((time.monotonic() - _started_monotonic) * 1000)


def _log_text(msg: str) -> None:
    """Human-readable diagnostic → stderr only (used in text mode)."""
    if _format == "silent":
        return
    print(msg, file=sys.stderr, flush=True)


def _progress(event: str, **fields) -> None:
    """Progress event on stderr.

    JSON mode emits NDJSON so orchestrators can parse stderr for liveness.
    Text mode emits prose for humans.
    Silent mode emits nothing.
    """
    if _format == "silent":
        return
    if _format == "json":
        payload = {"event": event, "request_id": _request_id, "elapsed_ms": _now_ms(), **fields}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
        return

    # Text mode — render a short human line.
    if event == "session":
        # Agent-only diagnostic; silent in human mode.
        return
    if event == "start":
        _log_text(f"==> {fields.get('doi', '?')}")
    elif event == "source_skip":
        _log_text(f"  [{fields.get('source', '?')}] skipped ({fields.get('reason', '?')})")
    elif event == "source_try":
        _log_text(f"  [{fields.get('source', '?')}] trying…")
    elif event == "source_hit":
        _log_text(f"  [{fields.get('source', '?')}] {fields.get('pdf_url', '?')}")
    elif event == "source_miss":
        _log_text(f"  [{fields.get('source', '?')}] no PDF")
    elif event == "download_error":
        reason = fields.get("reason", "?")
        status = fields.get("http_status")
        detail = fields.get("error")
        if status:
            _log_text(f"  download failed: {reason} (HTTP {status})")
        elif detail:
            _log_text(f"  download failed: {reason} ({detail})")
        else:
            _log_text(f"  download failed: {reason}")
    elif event == "download_ok":
        _log_text(f"  saved → {fields.get('file', '?')}")
    elif event == "download_skip":
        _log_text(f"  [skip-existing] {fields.get('file', '?')}")
    elif event == "dry_run":
        _log_text(f"  [dry-run] [{fields.get('source', '?')}] {fields.get('pdf_url', '?')} → {fields.get('file', '?')}")
    elif event == "not_found":
        _log_text(f"  no OA PDF found for {fields.get('doi', '?')}")
    else:
        # fall back
        _log_text(f"  [{event}] {fields}")


def _dump_json(obj: dict) -> str:
    if _pretty:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    return json.dumps(obj, ensure_ascii=False)


def _emit(obj: dict) -> None:
    """Final result → stdout as JSON or human-readable text."""
    if _format == "json":
        print(_dump_json(obj))
    else:
        _emit_text(obj)


def _emit_ndjson(obj: dict) -> None:
    """Per-item streaming line on stdout (--stream mode)."""
    print(_dump_json(obj), flush=True)


def _emit_text(obj: dict) -> None:
    """Render a result envelope as human-readable text on stdout."""
    ok = obj.get("ok")
    if ok is False:
        err = obj.get("error", {})
        print(f"error: [{err.get('code', '?')}] {err.get('message', '?')}")
        return

    data = obj.get("data", {})
    results = data.get("results", [data] if "doi" in data else [])
    for r in results:
        if r.get("skipped"):
            status = "skipped"
        elif r.get("dry_run"):
            status = "dry-run"
        elif r.get("success"):
            status = "saved"
        else:
            status = "failed"
        src = r.get("source") or "?"
        doi = r.get("doi", "?")
        target = r.get("file") or r.get("pdf_url") or "?"
        print(f"[{src}] {doi} → {target}  ({status})")
    summary = data.get("summary")
    if summary:
        print(f"\n{summary['succeeded']}/{summary['total']} succeeded  ({summary.get('failed', 0)} failed)")
    nxt = data.get("next") or []
    if nxt:
        print("\nnext:")
        for hint in nxt:
            print(f"  {hint}")


def _meta(extra: dict | None = None) -> dict:
    m = {
        "request_id": _request_id,
        "latency_ms": _now_ms(),
        "schema_version": SCHEMA_VERSION,
        "cli_version": CLI_VERSION,
        "auth_mode": _auth_mode(),
    }
    if extra:
        m.update(extra)
    return m


def _envelope_ok(data: dict, *, ok=True, meta_extra: dict | None = None) -> dict:
    return {"ok": ok, "data": data, "meta": _meta(meta_extra)}


def _envelope_err(code: str, message: str, *, retryable: bool = False, **ctx) -> dict:
    e = {"code": code, "message": message, "retryable": retryable}
    e.update(ctx)
    return {"ok": False, "error": e, "meta": _meta()}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get(url: str, *, accept: str = "application/json", timeout: int, user_agent: str | None = None) -> bytes:
    if _is_institutional():
        _rate_limit_gate()
    req = urllib.request.Request(url, headers={"User-Agent": user_agent or UA, "Accept": accept})
    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410, 401, 403):
                raise
            last_err = e
            if attempt == 0:
                time.sleep(0.5)
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as e:
            last_err = e
            if attempt == 0:
                time.sleep(0.5)
    if last_err is not None:
        raise last_err
    raise TimeoutError(f"Request timed out for {url}")


def _get_json(url: str, *, timeout: int):
    return json.loads(_get(url, timeout=timeout).decode("utf-8"))


def _scihub_rate_gate() -> None:
    """1 req/s pacing for Sci-Hub fetches, applied in every auth mode."""
    global _last_scihub_request_monotonic
    min_interval = 1.0 / SCIHUB_RATE_PER_SEC
    now = time.monotonic()
    wait = _last_scihub_request_monotonic + min_interval - now
    if wait > 0:
        time.sleep(wait)
        now = time.monotonic()
    _last_scihub_request_monotonic = now


def _is_allowed_host(url: str) -> bool:
    """Gatekeeper for any outbound PDF fetch.

    Only SSRF defense applies — private IPs, non-http(s) schemes, non-80/443
    ports, and cloud metadata hostnames are rejected. Everything else is
    allowed: the skill trusts URLs returned by the OA APIs it already called
    (Unpaywall, Semantic Scholar, bioRxiv, PMC), and the %PDF magic-byte +
    50 MB size checks in `_download` catch tampered responses.
    """
    ok, _reason = _is_safe_url(url)
    return ok


_DOWNLOAD_SESSION = None

def _get_download_session():
    global _DOWNLOAD_SESSION
    if _DOWNLOAD_SESSION is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        _DOWNLOAD_SESSION = requests.Session()
        retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET", "HEAD"])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        _DOWNLOAD_SESSION.mount("http://", adapter)
        _DOWNLOAD_SESSION.mount("https://", adapter)
        _DOWNLOAD_SESSION.headers.update({
            "User-Agent": DOWNLOAD_UA,
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
    return _DOWNLOAD_SESSION


def _download(url: str, dest: Path, *, timeout: int) -> str | None:
    """Download a PDF with streaming fast-path, GoByPASS403 fallback, and Cloak fallback."""
    allowed, deny_reason = _url_fetch_allowed(url)
    if not allowed:
        _progress("download_error", reason="host_not_allowed", url=url, detail=deny_reason)
        return "host_not_allowed"

    def _finalize(data: bytes) -> str | None:
        """Validate (%PDF magic, complete EOF trailer, pypdf syntax check, size cap) and write."""
        valid, clean_data, reason = validate_pdf_data(data)
        if not valid:
            _progress("download_error", reason=reason)
            return reason
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_name(f".{dest.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:6]}")
            tmp_dest.write_bytes(clean_data)
            
            import sys
            _ANALISEIA_DIR = Path(__file__).resolve().parent.parent / "analiseia"
            if str(_ANALISEIA_DIR.parent) not in sys.path:
                sys.path.insert(0, str(_ANALISEIA_DIR.parent))
            from analiseia.platform.filesystem import safe_replace
            
            safe_replace(tmp_dest, dest)
        except OSError as e:
            _progress("download_error", reason="io_error", error=str(e))
            return "io_error"
        return None

    def _try_cloak() -> bool:
        if not _is_cloak_enabled():
            return False
        data = _cloak_fetch_pdf(url, timeout=timeout)
        if not data or _finalize(data) is not None:
            return False
        _CLOAK_DOWNLOADS.add(url)
        _progress("download_cloak_ok", url=url, bytes=len(data))
        return True

    # 1. FAST PATH: Streaming download via requests Session
    session = _get_download_session()
    try:
        with session.get(url, stream=True, timeout=(5, timeout), allow_redirects=True) as r:
            if r.status_code == 200:
                iterator = r.iter_content(chunk_size=8192)
                try:
                    first_chunk = next(iterator)
                except StopIteration:
                    first_chunk = b""
                
                if first_chunk.startswith(b"%PDF"):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp_dest = dest.with_name(f".{dest.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:6]}")
                    with open(tmp_dest, "wb") as f:
                        f.write(first_chunk)
                        for chunk in iterator:
                            if chunk:
                                f.write(chunk)
                    
                    from analiseia.platform.filesystem import safe_replace
                    safe_replace(tmp_dest, dest)
                    _progress("download_stream_ok", url=url)
                    return None
                else:
                    pass
    except Exception as e:
        pass

    # 2. Secondary Attempt: GoByPASS403 Engine
    ok, err = bypass_download_pdf(url, dest, timeout=timeout)
    if ok:
        _progress("download_bypass403_ok", url=url)
        return None

    # 3. Third Attempt: CloakBrowser stealth if enabled
    if _is_cloak_enabled() and _try_cloak():
        return None

    last_error = err
    # Handle the last error we encountered
    if last_error == "not_a_pdf":
        _progress("download_error", reason="not_a_pdf", url=url)
        return "not_a_pdf"
    elif last_error == "size_exceeded":
        return "size_exceeded"
    elif last_error == "io_error":
        return "io_error"
    elif last_error:
        _progress("download_error", reason="network_error", url=url, error=str(last_error))
        return "network_error"
    else:
        _progress("download_error", reason="network_error", url=url)
        return "network_error"

# Filename helpers
# ---------------------------------------------------------------------------


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:n]


_JOURNAL_STOPWORDS = {"the", "of", "and", "for", "in", "on", "a", "an", "to", "&"}


def _journal_abbrev(name: str | None, max_len: int = 20) -> str:
    """ISO-style initials for 3+ words (PNAS, JACS, NEJM); CamelCase otherwise."""
    if not name:
        return ""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w and w.lower() not in _JOURNAL_STOPWORDS]
    if not words:
        return ""
    if len(words) >= 3:
        return "".join(w[0].upper() for w in words)[:max_len]
    return "".join(w[:1].upper() + w[1:] for w in words)[:max_len]


def _filename(meta: dict) -> str:
    author = _slug((meta.get("author") or "unknown").split()[-1], 20)
    year = str(meta.get("year") or "nd")
    journal = _journal_abbrev(meta.get("journal"))
    title = _slug(meta.get("title") or "paper", 40)
    parts = [author, year]
    if journal:
        parts.append(journal)
    parts.append(title)
    return "_".join(parts) + ".pdf"


# ---------------------------------------------------------------------------
# Source resolvers
# ---------------------------------------------------------------------------


def _is_transport_exc(exc: Exception) -> bool:
    """True if `exc` is a retryable transport failure rather than a genuine miss.

    A 404/410 from a resolver means the paper isn't indexed at that source — a
    real miss a retry won't fix. Anything else (timeout, connection reset, 5xx,
    403, malformed JSON) is a transport-class failure: the source might well
    have the paper, we just couldn't reach it, so it should not be reported as a
    permanent ``not_found``.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in (404, 410):
        return False
    return True


def try_unpaywall(doi: str, *, timeout: int, errors: list | None = None) -> tuple[str | None, dict]:
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={EMAIL}"
    try:
        d = _get_json(url, timeout=timeout)
    except Exception as e:
        _progress("source_miss", source="unpaywall", reason=str(e))
        if errors is not None and _is_transport_exc(e):
            errors.append({"source": "unpaywall", "detail": str(e)})
        return None, {}
    meta = {
        "title": d.get("title"),
        "year": d.get("year"),
        "author": (d.get("z_authors") or [{}])[0].get("family") if d.get("z_authors") else None,
        "journal": d.get("journal_name"),
    }
    loc = d.get("best_oa_location") or {}
    return loc.get("url_for_pdf"), meta


def try_semantic_scholar(doi: str, *, timeout: int, errors: list | None = None) -> tuple[str | None, dict, dict]:
    url = (
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi)}"
        "?fields=title,year,authors,openAccessPdf,externalIds,venue"
    )
    try:
        d = _get_json(url, timeout=timeout)
    except Exception as e:
        _progress("source_miss", source="semantic_scholar", reason=str(e))
        if errors is not None and _is_transport_exc(e):
            errors.append({"source": "semantic_scholar", "detail": str(e)})
        return None, {}, {}
    meta = {
        "title": d.get("title"),
        "year": d.get("year"),
        "author": (d.get("authors") or [{}])[0].get("name"),
        "journal": d.get("venue") or None,
    }
    pdf = (d.get("openAccessPdf") or {}).get("url")
    return pdf, meta, d.get("externalIds") or {}

def try_openalex(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve DOI para URLs de PDFs OA usando OpenAlex."""

    params = {
        "filter": f"doi:https://doi.org/{doi}",
        "per_page": "1",
        "select": (
            "title,publication_year,authorships,"
            "primary_location,best_oa_location,locations"
        ),
    }
    mailto = os.environ.get("OPENALEX_MAILTO") or EMAIL
    if mailto:
        params["mailto"] = mailto

    url = (
        "https://api.openalex.org/works?"
        + urllib.parse.urlencode(params)
    )

    try:
        data = _get_json(
            url,
            timeout=timeout,
        )

    except Exception as e:
        _progress(
            "source_miss",
            source="openalex",
            reason=str(e),
        )

        if errors is not None and _is_transport_exc(e):
            errors.append({
                "source": "openalex",
                "detail": str(e),
            })

        return [], {}

    resultados = data.get("results") or []

    if not resultados:
        return [], {}

    work = resultados[0]

    # -------------------------
    # Metadados
    # -------------------------

    authorships = work.get("authorships") or []

    first_author = None

    if authorships:
        author = authorships[0].get("author") or {}
        first_author = author.get("display_name")

    primary_location = (
        work.get("primary_location") or {}
    )

    source = (
        primary_location.get("source") or {}
    )

    meta = {
        "title": work.get("title"),
        "year": work.get("publication_year"),
        "author": first_author,
        "journal": source.get("display_name"),
    }

    # -------------------------
    # PDFs encontrados
    # -------------------------

    pdf_urls: list[str] = []

    def add_pdf(url: str | None) -> None:
        if not url:
            return

        if url not in pdf_urls:
            pdf_urls.append(url)

    # Primeiro a melhor localização OA escolhida pelo OpenAlex.
    best = work.get("best_oa_location") or {}

    if best.get("is_oa"):
        add_pdf(best.get("pdf_url"))

    # Depois todas as demais cópias abertas.
    for location in work.get("locations") or []:

        if not location.get("is_oa"):
            continue

        add_pdf(
            location.get("pdf_url")
        )

    return pdf_urls, meta

def try_core(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> list[str]:
    """Procura cópias de acesso aberto de um DOI usando a CORE API."""

    # Skip Core API if requested (e.g., in special mode to avoid delays)
    if os.environ.get("PAPER_FETCH_SKIP_CORE") == "1":
        return []

    if not CORE_API_KEY:
        return []

    # Use phrase-exact search to reduce false positives
    params = urllib.parse.urlencode({
        "q": f'"{doi}"',
        "limit": "10",
    })

    url = (
        "https://api.core.ac.uk/v3/search/works/?"
        + params
    )

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {CORE_API_KEY}",
            "User-Agent": UA,
            "Accept": "application/json",
        },
    )

    try:
        _rate_limit_gate()

        # Use shorter timeout for Core API to avoid long delays when it's slow/unresponsive
        core_timeout = min(timeout, 5)  # Max 3 seconds for Core API
        with urllib.request.urlopen(
            request,
            timeout=core_timeout,
        ) as response:

            data = json.loads(
                response.read().decode("utf-8")
            )

    except urllib.error.HTTPError as e:

        # 401/403 normalmente indicam problema de autenticação.
        if e.code in (401, 403):
            _progress(
                "source_miss",
                source="core",
                reason=f"authentication_failed_http_{e.code}",
            )
            return []

        if errors is not None and _is_transport_exc(e):
            errors.append({
                "source": "core",
                "detail": str(e),
            })

        return []

    except Exception as e:

        if errors is not None and _is_transport_exc(e):
            errors.append({
                "source": "core",
                "detail": str(e),
            })

        return []

    resultados = data.get("results") or []

    pdf_urls: list[str] = []

    def add_url(url: str | None) -> None:

        if not url:
            return

        url = str(url).strip()

        if not url:
            return

        if url not in pdf_urls:
            pdf_urls.append(url)

    target_doi = normalize_doi(doi)

    for artigo in resultados:
        # Extract all DOIs from the record
        record_dois_list = record_dois(artigo)

        # Check if any of the record's DOIs match our target DOI
        doi_match = False
        for record_doi in record_dois_list:
            if normalize_doi(record_doi) == target_doi:
                doi_match = True
                break

        if not doi_match:
            continue

        # Only if we have a DOI match, extract URLs
        add_url(
            artigo.get("downloadUrl")
        )

        for fulltext_url in (
            artigo.get("sourceFulltextUrls") or []
        ):
            add_url(fulltext_url)

    return pdf_urls

def try_arxiv(arxiv_id: str) -> str:
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def try_arxiv_metadata(arxiv_id: str, *, timeout: int) -> dict:
    """Fetch title / year / first-author from arXiv's Atom API.

    Used when neither Unpaywall nor S2 returned metadata — typical for
    arXiv-only papers reached via the synthesized 10.48550/arXiv.<id> DOI
    form, which S2's by-DOI endpoint does not index. Without this, the
    deterministic filename falls back to encoding the DOI literal.

    Best-effort: returns an empty dict on any failure (offline, malformed
    response, paper not found).
    """
    bare = re.sub(r"v\d+$", "", arxiv_id)
    try:
        body = _get(
            f"http://export.arxiv.org/api/query?id_list={bare}",
            accept="application/atom+xml",
            timeout=timeout,
        )
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(body)
        entry = root.find("atom:entry", ns)
        if entry is None:
            return {}
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        published = entry.findtext("atom:published", default="", namespaces=ns) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        author = entry.findtext("atom:author/atom:name", default=None, namespaces=ns)
        return {"title": title or None, "year": year, "author": author}
    except Exception:
        return {}


def try_pmc(pmcid: str) -> str:
    pmcid = pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"
    return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"


def try_europe_pmc(pmcid: str) -> str:
    """Europe PMC's render endpoint — mirror of PMC without PoW challenge.

    For articles flagged as hasPDF=Y in Europe PMC's catalog, this returns
    the paper's PDF directly. Useful as a fallback when NCBI PMC returns
    its cloudpmc-viewer JavaScript proof-of-work page.
    """
    pmcid = pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"
    return f"https://europepmc.org/articles/{pmcid}?pdf=render"

def try_europe_pmc_by_doi(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> str | None:
    """Resolve um DOI diretamente para PMCID usando a API do Europe PMC."""

    params = urllib.parse.urlencode({
        "query": f'DOI:"{doi}"',
        "format": "json",
        "pageSize": "1",
    })

    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        + params
    )

    try:
        data = _get_json(url, timeout=timeout)

    except Exception as e:
        if errors is not None and _is_transport_exc(e):
            errors.append({
                "source": "europe_pmc",
                "detail": str(e),
            })

        return None

    resultados = (
        data
        .get("resultList", {})
        .get("result", [])
    )

    if not resultados:
        return None

    artigo = resultados[0]

    pmcid = artigo.get("pmcid")

    if not pmcid:
        return None

    pmcid = str(pmcid).strip().upper()

    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"

    return pmcid


def try_pmcid_from_pmid(pmid: str, *, timeout: int) -> str | None:
    """Convert PMID to PMCID using NCBI's E-utilities.

    Uses the NCBI E-utilities efetch tool to get the PMC ID associated with a PMID.
    Returns the PMCID in format 'PMCXXXXXXX' or None if not found.
    """
    # Normalize PMID - remove any non-numeric prefixes/suffixes
    pmid = ''.join(filter(str.isdigit, pmid))
    if not pmid:
        return None

    # Use NCBI E-utilities to get PMCID from PMID
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"

    try:
        xml_data = _get(url, accept="application/xml", timeout=timeout).decode("utf-8")
        root = ET.fromstring(xml_data)

        # Look for PMCID in the XML
        # PMCID is typically in <article-id pub-id-type="pmc"> elements
        for article_id in root.iter('article-id'):
            if article_id.get('pub-id-type') == 'pmc':
                pmcid = article_id.text
                if pmcid and pmcid.startswith('PMC'):
                    return pmcid.upper()
                elif pmcid:
                    return f"PMC{pmcid.upper()}"

        # Alternative: look in <pubmed-data> sections
        for pub_data in root.iter('pub-data'):
            for article_id in pub_data.iter('article-id'):
                if article_id.get('pub-id-type') == 'pmc':
                    pmcid = article_id.text
                    if pmcid and pmcid.startswith('PMC'):
                        return pmcid.upper()
                    elif pmcid:
                        return f"PMC{pmcid.upper()}"

    except Exception:
        # Silently fail - PMID to PMCID conversion is best-effort
        pass

    return None


def try_pmid(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> str | None:
    """Extract PMID from DOI using external IDs from Semantic Scholar or Crossref.

    First tries to get PMID from Semantic Scholar's externalIds, then falls back
    to Crossref if needed.
    """
    # Try Semantic Scholar first (often has good external IDs)
    try:
        url = (
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{urllib.parse.quote(doi)}"
            "?fields=externalIds"
        )
        d = _get_json(url, timeout=timeout)
        ext_ids = d.get("externalIds") or {}
        pmid = ext_ids.get("PMID")
        if pmid:
            # Normalize PMID - just digits
            pmid_digits = ''.join(filter(str.isdigit, str(pmid)))
            if pmid_digits:
                return pmid_digits
    except Exception:
        pass

    # Fallback to Crossref
    try:
        url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        d = _get_json(url, timeout=timeout)
        # Crossref might have PMID in alternative-id or similar
        alt_ids = (d.get("message") or {}).get("alternative-id") or []
        for alt_id in alt_ids:
            if isinstance(alt_id, str) and alt_id.upper().startswith("PMID"):
                # Extract digits from PMID:12345 format
                pmid_digits = ''.join(filter(str.isdigit, alt_id))
                if pmid_digits:
                    return pmid_digits
    except Exception:
        pass

    return None


_PMCID_URL_RE = re.compile(r"/pmc/articles/(PMC\d+)", re.IGNORECASE)


def _pmcid_from_url(url: str | None) -> str | None:
    """Extract a PMCID from a URL like https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/...

    S2's openAccessPdf.url often points to a PMC article without also
    populating externalIds.PubMedCentral; parsing the URL recovers the id
    so we can still build Europe PMC / PMC fallback candidates.
    """
    if not url:
        return None
    m = _PMCID_URL_RE.search(url)
    return m.group(1).upper() if m else None


def try_biorxiv(doi: str, *, timeout: int) -> str | None:
    if not doi.startswith("10.1101/"):
        return None
    for server in ("biorxiv", "medrxiv"):
        try:
            d = _get_json(f"https://api.biorxiv.org/details/{server}/{doi}", timeout=timeout)
            coll = d.get("collection") or []
            if coll:
                latest = coll[-1]
                return f"https://www.{server}.org/content/10.1101/{latest['doi'].split('/')[-1]}v{latest.get('version', 1)}.full.pdf"
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Title → DOI resolvers (Crossref + Semantic Scholar fallback)
# ---------------------------------------------------------------------------

# Minimum title length we'll send to a resolver. Anything shorter is almost
# certainly a typo or one-word query that will return noise.
_MIN_TITLE_LEN = 6

# Heuristic confidence thresholds for Crossref's relevance score. The score
# is unitless and scales with title length, so these are calibrated to be
# permissive — anything obviously sloppy still produces a low_confidence
# flag rather than silently picking the wrong paper.
TITLE_SCORE_MIN = 40.0   # absolute floor; below this the top is suspect
TITLE_GAP_MIN = 3.0      # gap from top to runner-up; below this the top is ambiguous


def try_crossref_title(title: str, *, timeout: int) -> tuple[str | None, dict, list[dict]]:
    """Resolve a paper title to a DOI via Crossref.

    Crossref's relevance score is unitless and scales with title length, so we
    don't gate on an absolute threshold — we hand the top match plus the
    top 3 candidates back to the caller so an agent can sanity-check.

    Returns ``(top_doi, top_meta, candidates)``:
      - ``top_doi``: best-match DOI, or ``None`` if Crossref returned no items
      - ``top_meta``: ``{title, year, author, journal, score}`` for the top hit
      - ``candidates``: list of up to 3 candidate dicts in score order
    """
    q = title.strip()
    if len(q) < _MIN_TITLE_LEN:
        return None, {}, []
    # query.title outranks query.bibliographic for this use case: the input is
    # explicitly a paper title, and bibliographic mode also weights authors/year
    # equally — empirically that demoted the canonical AlphaFold paper below
    # secondary "Faculty Opinions recommendation of ..." entries that share
    # all the user's title tokens.
    params = {
        "query.title": q,
        "rows": "3",
        "select": "DOI,title,score,author,issued,container-title",
    }
    # Crossref's polite pool gives priority to requests that identify the
    # caller via mailto. We already pass UA but also include mailto when the
    # operator set UNPAYWALL_EMAIL, since the same address is theirs.
    if EMAIL:
        params["mailto"] = EMAIL
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    try:
        data = _get_json(url, timeout=timeout)
    except Exception as e:
        _progress("title_resolve_failed", reason=str(e))
        return None, {}, []

    items = ((data.get("message") or {}).get("items")) or []
    if not items:
        return None, {}, []

    candidates: list[dict] = []
    for it in items[:3]:
        title_list = it.get("title") or []
        author_list = it.get("author") or []
        first_author = ""
        if author_list:
            a0 = author_list[0]
            first_author = a0.get("family") or a0.get("name") or ""
        issued = ((it.get("issued") or {}).get("date-parts") or [[None]])[0]
        year = issued[0] if issued and issued[0] else None
        cont = it.get("container-title") or []
        candidates.append({
            "doi": it.get("DOI"),
            "title": title_list[0] if title_list else None,
            "year": year,
            "author": first_author or None,
            "journal": cont[0] if cont else None,
            "score": it.get("score"),
        })

    top = candidates[0]
    top_meta = {k: v for k, v in top.items() if k != "doi"}
    return top.get("doi"), top_meta, candidates


def try_semantic_scholar_match(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Resolve a title to a DOI via Semantic Scholar's ``/paper/search/match``.

    S2's match endpoint returns at most one paper — its closest title in the
    corpus. Better than the relevance endpoint for our use case because we
    want exactness, not breadth. Critically, S2's corpus includes arXiv-only
    papers that never get a Crossref DOI; for those we synthesize the
    canonical arXiv DOI ``10.48550/arXiv.{id}`` so the downstream fetch
    chain treats the result uniformly.

    Returns ``(doi, meta)``. ``meta`` carries ``title``, ``year``, ``author``,
    ``journal``, ``paper_id``, and ``external_ids`` for caller transparency.
    """
    q = title.strip()
    if len(q) < _MIN_TITLE_LEN:
        return None, {}
    params = {
        "query": q,
        "fields": "title,authors,year,venue,externalIds",
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search/match?" + urllib.parse.urlencode(params)
    try:
        d = _get_json(url, timeout=timeout)
    except Exception as e:
        # 404 (no match) is the expected miss path here — the helper logs
        # the same way for any failure since the caller treats them as miss.
        _progress("title_resolver_miss", resolver="semantic_scholar", reason=str(e))
        return None, {}

    items = d.get("data") or []
    if not items:
        return None, {}
    top = items[0]
    ext = top.get("externalIds") or {}
    doi = ext.get("DOI")
    if not doi and ext.get("ArXiv"):
        # arXiv assigns DataCite DOIs as 10.48550/arXiv.<id> (since 2022;
        # for older preprints the DOI may not be registered, but the fetch
        # chain's arXiv source resolver doesn't need a registered DOI —
        # it builds the PDF URL from the arXiv id itself once S2 / Unpaywall
        # surface it via externalIds during the download phase).
        doi = f"10.48550/arXiv.{ext['ArXiv']}"
    if not doi:
        return None, {}
    authors = top.get("authors") or []
    return doi, {
        "doi": doi,
        "title": top.get("title"),
        "year": top.get("year"),
        "author": authors[0].get("name") if authors else None,
        "journal": top.get("venue") or None,
        "paper_id": top.get("paperId"),
        "external_ids": ext,
    }


# ---------------------------------------------------------------------------
# Publisher-direct fallback (institutional mode only)
# ---------------------------------------------------------------------------
# When the five OA sources all miss and the operator has opted into
# institutional mode, construct a publisher-side PDF URL by DOI prefix.
# The caller's IP / subscription cookies / EZproxy determine whether the
# publisher actually serves the PDF; unauthorized responses (401/403 or an
# HTML login page) fail the %PDF magic-byte check and the envelope surfaces
# download_not_a_pdf. SSRF + 50 MB + 1 req/s rate limit still apply.

_PUBLISHER_DIRECT_TEMPLATES: dict[str, tuple[str, str]] = {
    # DOI prefix -> (publisher label, URL template).
    # {doi} = full DOI; {suffix} = part after the prefix.
    "10.1038/": ("nature", "https://www.nature.com/articles/{suffix}.pdf"),
    "10.1126/": ("science", "https://www.science.org/doi/pdf/{doi}"),
    "10.1002/": ("wiley", "https://onlinelibrary.wiley.com/doi/pdf/{doi}"),
    "10.1007/": ("springer", "https://link.springer.com/content/pdf/{doi}.pdf"),
    "10.1021/": ("acs", "https://pubs.acs.org/doi/pdf/{doi}"),
    "10.1073/": ("pnas", "https://www.pnas.org/doi/pdf/{doi}"),
    "10.1056/": ("nejm", "https://www.nejm.org/doi/pdf/{doi}"),
    "10.1177/": ("sage", "https://journals.sagepub.com/doi/pdf/{doi}"),
    "10.1080/": ("tandf", "https://www.tandfonline.com/doi/pdf/{doi}"),
    # 10.1016/ (Elsevier / Cell Press) needs PII lookup — handled separately below.
    # 10.3390/ (MDPI) needs slug lookup — handled separately below; the
    # canonical www.mdpi.com PDF URL is gated by Akamai and 403s many
    # data-center / non-Western IPs even on OA papers, so we route via the
    # pub.mdpi-res.com CDN instead (see _mdpi_pdf_candidates).
}


# MDPI uses a short journal abbreviation in its DOI suffix (e.g. "app" for
# Applied Sciences) but a longer slug in the CDN URL (e.g. "applsci"). For
# many journals these are identical — ijms, molecules, sensors, cells,
# nutrients, cancers, foods, plants, etc. — and the fallback below covers
# them. Only journals whose slug differs from the short need to live here.
# Source: MDPI's own pub.mdpi-res.com URL convention, verified against
# representative DOIs from each listed journal.
_MDPI_SHORT_TO_SLUG: dict[str, str] = {
    "app": "applsci",
    "su": "sustainability",
    "ma": "materials",
    "en": "energies",
    "ani": "animals",
    "polym": "polymers",
    "antiox": "antioxidants",
    "math": "mathematics",
    "sym": "symmetry",
    "nano": "nanomaterials",
    "met": "metals",
    "catal": "catalysts",
    "cryst": "crystals",
    "atmos": "atmosphere",
    "info": "information",
    "md": "marinedrugs",
    "fi": "futureinternet",
    "f": "forests",
    "w": "water",
    "v": "viruses",
    "d": "diversity",
}

# DOI suffix shape for MDPI: <alpha-short><yy><iss><art>. Year and issue
# are 2 digits each; article fills the rest (1+ digits, padded to 5 in URL).
_MDPI_DOI_SUFFIX_RE = re.compile(r"^([a-z]+)(\d{2})(\d{2})(\d+)$")


def _mdpi_pdf_candidates(doi: str) -> list[str]:
    """CDN URL candidates for an MDPI DOI (10.3390/...).

    Returns 1-2 candidate URLs on pub.mdpi-res.com. Empty list if the DOI
    suffix doesn't match the expected MDPI shape (rare; older DOIs).

    Two URLs are returned when the short prefix has a known mapping AND
    differs from the short itself, so the download loop can fall back to
    the short-as-slug guess if the mapping is wrong or stale.
    """
    if not doi.startswith("10.3390/"):
        return []
    suffix = doi[len("10.3390/"):]
    m = _MDPI_DOI_SUFFIX_RE.match(suffix)
    if not m:
        return []
    short, vol, _iss, art = m.groups()
    art5 = art.zfill(5)
    slugs: list[str] = []
    mapped = _MDPI_SHORT_TO_SLUG.get(short)
    if mapped:
        slugs.append(mapped)
    if short not in slugs:
        slugs.append(short)
    return [
        f"https://pub.mdpi-res.com/{s}/{s}-{vol}-{art5}/article_deploy/{s}-{vol}-{art5}.pdf"
        for s in slugs
    ]


def _try_publisher_direct(doi: str, *, timeout: int) -> list[tuple[str, str]]:
    """Construct publisher-side direct PDF URL candidates by DOI prefix.

    Returns a list of (url, publisher_label) tuples in priority order, or
    an empty list if no template matches. Multiple candidates are returned
    when the publisher has more than one viable host (e.g. MDPI with both
    a mapped slug and a fallback slug). The actual HTTP fetch will reveal
    authorization failures via 401/403 or HTML responses.
    """
    if doi.startswith("10.1016/"):
        # Elsevier / ScienceDirect: Support official API with ELSEVIER_API_KEY + PII resolution
        els_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
        candidates: list[tuple[str, str]] = []
        if els_key:
            candidates.append((f"https://api.elsevier.com/content/article/doi/{urllib.parse.quote(doi)}?httpAccept=application/pdf", "elsevier_api"))

        try:
            data = _get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}", timeout=timeout)
        except Exception:
            data = {}
        ids = (data.get("message") or {}).get("alternative-id") or []
        pii = next(
            (i for i in ids if isinstance(i, str) and i.startswith("S") and len(i) >= 16),
            None,
        )
        if pii:
            if els_key:
                candidates.append((f"https://api.elsevier.com/content/article/pii/{pii}?httpAccept=application/pdf", "elsevier_api"))
            candidates.append((f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft", "elsevier"))

        if candidates:
            return candidates

    if doi.startswith("10.3390/"):
        return [(url, "mdpi") for url in _mdpi_pdf_candidates(doi)]

    for prefix, (label, tmpl) in _PUBLISHER_DIRECT_TEMPLATES.items():
        if doi.startswith(prefix):
            suffix = doi[len(prefix):]
            return [(tmpl.format(doi=doi, suffix=suffix), label)]

    return []


# ---------------------------------------------------------------------------
# Sci-Hub resolver
# ---------------------------------------------------------------------------

_SCIHUB_DISCOVERY_RE = re.compile(
    r'href=["\']https?://(?:www\.)?(sci-hub\.[a-z0-9.-]+)/?["\']',
    re.IGNORECASE,
)
# Phrases that signal the paper is genuinely not in Sci-Hub's corpus
# (vs. CAPTCHA / mirror outage). Lets us short-circuit instead of cycling
# through every mirror.
_SCIHUB_NOT_IN_CORPUS_PATTERNS = (
    re.compile(r"please\s+try\s+to\s+search\s+again\s+using\s+doi", re.IGNORECASE),
    re.compile(r"статья\s+не\s+найдена\s+в\s+базе", re.IGNORECASE),
    re.compile(r"article\s+not\s+found\s+in\s+(?:the\s+)?database", re.IGNORECASE),
)

# Lazily populated; reset only on process restart.
_scihub_discovered_cache: list[str] | None = None


def _is_scihub_enabled() -> bool:
    """True unless operator opted out via PAPER_FETCH_NO_SCIHUB=1."""
    return not os.environ.get("PAPER_FETCH_NO_SCIHUB")


def _scihub_mirrors() -> list[str]:
    """Mirror list for Sci-Hub, in priority order.

    PAPER_FETCH_SCIHUB_MIRRORS (comma-sep) overrides the built-in defaults.
    Discovery (re-scanning SCIHUB_DISCOVERY_URL) is invoked separately by
    `try_scihub` after the configured list is exhausted.
    """
    override = os.environ.get("PAPER_FETCH_SCIHUB_MIRRORS", "").strip()
    if override:
        return _parse_mirror_overrides(override)
    return list(SCIHUB_DEFAULT_MIRRORS)


def _parse_mirror_overrides(raw: str) -> list[str]:
    """Parse comma-separated mirror overrides into bare hostnames.

    Accepts forms like ``sci-hub.ru``, ``https://sci-hub.ru``, or
    ``sci-hub.ru/path/`` and returns just the hostname. Empty / unsafe
    entries (non-http(s) schemes, IP literals in private space, blocked
    hosts) are dropped — without this, a typo in the env var could route
    traffic at an attacker-controlled host.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw_entry in raw.split(","):
        entry = raw_entry.strip().rstrip("/")
        if not entry:
            continue
        # Add a scheme so urlparse splits hostname correctly for bare
        # ``sci-hub.ru`` inputs (urlparse treats them as path-only).
        candidate = entry if "://" in entry else "https://" + entry
        try:
            parsed = urllib.parse.urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if not host or host in seen:
            continue
        # Reuse the universal SSRF guard so an override of e.g.
        # ``localhost`` or a private IP literal is dropped.
        ok, _ = _is_safe_url(f"https://{host}/")
        if not ok:
            continue
        seen.add(host)
        out.append(host)
    return out


def _scihub_is_not_in_corpus(html: str) -> bool:
    """True if the HTML matches a known 'paper not in database' message.

    Lets the resolver skip the remaining mirrors when continuing is pointless
    (every mirror serves the same shared corpus). Distinct from CAPTCHA, which
    looks like an empty or challenge page — for that we still rotate mirrors.
    """
    return any(p.search(html) for p in _SCIHUB_NOT_IN_CORPUS_PATTERNS)


class _ScihubEmbedFinder(html.parser.HTMLParser):
    """Collect <iframe>/<embed> tags from Sci-Hub paper pages.

    Order-independent attribute capture — unlike the prior regex, an
    ``<iframe src="..." id="pdf">`` is treated identically to
    ``<iframe id="pdf" src="...">``. Records all candidates so the caller
    can prefer ``id="pdf"`` and fall back to any ``.pdf`` src.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # list of (id_attr_lower, src_attr) tuples, in document order.
        self.candidates: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._maybe_record(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        # Self-closing variant (<embed ... />) — still want to capture.
        self._maybe_record(tag, attrs)

    def _maybe_record(self, tag: str, attrs: list) -> None:
        if tag.lower() not in ("iframe", "embed"):
            return
        attr_map = {(k or "").lower(): (v or "") for k, v in attrs}
        src = attr_map.get("src", "").strip()
        if not src:
            return
        self.candidates.append((attr_map.get("id", "").lower(), src))


def _scihub_normalize_pdf_url(url: str, mirror_host: str | None) -> str | None:
    """Normalize a candidate src into an absolute https URL.

    Returns None if the URL is path-relative without a mirror context to
    anchor it against — the caller will fall back to another mirror.
    """
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        if not mirror_host:
            return None
        return f"https://{mirror_host}{url}"
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def _scihub_extract_iframe(html_text: str, mirror_host: str | None = None) -> str | None:
    """Extract the embedded PDF URL from a Sci-Hub paper page.

    Sci-Hub returns an HTML page with an <iframe src="...pdf"> (or sometimes
    an <embed src="...pdf">) pointing at the actual PDF on a CDN. Returns
    the absolute https:// URL, or None if no embed found (CAPTCHA, missing
    paper, or layout change). When `mirror_host` is provided, path-relative
    URLs (e.g. `/downloads/abc.pdf`) are resolved against it.
    """
    finder = _ScihubEmbedFinder()
    try:
        finder.feed(html_text)
    except Exception:
        # Malformed markup — bail to None so the caller rotates mirrors.
        return None

    # Prefer tags carrying id="pdf" regardless of attribute order in the source.
    # Within each tier, prefer entries whose src contains ".pdf".
    pdf_id = [(i, s) for i, s in finder.candidates if i == "pdf"]
    other = [(i, s) for i, s in finder.candidates if i != "pdf"]

    for tier in (pdf_id, other):
        # First pass within tier — strict ".pdf" hint.
        for _, src in tier:
            if ".pdf" not in src.lower():
                continue
            normalized = _scihub_normalize_pdf_url(src.strip(), mirror_host)
            if normalized:
                return normalized
        # Second pass within the id="pdf" tier — Sci-Hub sometimes serves
        # an obfuscated CDN URL without the ``.pdf`` extension. Trust the
        # explicit id anchor over filename hints.
        if tier is pdf_id:
            for _, src in tier:
                normalized = _scihub_normalize_pdf_url(src.strip(), mirror_host)
                if normalized:
                    return normalized
    return None


def _scihub_discover_mirrors(*, timeout: int) -> list[str]:
    """Scrape SCIHUB_DISCOVERY_URL for current mirror list. Cached per process."""
    global _scihub_discovered_cache
    if _scihub_discovered_cache is not None:
        return _scihub_discovered_cache
    try:
        html = _get(SCIHUB_DISCOVERY_URL, accept="text/html", timeout=timeout).decode("utf-8", "replace")
    except Exception as e:
        _progress("scihub_discover_failed", reason=str(e))
        _scihub_discovered_cache = []
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _SCIHUB_DISCOVERY_RE.finditer(html):
        host = m.group(1).lower()
        if host in seen:
            continue
        seen.add(host)
        found.append(host)
    _scihub_discovered_cache = found
    if found:
        _progress("scihub_discover_ok", mirrors=found)
    return found


def try_scihub(doi: str, *, timeout: int) -> tuple[str, str] | None:
    """Resolve a DOI to a PDF URL via concurrent Sci-Hub mirror probing."""
    import concurrent.futures
    mirrors = _scihub_mirrors()
    req_timeout = min(timeout, 6)

    def _try_one(host: str) -> tuple[str | None, str]:
        url = f"https://{host}/{doi}"
        if not _is_allowed_host(url):
            return None, "error"
        try:
            html = _get(
                url,
                accept="text/html,application/xhtml+xml",
                timeout=req_timeout,
                user_agent=SCIHUB_UA,
            ).decode("utf-8", "replace")
        except Exception:
            return None, "error"
        pdf = _scihub_extract_iframe(html, mirror_host=host)
        if pdf:
            return pdf, "pdf"
        if _scihub_is_not_in_corpus(html):
            return None, "not_in_corpus"
        return None, "no_pdf"

    # Concurrent probing of top mirrors
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(mirrors), 4)) as executor:
        future_to_host = {executor.submit(_try_one, host): host for host in mirrors[:6]}
        for future in concurrent.futures.as_completed(future_to_host):
            host = future_to_host[future]
            try:
                pdf, status = future.result()
                if pdf:
                    return pdf, host
                if status == "not_in_corpus":
                    _progress("source_miss", source="scihub", reason="not_in_corpus", mirror=host)
                    return None
            except Exception:
                continue

    return None


def try_annas_archive(doi: str, *, timeout: int, errors: list | None = None) -> str | None:
    """Try to fetch a paper from Annas Archive as a fallback source."""
    # Annas Archive API endpoint for DOI resolution
    url = f"https://annas-archive.org/search?str={urllib.parse.quote(doi)}&ext=pdf"
    try:
        # First, search for the DOI on Annas Archive
        html = _get(url, accept="text/html", timeout=timeout).decode("utf-8", "replace")

        # Look for PDF download links in the search results
        # Annas Archive typically shows results with links like /download/xxx.pdf
        import re
        pdf_links = re.findall(r'href=["\'](/download/[^"\']*\.pdf)["\']', html)

        if pdf_links:
            # Take the first PDF link found
            pdf_url = "https://annas-archive.org" + pdf_links[0]
            return pdf_url

        # If no direct PDF links, try to find the book/detail page and extract download link from there
        detail_links = re.findall(r'href=["\'](/detail/[^"\']+)["\']', html)
        if detail_links:
            detail_url = "https://annas-archive.org" + detail_links[0]
            try:
                detail_html = _get(detail_url, accept="text/html", timeout=timeout).decode("utf-8", "replace")
                # Look for download links in the detail page
                pdf_links = re.findall(r'href=["\'](/download/[^"\']*\.pdf)["\']', detail_html)
                if pdf_links:
                    pdf_url = "https://annas-archive.org" + pdf_links[0]
                    return pdf_url
            except Exception:
                pass

    except Exception as e:
        if errors is not None and _is_transport_exc(e):
            errors.append({"source": "annas_archive", "detail": str(e)})
        return None

    return None


def try_doi_resolver(doi: str, *, timeout: int, errors: list | None = None) -> tuple[str | None, dict]:
    """Try to resolve a DOI using doi.org resolver directly.

    This follows redirects to find the final landing page, then attempts to
    extract a PDF link from common patterns on publisher sites.
    """
    # Normalize DOI
    doi_norm = normalize_doi(doi)
    if not doi_norm:
        return None, {}

    # Try doi.org resolver
    url = f"https://doi.org/{doi_norm}"
    try:
        # Follow redirects to get the final URL
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            # Read a limited amount of HTML to look for PDF links
            html = resp.read(1024 * 1024).decode("utf-8", "ignore")  # 1MB max
    except Exception as e:
        if errors is not None and _is_transport_exc(e):
            errors.append({"source": "doi_resolver", "detail": str(e)})
        return None, {}

    # Check if we got a challenge/interstitial page (e.g., Cloudflare) that prevents
    # us from seeing the real content without JavaScript/cookies.
    challenge_indicators = [
        "Just a moment",
        "Enable JavaScript and cookies",
        "Checking your browser before accessing",
        "attention required!|please stand by",
        "browser check",
        "DDoS protection by Cloudflare",
        "Access denied",
        "Please wait while we are checking your browser",
        "jschallenge",
        "cf-chl-bypass",
        "cf-error-details",
        "cf-browser-verification",
    ]
    html_lower = html.lower()
    for indicator in challenge_indicators:
        if indicator.lower() in html_lower:
            # Likely a challenge page; skip PDF extraction as we won't get the real content.
            return None, {}

    # Common patterns for PDF links on publisher sites
    import re
    pdf_patterns = [
        r'href=["\']([^"\']*\.pdf)["\']',
        r'src=["\']([^"\']*\.pdf)["\']',
        r'(?:pdf|PDF)["\']?\s*[^\'">]*?href=["\']([^"\']*\.pdf)["\']',
        r'data-url=["\']([^"\']*\.pdf)["\']',
    ]

    pdf_url = None
    for pattern in pdf_patterns:
        matches = re.findall(pattern, html, re.IGNORECASE)
        if matches:
            # Take the first match that looks like a full URL or make it absolute
            for match in matches:
                if match.startswith('http'):
                    pdf_url = match
                    break
                elif match.startswith('//'):
                    pdf_url = 'https:' + match
                    break
                elif match.startswith('/'):
                    # Relative to domain
                    from urllib.parse import urlparse
                    parsed = urlparse(final_url)
                    pdf_url = f"{parsed.scheme}://{parsed.netloc}{match}"
                    break
                else:
                    # Relative path - try to append to final URL path
                    from urllib.parse import urljoin
                    pdf_url = urljoin(final_url, match)
                    break
            if pdf_url:
                break

    # Extract some metadata from the HTML title for filename generation
    meta = {}
    title_match = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if title_match:
        meta["title"] = title_match.group(1).strip()

    return pdf_url, meta


# ---------------------------------------------------------------------------
# Libgen resolver
# ---------------------------------------------------------------------------


def _is_libgen_enabled() -> bool:
    """True unless operator opted out via PAPER_FETCH_NO_LIBGEN=1."""
    return not os.environ.get("PAPER_FETCH_NO_LIBGEN")


def _libgen_mirrors() -> list[str]:
    """Mirror list for Libgen in priority order."""
    override = os.environ.get("PAPER_FETCH_LIBGEN_MIRRORS", "").strip()
    if override:
        return [m.strip().rstrip("/") for m in override.split(",") if m.strip()]
    return list(LIBGEN_DEFAULT_MIRRORS)


def try_libgen(
    doi: str,
    title: str | None = None,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve a DOI or title to direct PDF download URLs and metadata via Libgen."""
    if not _is_libgen_enabled():
        return [], {}

    pdf_urls: list[str] = []
    meta: dict = {}

    headers = {
        "User-Agent": LIBGEN_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    req_timeout = min(timeout, 5)

    # 1. Fast direct HTTP mirror resolution (instant and resilient)
    queries = []
    if doi:
        queries.append(f"doi:{doi}")
    if title and len(title) >= 6:
        queries.append(title)

    mirrors = _libgen_mirrors()
    for mirror in mirrors:
        if pdf_urls:
            break
        for q in queries:
            if pdf_urls:
                break
            try:
                search_url = f"{mirror}/index.php?req={urllib.parse.quote(q)}&columns%5B%5D=t&res=25"
                req = urllib.request.Request(search_url, headers=headers)
                with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                    html_text = resp.read().decode("utf-8", "replace")

                if "edition.php?id=" not in html_text and "/ads.php?md5=" not in html_text:
                    continue

                md5_list: list[str] = []
                for m in re.findall(r'/ads\.php\?md5=([a-fA-F0-9]{32})', html_text):
                    if m not in md5_list:
                        md5_list.append(m)

                edition_matches = re.findall(r'href=["\'](edition\.php\?id=\d+)["\']', html_text)
                for ed in edition_matches[:2]:
                    ed_url = f"{mirror}/{ed}"
                    try:
                        req_ed = urllib.request.Request(ed_url, headers=headers)
                        with urllib.request.urlopen(req_ed, timeout=req_timeout) as resp_ed:
                            ed_html = resp_ed.read().decode("utf-8", "replace")
                        for m in re.findall(r'/ads\.php\?md5=([a-fA-F0-9]{32})', ed_html):
                            if m not in md5_list:
                                md5_list.append(m)
                    except Exception:
                        continue

                for md5 in md5_list[:2]:
                    ads_url = f"{mirror}/ads.php?md5={md5}"
                    try:
                        req_ads = urllib.request.Request(ads_url, headers=headers)
                        with urllib.request.urlopen(req_ads, timeout=req_timeout) as resp_ads:
                            ads_html = resp_ads.read().decode("utf-8", "replace")

                        get_match = re.search(r'href=["\'](get\.php\?md5=[a-fA-F0-9]+&key=[a-zA-Z0-9]+)["\']', ads_html)
                        if get_match:
                            dl_url = f"{mirror}/{get_match.group(1)}"
                            if dl_url not in pdf_urls:
                                pdf_urls.append(dl_url)
                    except Exception:
                        continue

            except Exception as exc:
                if errors is not None and _is_transport_exc(exc):
                    errors.append({"source": "libgen", "detail": str(exc)})
                continue

    if pdf_urls:
        return pdf_urls, meta

    # 2. Fallback: Try using libgen-api-enhanced / libgen-api if installed
    try:
        from io import StringIO
        import contextlib
        import logging
        import socket
        logging.getLogger("libgen_api_enhanced").setLevel(logging.CRITICAL)
        logging.getLogger("libgen_api").setLevel(logging.CRITICAL)
        from libgen_api_enhanced import LibgenSearch
        old_sock_t = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(min(timeout, 3))
            for mirror_code in ("li",):
                try:
                    s = LibgenSearch(mirror=mirror_code)
                    # Suppress third-party print noise
                    with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                        results = s.search_default(f"doi:{doi}")
                        if not results and title and len(title) >= 6:
                            results = s.search_title(title)
                    if results:
                        for book in results[:3]:
                            if not meta.get("title") and getattr(book, "title", None):
                                meta["title"] = book.title
                            if not meta.get("author") and getattr(book, "author", None):
                                meta["author"] = book.author
                            if not meta.get("year") and getattr(book, "year", None):
                                try:
                                    meta["year"] = int(book.year)
                                except (ValueError, TypeError):
                                    pass
                            try:
                                with contextlib.redirect_stdout(StringIO()), contextlib.redirect_stderr(StringIO()):
                                    book.resolve_direct_download_link()
                                if getattr(book, "resolved_download_link", None):
                                    link = book.resolved_download_link
                                    if link not in pdf_urls:
                                        pdf_urls.append(link)
                            except Exception:
                                pass
                            for m in getattr(book, "mirrors", []) or []:
                                if m and m not in pdf_urls and ("get.php" in m or ".pdf" in m or "ads.php" in m):
                                    pdf_urls.append(m)
                        if pdf_urls:
                            return pdf_urls, meta
                except Exception:
                    continue
        finally:
            socket.setdefaulttimeout(old_sock_t)
    except ImportError:
        pass

    return pdf_urls, meta


# ---------------------------------------------------------------------------
# Crossref direct links resolver
# ---------------------------------------------------------------------------


def try_crossref_links(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Extract direct fulltext PDF URLs and metadata from Crossref works API."""
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    if EMAIL:
        url += f"?mailto={urllib.parse.quote(EMAIL)}"
    try:
        data = _get_json(url, timeout=timeout)
    except Exception as e:
        if errors is not None and _is_transport_exc(e):
            errors.append({"source": "crossref", "detail": str(e)})
        return [], {}

    msg = data.get("message") or {}
    title_list = msg.get("title") or []
    author_list = msg.get("author") or []
    first_author = ""
    if author_list:
        a0 = author_list[0]
        first_author = a0.get("family") or a0.get("name") or ""
    issued = ((msg.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued and issued[0] else None
    cont = msg.get("container-title") or []

    meta = {
        "title": title_list[0] if title_list else None,
        "year": year,
        "author": first_author or None,
        "journal": cont[0] if cont else None,
    }

    pdf_urls: list[str] = []
    links = msg.get("link") or []
    for l in links:
        if isinstance(l, dict):
            ctype = (l.get("content-type") or "").lower()
            intended = (l.get("intended-application") or "").lower()
            url_cand = l.get("URL")
            if url_cand and ("pdf" in ctype or "text-mining" in intended or url_cand.endswith(".pdf")):
                if url_cand not in pdf_urls:
                    pdf_urls.append(url_cand)

    return pdf_urls, meta


# ---------------------------------------------------------------------------
# ACL Anthology & PaperDL resolvers
# ---------------------------------------------------------------------------


def try_acl_anthology(
    doi: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve ACL Anthology DOIs (e.g. 10.18653/v1/2020.acl-main.1) directly to PDF."""
    doi_lower = doi.lower()
    pdf_urls: list[str] = []
    meta: dict = {}
    if "10.18653/v1/" in doi_lower:
        acl_id = doi_lower.split("10.18653/v1/")[-1].strip("/")
        pdf_urls.append(f"https://aclanthology.org/{acl_id}.pdf")
    elif "10.18653/" in doi_lower:
        acl_id = doi_lower.split("10.18653/")[-1].strip("/")
        pdf_urls.append(f"https://aclanthology.org/{acl_id}.pdf")
    return pdf_urls, meta


def try_paperdl(
    query: str,
    *,
    timeout: int,
    errors: list | None = None,
) -> tuple[list[str], dict]:
    """Resolve papers via PaperDL async clients (ACL Anthology, PMLR)."""
    try:
        import asyncio
        from paperdl import PaperClient

        async def _run_search():
            async with PaperClient(
                ["acl_anthology"],
                default_init_kwargs={"verbose": False, "show_progress": False},
            ) as client:
                return await client.search(query, total_results=2)

        papers = asyncio.run(_run_search())
        pdf_urls: list[str] = []
        meta: dict = {}
        for p in papers or []:
            if getattr(p, "download_url", None) and p.download_url not in pdf_urls:
                pdf_urls.append(p.download_url)
                if not meta.get("title") and getattr(p, "title", None):
                    meta["title"] = p.title
                if not meta.get("author") and getattr(p, "authors", None):
                    if isinstance(p.authors, list) and p.authors:
                        meta["author"] = p.authors[0]
                    elif isinstance(p.authors, str):
                        meta["author"] = p.authors.split(",")[0]
        return pdf_urls, meta
    except Exception as e:
        if errors is not None and _is_transport_exc(e):
            errors.append({"source": "paperdl", "detail": str(e)})
        return [], {}


# ---------------------------------------------------------------------------
# Core fetch logic
# ---------------------------------------------------------------------------


def _download_failure(
    doi: str,
    meta: dict,
    sources_tried: list[str],
    errors: list[dict],
    *,
    candidates: list[tuple[str, str]] | None = None,
) -> dict:
    """Build a per-item download failure result. `errors` must be non-empty."""
    last = errors[-1]
    retryable = last["reason"] in ("network_error", "size_exceeded", "io_error")
    code = f"download_{last['reason']}"
    err_obj = {
        "code": code,
        "message": (
            f"All {len(errors)} candidate(s) failed; last error from {last['source']}: {last['reason']}"
            if len(errors) > 1
            else f"Download failed from {last['source']}: {last['reason']}"
        ),
        "retryable": retryable,
    }
    if retryable and code in RETRY_AFTER_HOURS:
        err_obj["retry_after_hours"] = RETRY_AFTER_HOURS[code]
    out = {
        "doi": doi,
        "success": False,
        "source": last["source"],
        "pdf_url": last["url"],
        "file": None,
        "meta": meta or {},
        "sources_tried": sources_tried,
        "download_attempts": errors,
        "error": err_obj,
    }
    if candidates:
        out["candidates"] = [{"source": s, "url": u} for s, u in candidates]
    return out


def fetch(
    doi: str,
    out_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    timeout: int,
    sources: list[str] | None = None,
) -> dict:
    """Resolve and optionally download a single DOI.

    Returns a structured per-item result (not an envelope). Guaranteed keys:
      doi, success, source, pdf_url, file, meta, sources_tried, error?
    """
    doi = doi.strip()
    for _prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi.org/", "dx.doi.org/", "doi:"):
        if doi.startswith(_prefix):
            doi = doi[len(_prefix):]
            break
    if not _DOI_RE.match(doi):
        return {
            "doi": doi,
            "success": False,
            "source": "unknown",
            "pdf_url": None,
            "file": None,
            "meta": {},
            "sources_tried": [],
            "error": {
                "code": "validation_error",
                "message": f"Not a valid DOI: {doi!r} (expected pattern {DOI_PATTERN})",
                "retryable": False,
            },
        }

    # Pre-check: if a matching PDF already exists and overwrite is False, skip immediately
    if not overwrite and out_dir.exists():
        doi_slug = _slug(doi, n=20)
        for existing in out_dir.glob("*.pdf"):
            if doi_slug.lower() in existing.name.lower():
                _progress("download_skip", doi=doi, file=str(existing))
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

    _progress("start", doi=doi)

    # Source filtering for multi-session / worker isolation
    sources_allowed = set(s.strip().lower() for s in sources) if sources else None
    env_sources = os.environ.get("PAPER_FETCH_SOURCES")
    if not sources_allowed and env_sources:
        sources_allowed = set(s.strip().lower() for s in env_sources.split(",") if s.strip())

    def _can_try(src_name: str) -> bool:
        if sources_allowed is None:
            return True
        return src_name.lower() in sources_allowed

    sources_tried: list[str] = []
    meta: dict = {}
    download_errors: list[dict] = []
    resolver_errors: list[dict] = []
    got_useful_metadata: bool = False
    source_details: dict[str, dict] = {}
    attempted_urls: set[str] = set()
    candidates: list[tuple[str, str]] = []

    FATAL_DL_ERRORS = ("io_error",)

    def _merge_meta(extra: dict) -> list[str]:
        added: list[str] = []
        for k, v in (extra or {}).items():
            if v and not meta.get(k):
                meta[k] = v
                added.append(k)
        return added

    # --- Semantic Scholar is queried lazily (cached) ---
    _s2_cache: dict | None = None

    def _get_s2() -> tuple[str | None, dict, dict]:
        nonlocal _s2_cache
        if _s2_cache is not None:
            return _s2_cache["pdf"], _s2_cache["meta"], _s2_cache["ext"]
        if not _can_try("semantic_scholar"):
            return None, {}, {}
        if "semantic_scholar" not in sources_tried:
            sources_tried.append("semantic_scholar")
        _progress("source_try", doi=doi, source="semantic_scholar")
        pdf, s2_meta, ext = try_semantic_scholar(doi, timeout=timeout, errors=resolver_errors)
        _s2_cache = {"pdf": pdf, "meta": s2_meta, "ext": ext}
        return pdf, s2_meta, ext

    def _success(src: str, url: str, extra: dict | None = None) -> dict:
        fname = _filename(meta or {"title": doi})
        dest = out_dir / fname
        out = {
            "doi": doi,
            "success": True,
            "source": src,
            "pdf_url": url,
            "file": str(dest),
            "meta": meta or {},
            "sources_tried": sources_tried,
        }
        if src in source_details:
            out["source_detail"] = source_details[src]
        if url in _CLOAK_DOWNLOADS:
            out["via"] = "cloak"
        if candidates:
            out["candidates"] = [{"source": s, "url": u} for s, u in candidates]
        if extra:
            out.update(extra)
        return out

    def _try_candidate(cand_src: str, cand_url: str, cand_detail: dict | None = None) -> tuple[dict | None, bool]:
        """Try downloading a candidate URL immediately. Returns (result_dict, is_fatal)."""
        if not cand_url or cand_url in attempted_urls:
            return None, False
        attempted_urls.add(cand_url)
        candidates.append((cand_src, cand_url))
        if cand_detail:
            source_details[cand_src] = cand_detail

        fname = _filename(meta or {"title": doi})
        dest = out_dir / fname

        if dry_run:
            _progress("dry_run", doi=doi, source=cand_src, pdf_url=cand_url, file=str(dest))
            return _success(cand_src, cand_url, {"dry_run": True}), False

        if dest.exists() and not overwrite:
            _progress("download_skip", doi=doi, file=str(dest))
            return _success(cand_src, cand_url, {"skipped": True, "skip_reason": "file_exists"}), False

        dl_err = _download(cand_url, dest, timeout=timeout)
        if dl_err is None:
            _progress("download_ok", doi=doi, file=str(dest), source=cand_src)
            return _success(cand_src, cand_url), False

        download_errors.append({"source": cand_src, "url": cand_url, "reason": dl_err})
        if dl_err in FATAL_DL_ERRORS:
            return _download_failure(doi, meta, sources_tried, download_errors, candidates=candidates), True
        return None, False

    # -----------------------------------------------------------------------
    # 1. Unpaywall (fast OA lookup)
    # -----------------------------------------------------------------------
    up_url: str | None = None
    if EMAIL and _can_try("unpaywall"):
        _progress("source_try", doi=doi, source="unpaywall")
        sources_tried.append("unpaywall")
        up_url, up_meta = try_unpaywall(doi, timeout=timeout, errors=resolver_errors)
        if _merge_meta(up_meta):
            got_useful_metadata = True
        if up_url:
            _progress("source_hit", doi=doi, source="unpaywall", pdf_url=up_url)
            # Enrich metadata from S2 if author/title missing
            if not meta.get("author") or not meta.get("title"):
                _, s2_meta, _ = _get_s2()
                added = _merge_meta(s2_meta)
                if added:
                    got_useful_metadata = True
                    _progress("source_enrich", doi=doi, source="semantic_scholar", fields=added)
            res, fatal = _try_candidate("unpaywall", up_url)
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="unpaywall")
    elif not EMAIL and _can_try("unpaywall"):
        _progress("source_skip", doi=doi, source="unpaywall", reason="UNPAYWALL_EMAIL not set")

    # -----------------------------------------------------------------------
    # 2. Semantic Scholar (OA PDF + external IDs)
    # -----------------------------------------------------------------------
    s2_pdf, s2_meta, ext = _get_s2()
    if _merge_meta(s2_meta):
        got_useful_metadata = True
    if s2_pdf and _can_try("semantic_scholar"):
        _progress("source_hit", doi=doi, source="semantic_scholar", pdf_url=s2_pdf)
        res, fatal = _try_candidate("semantic_scholar", s2_pdf)
        if res is not None:
            return res
        if fatal:
            return res
    elif not up_url and _can_try("semantic_scholar"):
        _progress("source_miss", doi=doi, source="semantic_scholar")

    # -----------------------------------------------------------------------
    # 3. OpenAlex
    # -----------------------------------------------------------------------
    if _can_try("openalex"):
        if "openalex" not in sources_tried:
            sources_tried.append("openalex")
        _progress("source_try", doi=doi, source="openalex")
        openalex_urls, openalex_meta = try_openalex(doi, timeout=timeout, errors=resolver_errors)
        added = _merge_meta(openalex_meta)
        if added:
            got_useful_metadata = True
            _progress("source_enrich", doi=doi, source="openalex", fields=added)
        if openalex_urls:
            for oa_url in openalex_urls:
                _progress("source_hit", doi=doi, source="openalex", pdf_url=oa_url)
                res, fatal = _try_candidate("openalex", oa_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="openalex")

    # -----------------------------------------------------------------------
    # 4. ArXiv
    # -----------------------------------------------------------------------
    if not ext.get("ArXiv") and doi.lower().startswith("10.48550/arxiv."):
        ext["ArXiv"] = doi[len("10.48550/arxiv."):]
        if not meta.get("title"):
            ax_meta = try_arxiv_metadata(ext["ArXiv"], timeout=timeout)
            if ax_meta:
                added = _merge_meta(ax_meta)
                if added:
                    got_useful_metadata = True
                    _progress("source_enrich", doi=doi, source="arxiv", fields=added)
            else:
                _progress("source_enrich_failed", doi=doi, source="arxiv")

    if ext.get("ArXiv") and _can_try("arxiv"):
        if "arxiv" not in sources_tried:
            sources_tried.append("arxiv")
        arxiv_url = try_arxiv(ext["ArXiv"])
        _progress("source_hit", doi=doi, source="arxiv", pdf_url=arxiv_url)
        res, fatal = _try_candidate("arxiv", arxiv_url)
        if res is not None:
            return res
        if fatal:
            return res

    # -----------------------------------------------------------------------
    # 5. Europe PMC / PubMed Central / PubMed
    # -----------------------------------------------------------------------
    if not ext.get("PubMedCentral"):
        for url_src in (up_url, s2_pdf):
            pmcid_from_url = _pmcid_from_url(url_src)
            if pmcid_from_url:
                ext["PubMedCentral"] = pmcid_from_url
                break

    if not ext.get("PubMedCentral") and _can_try("europe_pmc"):
        _progress("source_try", doi=doi, source="europe_pmc")
        if "europe_pmc" not in sources_tried:
            sources_tried.append("europe_pmc")
        epmc_pmcid = try_europe_pmc_by_doi(doi, timeout=timeout, errors=resolver_errors)
        if epmc_pmcid:
            ext["PubMedCentral"] = epmc_pmcid
        else:
            _progress("source_miss", doi=doi, source="europe_pmc")

    if ext.get("PubMedCentral"):
        if _can_try("europe_pmc"):
            if "europe_pmc" not in sources_tried:
                sources_tried.append("europe_pmc")
            epmc_url = try_europe_pmc(ext["PubMedCentral"])
            _progress("source_hit", doi=doi, source="europe_pmc", pdf_url=epmc_url)
            res, fatal = _try_candidate("europe_pmc", epmc_url)
            if res is not None:
                return res
            if fatal:
                return res

        if _can_try("pmc"):
            if "pmc" not in sources_tried:
                sources_tried.append("pmc")
            pmc_url = try_pmc(ext["PubMedCentral"])
            _progress("source_hit", doi=doi, source="pmc", pdf_url=pmc_url)
            res, fatal = _try_candidate("pmc", pmc_url)
            if res is not None:
                return res
            if fatal:
                return res

    if not ext.get("PubMedCentral") and _can_try("pubmed"):
        pmid = ext.get("PMID") or try_pmid(doi, timeout=timeout, errors=resolver_errors)
        if pmid:
            if "pubmed" not in sources_tried:
                sources_tried.append("pubmed")
            _progress("source_try", doi=doi, source="pubmed", pmid=pmid)
            pmcid = try_pmcid_from_pmid(pmid, timeout=timeout)
            if pmcid:
                ext["PubMedCentral"] = pmcid
                _progress("source_enrich", doi=doi, source="pubmed", fields=["PubMedCentral"])
                epmc_url = try_europe_pmc(pmcid)
                if epmc_url:
                    _progress("source_hit", doi=doi, source="pubmed", pdf_url=epmc_url)
                    res, fatal = _try_candidate("pubmed", epmc_url)
                    if res is not None:
                        return res
                    if fatal:
                        return res
                pmc_url = try_pmc(pmcid)
                if pmc_url:
                    _progress("source_hit", doi=doi, source="pubmed", pdf_url=pmc_url)
                    res, fatal = _try_candidate("pubmed", pmc_url)
                    if res is not None:
                        return res
                    if fatal:
                        return res
            else:
                _progress("source_miss", doi=doi, source="pubmed", reason="pmcid_not_found")

    # -----------------------------------------------------------------------
    # 6. BioRxiv / MedRxiv
    # -----------------------------------------------------------------------
    if doi.startswith("10.1101/") and _can_try("biorxiv"):
        if "biorxiv" not in sources_tried:
            sources_tried.append("biorxiv")
        _progress("source_try", doi=doi, source="biorxiv")
        bx_url = try_biorxiv(doi, timeout=timeout)
        if bx_url:
            _progress("source_hit", doi=doi, source="biorxiv", pdf_url=bx_url)
            res, fatal = _try_candidate("biorxiv", bx_url)
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="biorxiv")

    # -----------------------------------------------------------------------
    # 6b. ACL Anthology (Computational Linguistics / NLP papers)
    # -----------------------------------------------------------------------
    if "10.18653/" in doi.lower() and _can_try("acl_anthology"):
        if "acl_anthology" not in sources_tried:
            sources_tried.append("acl_anthology")
        _progress("source_try", doi=doi, source="acl_anthology")
        acl_urls, _ = try_acl_anthology(doi, timeout=timeout, errors=resolver_errors)
        if acl_urls:
            for acl_url in acl_urls:
                _progress("source_hit", doi=doi, source="acl_anthology", pdf_url=acl_url)
                res, fatal = _try_candidate("acl_anthology", acl_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="acl_anthology")

    # -----------------------------------------------------------------------
    # 7. CORE
    # -----------------------------------------------------------------------
    if _can_try("core"):
        if CORE_API_KEY and os.environ.get("PAPER_FETCH_SKIP_CORE") != "1":
            if "core" not in sources_tried:
                sources_tried.append("core")
            _progress("source_try", doi=doi, source="core")
            core_urls = try_core(doi, timeout=timeout, errors=resolver_errors)
            if core_urls:
                for core_url in core_urls:
                    _progress("source_hit", doi=doi, source="core", pdf_url=core_url)
                    res, fatal = _try_candidate("core", core_url)
                    if res is not None:
                        return res
                    if fatal:
                        return res
            else:
                _progress("source_miss", doi=doi, source="core")
        else:
            _progress("source_skip", doi=doi, source="core", reason="CORE_API_KEY not set" if not CORE_API_KEY else "PAPER_FETCH_SKIP_CORE=1")

    # -----------------------------------------------------------------------
    # 8. Libgen (Library Genesis)
    # -----------------------------------------------------------------------
    if _is_libgen_enabled() and _can_try("libgen"):
        if "libgen" not in sources_tried:
            sources_tried.append("libgen")
        _progress("source_try", doi=doi, source="libgen")
        libgen_urls, libgen_meta = try_libgen(doi, title=meta.get("title"), timeout=timeout, errors=resolver_errors)
        if _merge_meta(libgen_meta):
            got_useful_metadata = True
        if libgen_urls:
            for lg_url in libgen_urls:
                _progress("source_hit", doi=doi, source="libgen", pdf_url=lg_url)
                res, fatal = _try_candidate("libgen", lg_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="libgen")

    # -----------------------------------------------------------------------
    # 9. Crossref Direct Links
    # -----------------------------------------------------------------------
    if _can_try("crossref"):
        if "crossref" not in sources_tried:
            sources_tried.append("crossref")
        _progress("source_try", doi=doi, source="crossref")
        cr_urls, cr_meta = try_crossref_links(doi, timeout=timeout, errors=resolver_errors)
        if _merge_meta(cr_meta):
            got_useful_metadata = True
        if cr_urls:
            for cr_url in cr_urls:
                _progress("source_hit", doi=doi, source="crossref", pdf_url=cr_url)
                res, fatal = _try_candidate("crossref", cr_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="crossref")

    # -----------------------------------------------------------------------
    # 10. Publisher-direct fallback (institutional mode or Elsevier API key configured)
    # -----------------------------------------------------------------------
    els_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
    if (_is_institutional() or (els_key and doi.startswith("10.1016/"))) and _can_try("publisher_direct"):
        _progress("source_try", doi=doi, source="publisher_direct")
        pub_candidates = _try_publisher_direct(doi, timeout=timeout)
        if pub_candidates:
            if "publisher_direct" not in sources_tried:
                sources_tried.append("publisher_direct")
            for pub_url, pub_label in pub_candidates:
                _progress("source_hit", doi=doi, source="publisher_direct", pdf_url=pub_url, publisher=pub_label)
                res, fatal = _try_candidate("publisher_direct", pub_url)
                if res is not None:
                    return res
                if fatal:
                    return res
        else:
            _progress("source_miss", doi=doi, source="publisher_direct", reason="no_template_for_doi_prefix")

    # -----------------------------------------------------------------------
    # 11. DOI resolver fallback
    # -----------------------------------------------------------------------
    if _can_try("doi_resolver"):
        _progress("source_try", doi=doi, source="doi_resolver")
        if "doi_resolver" not in sources_tried:
            sources_tried.append("doi_resolver")
        resolver_url, resolver_meta = try_doi_resolver(doi, timeout=timeout, errors=resolver_errors)
        if resolver_url:
            _progress("source_hit", doi=doi, source="doi_resolver", pdf_url=resolver_url)
            if _merge_meta(resolver_meta):
                got_useful_metadata = True
            res, fatal = _try_candidate("doi_resolver", resolver_url)
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="doi_resolver")

    # -----------------------------------------------------------------------
    # 12. Sci-Hub fallback
    # -----------------------------------------------------------------------
    if _is_scihub_enabled() and _can_try("scihub") and "scihub" not in sources_tried:
        _progress("source_try", doi=doi, source="scihub")
        sources_tried.append("scihub")
        sh_hit = try_scihub(doi, timeout=timeout)
        if sh_hit:
            sh_url, sh_mirror = sh_hit
            source_details["scihub"] = {"mirror": sh_mirror}
            _progress("source_hit", doi=doi, source="scihub", pdf_url=sh_url, mirror=sh_mirror)
            res, fatal = _try_candidate("scihub", sh_url, {"mirror": sh_mirror})
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="scihub")

    # -----------------------------------------------------------------------
    # 13. Anna's Archive fallback
    # -----------------------------------------------------------------------
    if _can_try("annas_archive") and "annas_archive" not in sources_tried:
        _progress("source_try", doi=doi, source="annas_archive")
        sources_tried.append("annas_archive")
        aa_url = try_annas_archive(doi, timeout=timeout, errors=resolver_errors)
        if aa_url:
            _progress("source_hit", doi=doi, source="annas_archive", pdf_url=aa_url)
            res, fatal = _try_candidate("annas_archive", aa_url)
            if res is not None:
                return res
            if fatal:
                return res
        else:
            _progress("source_miss", doi=doi, source="annas_archive")

    # -----------------------------------------------------------------------
    # Final Exhaustion Handling
    # -----------------------------------------------------------------------
    if download_errors:
        return _download_failure(doi, meta, sources_tried, download_errors, candidates=candidates)

    if resolver_errors and not got_useful_metadata:
        _progress("resolve_error", doi=doi, sources=[e["source"] for e in resolver_errors])
        return {
            "doi": doi,
            "success": False,
            "source": None,
            "pdf_url": None,
            "file": None,
            "meta": meta or {},
            "sources_tried": sources_tried,
            "resolver_errors": resolver_errors,
            "error": {
                "code": "resolve_network_error",
                "message": "Metadata resolvers failed with transport errors; OA availability is unknown",
                "retryable": True,
                "retry_after_hours": RETRY_AFTER_HOURS["resolve_network_error"],
                "reason": "resolver API unreachable (timeout / 5xx / 403), not a confirmed absence of OA",
            },
        }

    _progress("not_found", doi=doi)
    err = {
        "code": "not_found",
        "message": "No open-access PDF found",
        "retryable": True,
        "retry_after_hours": RETRY_AFTER_HOURS["not_found"],
        "reason": "OA availability changes over time; retry after embargo lifts or preprint appears",
    }
    if not _is_institutional():
        err["suggest_institutional"] = True
        err["hint"] = (
            "If your institution has a subscription to this paper, "
            "set PAPER_FETCH_INSTITUTIONAL=1 and run from on-campus or VPN."
        )
    return {
        "doi": doi,
        "success": False,
        "source": None,
        "pdf_url": None,
        "file": None,
        "meta": meta or {},
        "sources_tried": sources_tried,
        "error": err,
    }



# ===========================================================================


# ---------------------------------------------------------------------------
# Idempotency sidecar
# ---------------------------------------------------------------------------


def _idem_path(out_dir: Path, key: str) -> Path:
    # Hash the raw key so distinct long keys that share an 80-char prefix don't
    # collide onto the same sidecar and replay each other's envelope.
    safe = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return out_dir / ".paper-fetch-idem" / f"{safe}.json"


def _idem_load(out_dir: Path, key: str) -> dict | None:
    p = _idem_path(out_dir, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _idem_store(out_dir: Path, key: str, envelope: dict) -> None:
    p = _idem_path(out_dir, key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # best-effort only


# ---------------------------------------------------------------------------
# Schema subcommand
# ---------------------------------------------------------------------------


def build_schema() -> dict:
    return {
        "command": "paper-fetch",
        "cli_version": CLI_VERSION,
        "schema_version": SCHEMA_VERSION,
        "description": "Fetch PDFs by DOI via Unpaywall, Semantic Scholar, OpenAlex, arXiv, Europe PMC, PMC, PubMed, bioRxiv/medRxiv, CORE, Libgen, and Crossref. In institutional mode (PAPER_FETCH_INSTITUTIONAL=1), also attempts a publisher-direct fetch (publisher_direct source) using the caller's own subscription IP / cookies / EZproxy. As a last resort, falls back to Sci-Hub mirrors (scihub source) and Anna's Archive (annas_archive source). On finding a valid download candidate, downloads immediately and proceeds to the next paper.",
        "subcommands": {
            "schema": "Print this schema as JSON and exit (no network).",
        },
        "params": {
            "doi": {
                "type": "string",
                "required": False,
                "description": "DOI to fetch (positional). Use '-' to read DOIs line-by-line from stdin.",
                "pattern": DOI_PATTERN,
                "example": "10.1038/s41586-020-2649-2",
            },
            "title": {
                "type": "string",
                "required": False,
                "description": "Paper title; resolved to a DOI via Crossref before download. Mutually exclusive with positional DOI / --batch. The resolved DOI, top match, and up to 3 candidates are surfaced under meta.title_resolution.",
                "example": "Highly accurate protein structure prediction with AlphaFold",
            },
            "batch": {
                "type": "path",
                "required": False,
                "description": "File with one DOI per line for bulk download. Use '-' to read from stdin.",
            },
            "out": {
                "type": "path",
                "required": False,
                "default": "pdfs",
                "description": "Output directory.",
            },
            "dry_run": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Resolve sources without downloading; preview the PDF URL and destination path.",
            },
            "format": {
                "type": "enum",
                "values": ["json", "text"],
                "required": False,
                "default": "auto (json when stdout not a TTY, text otherwise)",
                "description": "Output format. json for agents, text for humans.",
            },
            "pretty": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Pretty-print JSON output with 2-space indentation.",
            },
            "stream": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Emit one NDJSON result per line on stdout as each DOI resolves, then a final summary line.",
            },
            "overwrite": {
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "Re-download PDFs even when the destination file already exists.",
            },
            "idempotency_key": {
                "type": "string",
                "required": False,
                "description": "Stable key for safe retries. Re-running with the same key returns the original envelope from a sidecar in <out>/.paper-fetch-idem/.",
            },
            "timeout": {
                "type": "integer",
                "required": False,
                "default": DEFAULT_TIMEOUT,
                "description": "HTTP timeout in seconds per request.",
            },
        },
        "exit_codes": {
            "0": "success (all DOIs resolved / previewed)",
            "1": "unresolved (some DOIs had no OA copy; no transport failure)",
            "2": "reserved for auth errors (currently unused)",
            "3": "validation error (bad arguments, missing input)",
            "4": "transport error (network / download / IO failure; retryable class)",
        },
        "error_codes": {
            "validation_error": {"retryable": False, "message": "Bad arguments or empty input"},
            "not_found": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["not_found"], "message": "No OA PDF found anywhere; OA availability changes over time"},
            "resolve_network_error": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["resolve_network_error"], "message": "Metadata resolvers failed with transport errors (timeout / 5xx / 403); OA availability is unknown, retry rather than treating as not_found"},
            "title_resolve_failed": {"retryable": False, "message": "Crossref returned no items for the given title; provide a DOI directly or refine the title"},
            "download_network_error": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["download_network_error"], "message": "Network failure during download"},
            "download_not_a_pdf": {"retryable": False, "message": "Response was not a PDF (HTML landing page)"},
            "download_host_not_allowed": {"retryable": False, "message": "PDF URL failed SSRF safety check (private IP, non-http(s) scheme, non-80/443 port, or blocked metadata host)"},
            "download_size_exceeded": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["download_size_exceeded"], "message": f"Response exceeded {MAX_PDF_SIZE // (1024*1024)} MB limit"},
            "download_io_error": {"retryable": True, "retry_after_hours": RETRY_AFTER_HOURS["download_io_error"], "message": "Local filesystem write failed"},
            "internal_error": {"retryable": False, "message": "Unexpected error"},
        },
        "envelope": {
            "success": {"ok": True, "data": {"results": [], "summary": {}, "next": []}, "meta": {}},
            "partial": {"ok": "partial", "data": {"results": [], "summary": {}, "next": []}, "meta": {}},
            "failure": {"ok": False, "error": {"code": "", "message": "", "retryable": False}, "meta": {}},
        },
        "result_fields": {
            "source_detail": "Optional per-source diagnostics (e.g. {'mirror': 'sci-hub.ru'} when source='scihub'). Present only when the resolving source has additional context worth surfacing for orchestrator routing.",
            "via": "Optional. Set to 'cloak' when the PDF was fetched through the CloakBrowser fallback (a Cloudflare-blocked URL retried via stealth Chromium). Absent for ordinary downloads. Requires PAPER_FETCH_CLOAK.",
            "resolver_errors": "Optional. Present on a resolve_network_error result; lists the metadata resolvers that failed with a transport error (timeout / 5xx / 403) rather than a genuine 404 miss, as [{source, detail}].",
        },
        "deprecations": [],
        "meta_fields": {
            "request_id": "Unique per-invocation id; correlates stderr progress events with the stdout envelope.",
            "latency_ms": "Wall-clock time from process start to this emit.",
            "schema_version": "Version of this schema contract; bumped on any additive or breaking change.",
            "cli_version": "Version of the paper-fetch binary that produced the envelope.",
            "auth_mode": "Either 'public' (OA sources, no client rate limit) or 'institutional' (user opted in via PAPER_FETCH_INSTITUTIONAL=1; 1 req/s rate limit to protect the operator's IP from publisher-side throttling).",
            "sources_tried": "Union of sources consulted across all DOIs in this run.",
            "title_resolution": "Present only when --title was used. Includes: query, resolver (the resolver whose match was used: 'crossref' or 'semantic_scholar'), resolvers_tried (ordered list of every resolver consulted), resolved_doi, resolved_title, match_score (Crossref relevance score; absent for S2 matches), candidates (top-3 from the winning resolver), low_confidence (true if the chosen DOI failed the score/gap heuristics), low_confidence_reason ('score_below_threshold' / 'ambiguous_runner_up' / 'no_match'), fallback_reason (why Crossref's match was rejected when S2 was used), and crossref_candidates (top-3 Crossref hits when the S2 fallback won, for cross-resolver inspection). Agents should sanity-check the top match — especially when low_confidence is true.",
        },
        "env": {
            "UNPAYWALL_EMAIL": "beravendanho@gmail.com",
            "PAPER_FETCH_INSTITUTIONAL": "1",
            "PAPER_FETCH_NO_SCIHUB": "Optional. Set to any value to disable the Sci-Hub fallback (enabled by default).",
            "PAPER_FETCH_SCIHUB_MIRRORS": "Optional. Comma-separated list of Sci-Hub mirror hostnames to try, in priority order, overriding the built-in defaults (e.g. 'sci-hub.ru,sci-hub.st,sci-hub.su').",
            "PAPER_FETCH_NO_LIBGEN": "Optional. Set to any value to disable the Library Genesis fallback (enabled by default).",
            "PAPER_FETCH_LIBGEN_MIRRORS": "Optional. Comma-separated list of Libgen mirror URLs to try, in priority order (e.g. 'https://libgen.li,https://libgen.vg,https://libgen.la').",
            "PAPER_FETCH_CLOAK": "Optional. Set to any value to enable the CloakBrowser fallback: when a download is blocked by Cloudflare (HTTP 403/429 or a non-PDF interstitial), the URL is retried through a stealth Chromium that can pass the JS challenge. Off by default; requires the cloak_pdf.py companion and a cloakbrowser-importable Python (see CLOAKBROWSER_PYTHON). Bytes are re-validated through the same %PDF + 50 MB checks. Operator action only — the agent cannot opt in.",
            "CLOAKBROWSER_PYTHON": "Optional. Path to a Python interpreter that can import cloakbrowser, used by the PAPER_FETCH_CLOAK fallback. If unset, the current interpreter is checked.",
            "PAPER_FETCH_CLOAK_HEADED": "Optional. Set to any value to make the cloak fallback launch a headed (visible) browser instead of headless. Harder Cloudflare challenges (e.g. science.org) defeat headless mode; the headed window clears them. Requires a display. Read by the cloak_pdf.py companion.",
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

EPILOG = """\
exit codes:
  0  all DOIs resolved successfully
  1  unresolved (some DOIs had no OA copy; no transport failure)
  3  validation error (bad arguments)
  4  transport error (network / download / IO failure; retryable class)

subcommands:
  schema                 print the machine-readable CLI schema and exit (no network)

stdin:
  paper-fetch -          read a single DOI from stdin
  paper-fetch --batch -  read DOIs line-by-line from stdin

output:
  stdout emits one JSON object per invocation (NDJSON with --stream).
  stderr emits NDJSON progress events when --format json, prose when --format text.
  stdout format auto-detects TTY: json when piped/captured, text in a terminal.

examples:
  %(prog)s 10.1038/s41586-020-2649-2
  %(prog)s 10.1038/s41586-020-2649-2 --dry-run
  %(prog)s --batch dois.txt --out ./papers --format text
  echo 10.1038/s41586-020-2649-2 | %(prog)s --batch -
  %(prog)s schema
"""


def _load_dois_from_args(args) -> list[str] | dict:
    """Parse DOI input from args. Returns list of DOIs or an error envelope dict.

    Title resolution (``--title``) is handled separately by ``_resolve_title``
    in main(); this loader sees only the resolved DOI by then.
    """
    inputs = [bool(args.batch), bool(args.doi), bool(getattr(args, "title", None))]
    if sum(inputs) > 1:
        return _envelope_err(
            "validation_error",
            "Pass exactly one of: positional DOI, --batch FILE, or --title TITLE.",
        )
    if args.batch:
        if args.batch == "-":
            text = sys.stdin.read()
            dois = [l.strip() for l in text.splitlines() if l.strip()]
        else:
            batch_path = Path(args.batch)
            if not batch_path.exists():
                return _envelope_err(
                    "validation_error",
                    f"Batch file not found: {args.batch}",
                    field="batch",
                )
            dois = [l.strip() for l in batch_path.read_text().splitlines() if l.strip()]
    elif args.doi == "-":
        text = sys.stdin.read()
        dois = [l.strip() for l in text.splitlines() if l.strip()]
    elif args.doi:
        # Check if the argument is an existing file (batch mode fallback)
        doi_as_file = Path(args.doi)
        if doi_as_file.is_file():
            dois = [l.strip() for l in doi_as_file.read_text().splitlines() if l.strip()]
        else:
            dois = [args.doi]
    else:
        return _envelope_err("validation_error", "Provide a DOI, --title, or --batch file")

    if not dois:
        return _envelope_err("validation_error", "No DOIs found in input")
    return dois


def _classify_low_confidence(score: float | None, gap: float | None) -> str | None:
    """Identify why a Crossref top match should be treated as low-confidence.

    Returns a single short reason string, or None if both heuristics pass.
    Order matters: ``score_below_threshold`` is the more diagnostic signal,
    so report that first when both fire.
    """
    if score is not None and score < TITLE_SCORE_MIN:
        return "score_below_threshold"
    if gap is not None and gap < TITLE_GAP_MIN:
        return "ambiguous_runner_up"
    return None


def _resolve_title(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Resolve a title to a DOI via Crossref → Semantic Scholar → OpenAlex → Europe PMC fallback chain.

    Always populates a ``resolution_meta`` dict (at least ``query`` and
    ``resolvers_tried``) so callers can surface it in the envelope's meta slot.
    """
    clean_title = re.sub(r"^[\[\"'‘“\s]+|[\]\"'’”\.\s]+$", "", title.strip())
    _progress("title_resolve_try", query=clean_title or title)
    resolvers_tried: list[str] = []

    # Pass 1 — Crossref. Confident hit short-circuits the chain.
    resolvers_tried.append("crossref")
    cr_doi, cr_top, cr_candidates = try_crossref_title(clean_title or title, timeout=timeout)
    cr_score = cr_top.get("score") if cr_top else None
    cr_gap: float | None = None
    if len(cr_candidates) >= 2:
        s0 = cr_candidates[0].get("score")
        s1 = cr_candidates[1].get("score")
        if isinstance(s0, (int, float)) and isinstance(s1, (int, float)):
            cr_gap = float(s0) - float(s1)
    cr_low_reason = _classify_low_confidence(cr_score, cr_gap) if cr_doi else "no_match"

    if cr_doi and cr_low_reason is None:
        _progress(
            "title_resolve_hit",
            query=title,
            resolver="crossref",
            doi=cr_doi,
            title=cr_top.get("title"),
            score=cr_score,
        )
        return cr_doi, {
            "query": title,
            "resolver": "crossref",
            "resolvers_tried": resolvers_tried,
            "resolved_doi": cr_doi,
            "resolved_title": cr_top.get("title"),
            "match_score": cr_score,
            "candidates": cr_candidates,
            "low_confidence": False,
        }

    # Pass 2 — Semantic Scholar match endpoint. Covers arXiv-only papers
    # (no Crossref DOI) and rescues low-confidence Crossref matches.
    _progress(
        "title_resolver_try",
        query=title,
        resolver="semantic_scholar",
        reason="crossref_" + cr_low_reason if cr_low_reason else "crossref_no_match",
    )
    resolvers_tried.append("semantic_scholar")
    s2_doi, s2_meta = try_semantic_scholar_match(clean_title or title, timeout=timeout)
    if s2_doi:
        _progress(
            "title_resolve_hit",
            query=title,
            resolver="semantic_scholar",
            doi=s2_doi,
            title=s2_meta.get("title"),
        )
        out: dict = {
            "query": title,
            "resolver": "semantic_scholar",
            "resolvers_tried": resolvers_tried,
            "resolved_doi": s2_doi,
            "resolved_title": s2_meta.get("title"),
            "candidates": [s2_meta],
            "low_confidence": False,
            "fallback_reason": cr_low_reason,
        }
        # Preserve the Crossref candidate list so an agent can compare what
        # each resolver thought was the top hit (helps when the two disagree).
        if cr_candidates:
            out["crossref_candidates"] = cr_candidates
        return s2_doi, out

    # Pass 3 — OpenAlex Title Search fallback
    try:
        oa_query = urllib.parse.quote(clean_title or title)
        oa_mailto = os.environ.get("OPENALEX_MAILTO") or EMAIL
        oa_url = f"https://api.openalex.org/works?search={oa_query}&per-page=3"
        if oa_mailto:
            oa_url += f"&mailto={urllib.parse.quote(oa_mailto)}"
        oa_data = _get_json(oa_url, timeout=timeout)
        results = (oa_data or {}).get("results") or []
        for res_item in results:
            raw_doi = (res_item.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
            if raw_doi and _DOI_RE.match(raw_doi):
                resolvers_tried.append("openalex")
                _progress(
                    "title_resolve_hit",
                    query=title,
                    resolver="openalex",
                    doi=raw_doi,
                    title=res_item.get("title"),
                )
                return raw_doi, {
                    "query": title,
                    "resolver": "openalex",
                    "resolvers_tried": resolvers_tried,
                    "resolved_doi": raw_doi,
                    "resolved_title": res_item.get("title"),
                    "candidates": results,
                    "low_confidence": False,
                }
    except Exception:
        pass

    # Pass 4 — Europe PMC Title Search fallback
    try:
        epmc_query = urllib.parse.quote(clean_title or title)
        epmc_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={epmc_query}&format=json&pageSize=3"
        epmc_data = _get_json(epmc_url, timeout=timeout)
        epmc_results = (epmc_data or {}).get("resultList", {}).get("result") or []
        for r_item in epmc_results:
            r_doi = (r_item.get("doi") or "").strip()
            if r_doi and _DOI_RE.match(r_doi):
                resolvers_tried.append("europe_pmc")
                _progress(
                    "title_resolve_hit",
                    query=title,
                    resolver="europe_pmc",
                    doi=r_doi,
                    title=r_item.get("title"),
                )
                return r_doi, {
                    "query": title,
                    "resolver": "europe_pmc",
                    "resolvers_tried": resolvers_tried,
                    "resolved_doi": r_doi,
                    "resolved_title": r_item.get("title"),
                    "candidates": epmc_results,
                    "low_confidence": False,
                }
    except Exception:
        pass

    # Pass 5 — every resolver missed. If Crossref had *any* candidate, return
    # it with a low_confidence flag so the agent can either (a) proceed with
    # caution or (b) bail out via the dry-run preview.
    if cr_doi:
        _progress(
            "title_resolve_hit",
            query=title,
            resolver="crossref",
            doi=cr_doi,
            title=cr_top.get("title"),
            score=cr_score,
            low_confidence=True,
            reason=cr_low_reason,
        )
        return cr_doi, {
            "query": title,
            "resolver": "crossref",
            "resolvers_tried": resolvers_tried,
            "resolved_doi": cr_doi,
            "resolved_title": cr_top.get("title"),
            "match_score": cr_score,
            "candidates": cr_candidates,
            "low_confidence": True,
            "low_confidence_reason": cr_low_reason,
        }

    _progress("title_resolve_miss", query=title, resolvers_tried=resolvers_tried)
    return None, {
        "query": title,
        "resolvers_tried": resolvers_tried,
        "candidates": [],
    }


# ===========================================================================
# EXPANDED LEGITIMATE DISCOVERY LAYER (ADDED — ORIGINAL CODE PRESERVED)
# ===========================================================================
# This layer is intentionally additive: the original fetch implementation,
# original sources, and original CLI remain intact above. The new wrapper below
# adds additional metadata/repository discovery only after the original fetch
# path has been exhausted, while title resolution is replaced by a stricter
# multi-database resolver.
#
# Added legitimate/open research sources:
#   - OpenAIRE Graph
#   - HAL Open Archive
#   - Zenodo Records API
#   - DataCite REST API
#   - DOAJ article search (best-effort metadata/PDF links)
#
# Existing sources are preserved and remain usable:
#   Unpaywall, Semantic Scholar, OpenAlex, Europe PMC, PMC, PubMed,
#   arXiv, bioRxiv/medRxiv, CORE, Crossref links, publisher-direct,
#   ACL Anthology, DOI resolver, and the pre-existing fallbacks.
#
# OpenAIRE currently exposes a production Graph v3 endpoint and documents
# DOI/title-based research-product search. DataCite exposes public DOI metadata
# retrieval/search. HAL exposes a public search API, and Zenodo exposes a
# records search API. These are used here only for discovery of publicly
# available records/files; a candidate is accepted only after PDF validation.

from difflib import SequenceMatcher
import unicodedata


EXPANDED_SOURCE_NAMES = {
    "openaire": "OpenAIRE",
    "hal": "HAL",
    "zenodo": "Zenodo",
    "datacite": "DataCite",
    "doaj": "DOAJ",
}

try:
    SOURCE_NAMES.update(EXPANDED_SOURCE_NAMES)  # type: ignore[name-defined]
except Exception:
    pass


def _norm_match_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _token_jaccard(a: str, b: str) -> float:
    sa = set(_norm_match_text(a).split())
    sb = set(_norm_match_text(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _title_similarity(a: str, b: str) -> float:
    """Robust bibliographic-title similarity.

    The resolver must tolerate common index differences such as:
      - omitted subtitles;
      - punctuation/Unicode differences;
      - hyphen vs space;
      - singular/plural or minor token noise;
      - title metadata with small formatting additions.

    At the same time, it must not treat merely related-topic titles as the
    same paper.  The requested-title token coverage therefore contributes a
    strong signal, while character similarity handles formatting changes.
    """
    na = _norm_match_text(a)
    nb = _norm_match_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    sa = set(na.split())
    sb = set(nb.split())
    if not sa or not sb:
        return 0.0

    seq = SequenceMatcher(None, na, nb).ratio()
    jac = len(sa & sb) / len(sa | sb)
    coverage = len(sa & sb) / len(sa)

    # Also compare token sequences after sorting unique tokens. This helps
    # with indexes that reorder subtitle fragments or publication metadata.
    sorted_a = " ".join(sorted(sa))
    sorted_b = " ".join(sorted(sb))
    sorted_seq = SequenceMatcher(None, sorted_a, sorted_b).ratio()

    score = (
        0.34 * coverage
        + 0.26 * jac
        + 0.25 * seq
        + 0.15 * sorted_seq
    )
    return round(min(1.0, score), 6)


def _deep_find_pdf_urls(obj, *, limit: int = 12) -> list[str]:
    """Best-effort recursive extraction of direct-looking PDF URLs from JSON."""
    found: list[str] = []
    seen: set[str] = set()

    def walk(node):
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    raw = value.strip()
                    if raw.startswith(("http://", "https://")):
                        low = raw.casefold()
                        if (
                            ".pdf" in low
                            or "/pdf/" in low
                            or "download" in low and ("file" in key.casefold() or "content" in key.casefold())
                            or key.casefold() in {"download", "download_url", "pdf_url", "file_url", "fulltext_url", "content_url"}
                        ):
                            clean = raw.replace("&amp;", "&")
                            if clean not in seen:
                                seen.add(clean)
                                found.append(clean)
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
                if len(found) >= limit:
                    break

    walk(obj)
    return found


def _extract_first_year(meta: dict) -> int | None:
    for key in ("year", "publication_year", "published", "issued"):
        value = meta.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            m = re.search(r"\b(19|20)\d{2}\b", value)
            if m:
                return int(m.group(0))
    return None


def _merge_candidate_meta(*metas: dict | None) -> dict:
    out: dict = {}
    for meta in metas:
        if not isinstance(meta, dict):
            continue
        for key, value in meta.items():
            if value and not out.get(key):
                out[key] = value
    return out


def try_openaire(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search OpenAIRE Graph v3 for a DOI or title."""
    try:
        params = {
            "page": 1,
            "pageSize": 10,
        }
        if doi:
            params["filter"] = f"type:publication,ids.doi:{doi}"
        elif title:
            params["search"] = title
            params["type"] = "publication"
        else:
            return [], {}, []
        url = "https://api.openaire.eu/graph/v3/research-products?" + urllib.parse.urlencode(params)
        data = _get_json(url, timeout=timeout)
        results = (data or {}).get("results") or []
        urls: list[str] = []
        candidates: list[dict] = []
        for item in results:
            item_meta = item if isinstance(item, dict) else {}
            candidates.append(item_meta)
            urls.extend(_deep_find_pdf_urls(item_meta))
        meta: dict = {}
        if results:
            first = results[0] or {}
            meta = {
                "title": first.get("mainTitle") or first.get("title"),
                "year": first.get("publicationDate") or first.get("year"),
                "author": None,
                "journal": None,
                "openaire_id": first.get("id"),
            }
            authors = first.get("authors") or first.get("author") or []
            if isinstance(authors, list) and authors:
                a0 = authors[0]
                if isinstance(a0, dict):
                    meta["author"] = a0.get("fullName") or a0.get("name")
                elif isinstance(a0, str):
                    meta["author"] = a0
        return list(dict.fromkeys(urls)), meta, candidates[:10]
    except Exception as exc:
        _progress("source_miss", source="openaire", reason=str(exc))
        return [], {}, []


def try_hal(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search HAL Open Archive."""
    try:
        if doi:
            q = f'doiId_s:"{doi}"'
        elif title:
            q = title
        else:
            return [], {}, []
        params = {
            "q": q,
            "wt": "json",
            "rows": 10,
            "fl": "*,uri_s,fileMain_s,fileMain,fileAnnexes_s,title_s,docType_s,doiId_s,uri_s",
        }
        url = "https://api.archives-ouvertes.fr/search/?" + urllib.parse.urlencode(params)
        data = _get_json(url, timeout=timeout)
        docs = ((data or {}).get("response") or {}).get("docs") or []
        urls: list[str] = []
        for doc in docs:
            for key in ("fileMain_s", "fileMain", "fileAnnexes_s", "uri_s"):
                value = doc.get(key)
                values = value if isinstance(value, list) else [value]
                for v in values:
                    if isinstance(v, str) and v.startswith(("http://", "https://")):
                        low = v.casefold()
                        if ".pdf" in low or "/file/" in low or key.startswith("file"):
                            urls.append(v)
        meta: dict = {}
        if docs:
            first = docs[0]
            title_value = first.get("title_s") or first.get("title")
            if isinstance(title_value, list):
                title_value = title_value[0] if title_value else None
            meta = {
                "title": title_value,
                "doi": first.get("doiId_s") if isinstance(first.get("doiId_s"), str) else None,
                "hal_id": first.get("docid"),
                "document_type": first.get("docType_s"),
            }
        return list(dict.fromkeys(urls)), meta, docs[:10]
    except Exception as exc:
        _progress("source_miss", source="hal", reason=str(exc))
        return [], {}, []


def try_zenodo(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search Zenodo published records and extract file download URLs."""
    try:
        q = doi if doi else title
        if not q:
            return [], {}, []
        params = {
            "q": q,
            "page": 1,
            "size": 10,
            "sort": "bestmatch",
        }
        url = "https://zenodo.org/api/records?" + urllib.parse.urlencode(params)
        data = _get_json(url, timeout=timeout)
        hits = ((data or {}).get("hits") or {}).get("hits") or []
        urls: list[str] = []
        candidates: list[dict] = []
        for hit in hits:
            candidates.append(hit)
            urls.extend(_deep_find_pdf_urls(hit))
        meta: dict = {}
        if hits:
            first = hits[0].get("metadata") or {}
            creators = first.get("creators") or []
            meta = {
                "title": first.get("title"),
                "year": first.get("publication_date") or first.get("date"),
                "author": (creators[0].get("name") if creators and isinstance(creators[0], dict) else None),
                "zenodo_id": hits[0].get("id"),
                "doi": first.get("doi"),
            }
        return list(dict.fromkeys(urls)), meta, candidates[:10]
    except Exception as exc:
        _progress("source_miss", source="zenodo", reason=str(exc))
        return [], {}, []


def try_datacite(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Search DataCite public DOI metadata."""
    try:
        if doi:
            url = f"https://api.datacite.org/dois/{urllib.parse.quote(doi, safe='')}"
            data = _get_json(url, timeout=timeout)
            records = [data.get("data")] if isinstance(data, dict) and data.get("data") else []
        elif title:
            params = {
                "query": title,
                "page[size]": 10,
            }
            url = "https://api.datacite.org/dois?" + urllib.parse.urlencode(params)
            data = _get_json(url, timeout=timeout)
            records = (data or {}).get("data") or []
        else:
            return [], {}, []

        urls: list[str] = []
        candidates: list[dict] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            candidates.append(record)
            urls.extend(_deep_find_pdf_urls(record))
            attrs = record.get("attributes") or {}
            url_value = attrs.get("url")
            if isinstance(url_value, str) and url_value.startswith(("http://", "https://")) and ".pdf" in url_value.casefold():
                urls.append(url_value)
        meta: dict = {}
        if records:
            attrs = records[0].get("attributes") or {}
            titles = attrs.get("titles") or []
            title_value = titles[0].get("title") if titles and isinstance(titles[0], dict) else None
            creators = attrs.get("creators") or []
            author = None
            if creators and isinstance(creators[0], dict):
                author = creators[0].get("name") or creators[0].get("familyName")
            meta = {
                "title": title_value,
                "year": attrs.get("publicationYear"),
                "author": author,
                "publisher": attrs.get("publisher"),
                "resource_type": (attrs.get("types") or {}).get("resourceTypeGeneral"),
                "datacite_doi": attrs.get("doi"),
            }
        return list(dict.fromkeys(urls)), meta, candidates[:10]
    except Exception as exc:
        _progress("source_miss", source="datacite", reason=str(exc))
        return [], {}, []


def try_doaj(doi: str | None = None, *, title: str | None = None, timeout: int = 20) -> tuple[list[str], dict, list[dict]]:
    """Best-effort DOAJ article search. DOAJ is used for metadata/direct OA links."""
    try:
        if doi:
            endpoint = "https://doaj.org/api/search/articles/doi:" + urllib.parse.quote(doi, safe="")
        elif title:
            endpoint = "https://doaj.org/api/search/articles/title:" + urllib.parse.quote(title, safe="")
        else:
            return [], {}, []
        data = _get_json(endpoint, timeout=timeout)
        results = (data or {}).get("results") or []
        urls: list[str] = []
        for item in results[:10]:
            urls.extend(_deep_find_pdf_urls(item))
        meta: dict = {}
        if results:
            bib = ((results[0].get("bibjson") or {}) if isinstance(results[0], dict) else {})
            authors = bib.get("author") or []
            meta = {
                "title": bib.get("title"),
                "year": bib.get("year"),
                "author": authors[0].get("name") if authors and isinstance(authors[0], dict) else None,
                "journal": (bib.get("journal") or {}).get("title") if isinstance(bib.get("journal"), dict) else None,
            }
        return list(dict.fromkeys(urls)), meta, results[:10]
    except Exception as exc:
        _progress("source_miss", source="doaj", reason=str(exc))
        return [], {}, []


def _candidate_records_for_title(title: str, *, timeout: int) -> list[dict]:
    """Gather title candidates from all available metadata indexes concurrently."""
    candidates: list[dict] = []

    def fetch_cr():
        cr_doi, cr_top, cr_candidates = try_crossref_title(title, timeout=timeout)
        return [{"resolver": "crossref", "doi": normalize_doi(str(item.get("doi") or "")), "title": item.get("title"), "year": item.get("year"), "author": item.get("author"), "journal": item.get("journal"), "raw_score": item.get("score")} for item in cr_candidates]

    def fetch_s2():
        s2_doi, s2_meta = try_semantic_scholar_match(title, timeout=timeout)
        if s2_doi:
            return [{"resolver": "semantic_scholar", "doi": normalize_doi(s2_doi), "title": s2_meta.get("title"), "year": s2_meta.get("year"), "author": s2_meta.get("author"), "journal": s2_meta.get("journal")}]
        return []

    def fetch_oa():
        params = {"search": title, "per-page": 5, "select": "id,doi,title,publication_year,authorships,primary_location,open_access"}
        oa_mailto = os.environ.get("OPENALEX_MAILTO") or EMAIL
        if oa_mailto: params["mailto"] = oa_mailto
        data = _get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
        res = []
        for item in (data or {}).get("results") or []:
            raw_doi = item.get("doi") or ""
            raw_doi = raw_doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
            authorships = item.get("authorships") or []
            author = ((authorships[0].get("author") or {}).get("display_name")) if authorships else None
            res.append({"resolver": "openalex", "doi": normalize_doi(raw_doi), "title": item.get("title"), "year": item.get("publication_year"), "author": author, "journal": ((item.get("primary_location") or {}).get("source") or {}).get("display_name")})
        return res

    def fetch_epmc():
        params = {"query": f'TITLE:"{title}"', "format": "json", "pageSize": 5}
        data = _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params), timeout=timeout)
        return [{"resolver": "europe_pmc", "doi": normalize_doi(str(item.get("doi") or "")), "title": item.get("title"), "year": item.get("pubYear"), "author": item.get("authorString"), "journal": item.get("journalTitle")} for item in (data or {}).get("resultList", {}).get("result") or []]

    def fetch_ntrs():
        return _try_ntrs_title_records(title, timeout=timeout)
        
    def fetch_pubmed():
        return _try_pubmed_title_records(title, timeout=timeout)

    tasks = [
        fetch_cr, fetch_s2, fetch_oa, fetch_epmc, fetch_ntrs, fetch_pubmed,
        lambda: [{"resolver": "openaire", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_openaire(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "hal", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_hal(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "datacite", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_datacite(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "zenodo", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_zenodo(title=title, timeout=timeout)] if m],
        lambda: [{"resolver": "doaj", "doi": normalize_doi(str(m.get("doi") or m.get("datacite_doi") or "")), "title": m.get("title"), "year": _extract_first_year(m), "author": m.get("author")} for _, m, _ in [try_doaj(title=title, timeout=timeout)] if m],
    ]

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = [executor.submit(t) for t in tasks]
        for fut in concurrent.futures.as_completed(futures):
            try:
                res = fut.result()
                if res:
                    candidates.extend(res)
            except Exception:
                pass

    return candidates

def _rank_title_candidates(requested_title: str, candidates: list[dict]) -> tuple[dict | None, list[dict]]:
    """Strictly rank title candidates and reject ambiguous/weak matches."""
    ranked: list[dict] = []
    for cand in candidates:
        ct = cand.get("title") or ""
        if not ct:
            continue
        title_score = _title_similarity(requested_title, ct)
        # Author agreement is a useful secondary signal when available.
        author_bonus = 0.0
        if cand.get("author"):
            # For title-only searches we only use a small bonus; never enough
            # to rescue a clearly wrong title.
            author_bonus = 0.03
        total = min(1.0, title_score + author_bonus)
        c = dict(cand)
        c["title_similarity"] = title_score
        c["rank_score"] = round(total, 6)
        ranked.append(c)
    ranked.sort(key=lambda x: x.get("rank_score", 0.0), reverse=True)
    return (ranked[0] if ranked else None), ranked[:10]


def _resolve_title_expanded(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Multi-index title resolver with strict similarity validation."""
    clean_title = re.sub(r"^[\[\"'‘“\s]+|[\]\"'’”\.\s]+$", "", title.strip())
    clean_title = clean_title or title.strip()
    _progress("title_resolve_try", query=clean_title, resolver="expanded_multi_source")
    candidates = _candidate_records_for_title(clean_title, timeout=timeout)
    best, ranked = _rank_title_candidates(clean_title, candidates)

    resolution = {
        "query": title,
        "resolver": "expanded_multi_source",
        "resolvers_tried": sorted({c.get("resolver") for c in candidates if c.get("resolver")}),
        "candidates": ranked,
        "low_confidence": True,
    }

    if not best:
        resolution["low_confidence_reason"] = "no_match"
        _progress("title_resolve_miss", query=title, resolver="expanded_multi_source")
        return None, resolution

    best_score = float(best.get("rank_score") or 0.0)
    second_score = float(ranked[1].get("rank_score") or 0.0) if len(ranked) > 1 else 0.0
    gap = best_score - second_score
    resolution.update({
        "resolved_doi": best.get("doi"),
        "resolved_title": best.get("title"),
        "match_score": best_score,
        "title_similarity": best.get("title_similarity"),
        "candidate_gap": round(gap, 6),
        "resolver_selected": best.get("resolver"),
    })

    # Strict default for title-only resolution. Do not reproduce the old
    # behaviour of returning a weak Crossref hit simply because a result exists.
    if not best.get("doi"):
        resolution["low_confidence_reason"] = "no_doi"
        return None, resolution

    if best_score < 0.90:
        resolution["low_confidence_reason"] = "title_similarity_below_threshold"
        return None, resolution

    if len(ranked) > 1 and gap < 0.03 and best_score < 0.98:
        resolution["low_confidence_reason"] = "ambiguous_top_candidates"
        return None, resolution

    resolution["low_confidence"] = False
    _progress(
        "title_resolve_hit",
        query=title,
        resolver=best.get("resolver"),
        doi=best.get("doi"),
        title=best.get("title"),
        score=best_score,
    )
    return normalize_doi(str(best["doi"])), resolution


# Preserve the original resolver for compatibility/debugging while routing new
# title searches through the strict multi-source resolver.
_resolve_title_original = _resolve_title
_resolve_title = _resolve_title_expanded


# Keep a handle to the original DOI fetch implementation. The wrapper below
# only adds new discovery sources after the original source chain returns a
# non-success result.
_fetch_original = fetch


def _expanded_download_candidate(
    doi: str,
    out_dir: Path,
    source: str,
    url: str,
    *,
    timeout: int,
    overwrite: bool,
    candidates: list[dict],
    meta: dict,
) -> dict | None:
    if not url:
        return None
    candidate = {"source": source, "url": url}
    if candidate not in candidates:
        candidates.append(candidate)
    if not overwrite:
        try:
            existing = next(iter(out_dir.glob(f"{_slug(doi, n=20)}*.pdf")), None)
            if existing and validate_pdf_data(existing.read_bytes())[0]:
                return {
                    "doi": doi,
                    "success": True,
                    "source": "cache",
                    "pdf_url": url,
                    "file": str(existing),
                    "meta": meta or {},
                    "sources_tried": [],
                    "skipped": True,
                }
        except Exception:
            pass
    fname = _filename(meta or {"title": doi})
    dest = out_dir / fname
    err = _download(url, dest, timeout=timeout)
    if err is None:
        return {
            "doi": doi,
            "success": True,
            "source": source,
            "pdf_url": url,
            "file": str(dest),
            "meta": meta or {},
            "sources_tried": [source],
            "candidates": candidates,
            "expanded_discovery": True,
        }
    try:
        if dest.exists() and not validate_pdf_data(dest.read_bytes())[0]:
            dest.unlink()
    except Exception:
        pass
    return None


def _fetch_from_expanded_sources(
    doi: str,
    out_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    timeout: int,
    original_result: dict,
) -> dict:
    """Search additional legitimate metadata/repository sources concurrently."""
    if dry_run:
        original_result.setdefault("expanded_discovery", {})
        original_result["expanded_discovery"]["status"] = "skipped_for_dry_run"
        return original_result

    sources_tried: list[str] = list(original_result.get("sources_tried") or [])
    candidates: list[dict] = []
    meta = dict(original_result.get("meta") or {})
    errors: list[dict] = []
    
    resolvers = [
        ("openaire", lambda: try_openaire(doi=doi, timeout=timeout)),
        ("hal", lambda: try_hal(doi=doi, timeout=timeout)),
        ("zenodo", lambda: try_zenodo(doi=doi, timeout=timeout)),
        ("datacite", lambda: try_datacite(doi=doi, timeout=timeout)),
        ("doaj", lambda: try_doaj(doi=doi, timeout=timeout)),
    ]
    
    import concurrent.futures
    
    def run_resolver(name, resolver_func):
        try:
            urls, extra_meta, raw = resolver_func()
            return name, urls, extra_meta, raw, None
        except Exception as exc:
            return name, None, None, None, str(exc)
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(resolvers)) as executor:
        futures = [executor.submit(run_resolver, name, func) for name, func in resolvers]
        for fut in concurrent.futures.as_completed(futures):
            name, urls, extra_meta, raw, exc_str = fut.result()
            if name not in sources_tried:
                sources_tried.append(name)
            
            if exc_str:
                errors.append({"source": name, "reason": exc_str})
                _progress("source_miss", doi=doi, source=name, reason=exc_str, phase="expanded")
                continue
                
            for key, value in (extra_meta or {}).items():
                if value and not meta.get(key):
                    meta[key] = value
                    
            if urls:
                for url in urls:
                    if not url:
                        continue
                    _progress("source_hit", doi=doi, source=name, pdf_url=url, phase="expanded")
                    result = _expanded_download_candidate(
                        doi,
                        out_dir,
                        name,
                        url,
                        timeout=timeout,
                        overwrite=overwrite,
                        candidates=candidates,
                        meta=meta,
                    )
                    if result:
                        result["meta"] = meta
                        result["sources_tried"] = sources_tried
                        result.setdefault("title_resolution", original_result.get("title_resolution"))
                        return result
                    
            _progress("source_miss", doi=doi, source=name, phase="expanded")

    title = meta.get("title")
    if title:
        title_resolvers = [
            ("openaire", lambda: try_openaire(title=title, timeout=timeout)),
            ("hal", lambda: try_hal(title=title, timeout=timeout)),
            ("zenodo", lambda: try_zenodo(title=title, timeout=timeout)),
            ("datacite", lambda: try_datacite(title=title, timeout=timeout)),
            ("doaj", lambda: try_doaj(title=title, timeout=timeout)),
        ]
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(title_resolvers)) as executor:
            futures = [executor.submit(run_resolver, name, func) for name, func in title_resolvers]
            for fut in concurrent.futures.as_completed(futures):
                name, urls, extra_meta, raw, exc_str = fut.result()
                if name not in sources_tried:
                    sources_tried.append(name)
                    
                if exc_str:
                    errors.append({"source": name, "reason": exc_str})
                    _progress("source_miss", doi=doi, source=name, reason=exc_str, phase="expanded")
                    continue
                    
                for key, value in (extra_meta or {}).items():
                    if value and not meta.get(key):
                        meta[key] = value
                        
                if urls:
                    for url in urls:
                        if not url:
                            continue
                        _progress("source_hit", doi=doi, source=name, pdf_url=url, phase="expanded")
                        result = _expanded_download_candidate(
                            doi,
                            out_dir,
                            name,
                            url,
                            timeout=timeout,
                            overwrite=overwrite,
                            candidates=candidates,
                            meta=meta,
                        )
                        if result:
                            result["meta"] = meta
                            result["sources_tried"] = sources_tried
                            result.setdefault("title_resolution", original_result.get("title_resolution"))
                            return result
                        
                _progress("source_miss", doi=doi, source=name, phase="expanded")
                
    original_result["sources_tried"] = sources_tried
    original_result["expanded_discovery"] = {
        "status": "exhausted",
        "sources": sources_tried,
        "candidates": candidates,
        "errors": errors,
        "metadata": meta,
    }
    original_result["meta"] = meta
    return original_result


# ===========================================================================
# END OF ADDITIVE EXPANDED DISCOVERY LAYER

def fetch(
    doi: str,
    out_dir: Path,
    *,
    dry_run: bool,
    overwrite: bool,
    timeout: int,
    sources: list[str] | None = None,
) -> dict:
    """Additive wrapper around the original fetch() with expanded discovery."""
    result = _fetch_original(
        doi,
        out_dir,
        dry_run=dry_run,
        overwrite=overwrite,
        timeout=timeout,
        sources=sources,
    )
    if result.get("success") or dry_run:
        return result

    # Respect explicit source filtering. If the caller supplied --sources and
    # none of the new sources were requested, do not surprise them with extra calls.
    expanded_allowed = {s.strip().lower() for s in sources} if sources else None
    if expanded_allowed is not None and not expanded_allowed.intersection(EXPANDED_SOURCE_NAMES):
        return result

    return _fetch_from_expanded_sources(
        normalize_doi(doi),
        out_dir,
        dry_run=dry_run,
        overwrite=overwrite,
        timeout=timeout,
        original_result=result,
    )


# ===========================================================================
# END OF ADDITIVE EXPANDED DISCOVERY LAYER

def _default_format() -> str:
    try:
        return "json" if not sys.stdout.isatty() else "text"
    except Exception:
        return "json"


def _decide_exit(results: list[dict]) -> int:
    """Pick the most descriptive exit code from per-item outcomes."""
    any_validation = False
    any_transport = False
    any_unresolved = False
    any_failure = False
    for r in results:
        if r.get("success"):
            continue
        any_failure = True
        err = r.get("error") or {}
        code = err.get("code", "")
        if code == "validation_error":
            any_validation = True
        elif code == "not_found":
            any_unresolved = True
        elif code.startswith("download_") or code == "resolve_network_error":
            any_transport = True
        else:
            any_unresolved = True
    if not any_failure:
        return EXIT_SUCCESS
    # Validation errors win: a malformed DOI is a caller bug that won't fix
    # itself on retry, so surface it even when the batch also has transient
    # network or not-found failures the caller might otherwise blindly retry.
    if any_validation:
        return EXIT_VALIDATION
    if any_transport:
        return EXIT_TRANSPORT
    return EXIT_UNRESOLVED


def _next_hints(results: list[dict], args) -> list[str]:
    """Suggest follow-up commands for the failed subset.

    Hints are intended for an agent or human to copy-paste and run, so all
    user-controlled values (DOIs, --out path) are shell-quoted to prevent
    a maliciously crafted DOI from injecting commands.
    """
    failed = [r["doi"] for r in results if not r.get("success")]
    if not failed:
        return []
    out = shlex.quote(args.out)
    if len(failed) == 1:
        cmd = f"paper-fetch {shlex.quote(failed[0])} --out {out}"
        if args.dry_run:
            cmd += " --dry-run"
        return [cmd]
    # Multiple failures — feed them via stdin so each DOI is delimited by a
    # real newline rather than interpolated into the shell command.
    payload = shlex.quote("\n".join(failed) + "\n")
    cmd = f"printf %s {payload} | paper-fetch --batch - --out {out}"
    if args.dry_run:
        cmd += " --dry-run"
    return [cmd]


def generate_detailed_report(results: list[dict], dois: list[str]) -> str:
    """Generate a detailed report of the DOI processing results."""

    # Initialize counters
    total = len(dois)
    successes = 0
    request_denials = 0  # transport errors
    not_found = 0        # unresolved errors

    # Source usage tracking
    source_success_count = {}
    source_attempt_count = {}

    # Detailed results per DOI
    doi_details = []

    for i, (doi, result) in enumerate(zip(dois, results)):
        success = result.get("success", False)
        error = result.get("error", {})
        error_code = error.get("code", "") if error else ""
        source = result.get("source", "unknown")
        pdf_url = result.get("pdf_url", "none")

        # Track source attempts
        if source not in source_attempt_count:
            source_attempt_count[source] = 0
        source_attempt_count[source] += 1

        if success:
            successes += 1
            status = "sucesso"
            status_detail = f"Fonte: {source}"
            if source not in source_success_count:
                source_success_count[source] = 0
            source_success_count[source] += 1
        else:
            # Classify failure type
            if error_code.startswith("download_") or error_code == "resolve_network_error":
                request_denials += 1

                status = "requisição negada"
                status_detail = f"Erro: {error_code}"
            else:
                not_found += 1
                status = "não encontrado"
                status_detail = f"Erro: {error_code}" if error_code else "Nenhuma fonte encontrou o PDF"

        doi_details.append({
            "index": i + 1,
            "doi": doi,
            "status": status,
            "status_detail": status_detail,
            "source": source,
            "pdf_url": pdf_url if pdf_url != "none" else "Não obtido"
        })

    # Build the report
    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("RELATÓRIO DETALHADO DE PROCESSAMENTO DE DOIS")
    report_lines.append("=" * 60)
    report_lines.append("")

    # Summary statistics
    report_lines.append("RESUMO GERAL:")
    report_lines.append(f"  Total de DOIs processados: {total}")
    report_lines.append(f"  Sucessos (downloads concluídos): {successes}")
    report_lines.append(f"  Requisições negadas (erros de transporte): {request_denials}")
    report_lines.append(f"  Não encontrados (sem cópia OA disponível): {not_found}")
    report_lines.append("")

    # Success rate
    if total > 0:
        success_rate = (successes / total) * 100
        report_lines.append(f"Taxa de sucesso: {success_rate:.1f}%")
        report_lines.append("")

    # Source utilization
    report_lines.append("UTILIZAÇÃO DAS FONTES:")
    all_sources = set()
    for d in source_attempt_count.keys():
        if d is not None:
            all_sources.add(d)
    for d in source_success_count.keys():
        if d is not None:
            all_sources.add(d)
    for source in sorted(all_sources):
        attempted = source_attempt_count.get(source, 0)
        successful = source_success_count.get(source, 0)
        if attempted > 0:
            rate = (successful / attempted) * 100
            report_lines.append(f"  {source}: {successful}/{attempted} ({rate:.1f}%)")
        else:
            report_lines.append(f"  {source}: 0/0 (0.0%)")
    report_lines.append("")

    # Detailed DOI results
    report_lines.append("DETALHAMENTO POR DOI:")
    report_lines.append("-" * 60)
    for detail in doi_details:
        report_lines.append(f"{detail['index']:3d}. DOI: {detail['doi']}")
        report_lines.append(f"     Status: {detail['status']}")
        report_lines.append(f"     Detalhe: {detail['status_detail']}")
        report_lines.append(f"     Fonte: {detail['source']}")
        report_lines.append(f"     PDF URL: {detail['pdf_url']}")
        report_lines.append("")

    # Recommendations
    report_lines.append("RECOMENDAÇÕES:")
    report_lines.append("-" * 60)

    if not_found > 0:
        report_lines.append(f"• {not_found} DOIs não tiveram cópia de acesso aberto encontrada.")
        report_lines.append("  Considere verificar acesso institucional ou usar serviços como")
        report_lines.append("  solicitação de cópia diretamente aos autores.")

    if request_denials > 0:
        report_lines.append(f"• {request_denials} DOIs falharam devido a erros de transporte.")
        report_lines.append("  Pode ser problemas temporários de rede ou bloqueio temporário.")
        report_lines.append("  Considere tentar novamente em momentos diferentes.")

    if successes > 0:
        report_lines.append(f"• {successes} DOIs foram baixados com sucesso.")
        report_lines.append("  Os PDFs estão disponíveis no diretório 'pdfs'.")

    if successes == 0 and request_denials == 0 and not_found > 0:
        report_lines.append("• Nenhum DOI foi baixado com sucesso.")
        report_lines.append("  Verifique se os DOIs são válidos e se há conectividade de rede.")
        report_lines.append("  Consulte um bibliotecário para opções de acesso institucional.")

    report_lines.append("")
    report_lines.append("=" * 60)
    report_lines.append("Relatório gerado automaticamente pelo paper-fetch")
    report_lines.append("=" * 60)

    return "\n".join(report_lines)



def _load_dois_and_titles_from_file(path: Path) -> tuple[list[str], list[str]]:
    """Read DOI's.txt and separate DOI lines from trailing article titles.

    Every non-empty line up to and including the last valid DOI is treated as
    part of the DOI section. Every non-empty line after the last valid DOI is
    treated as an article title.

    This allows DOI's.txt to have the following structure::

        10.1234/example.one
        10.5678/example.two

        Article title one
        Article title two

    Optional section headers such as ``ARTIGOS:`` are ignored.
    """
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    dois: list[str] = []
    titles: list[str] = []

    ignored_headers = {
        "artigo", "artigos", "artigos:", "titulo", "título", "titulos",
        "títulos", "titulo:", "título:", "titulos:", "títulos:",
        "nomes dos artigos", "nomes dos artigos:", "dois", "doi", "dois:", "doi:"
    }

    for line in lines:
        if line.casefold() in ignored_headers or re.fullmatch(r"[-=_]{3,}", line):
            continue
        
        candidate = normalize_doi(line)
        if _DOI_RE.match(candidate):
            dois.append(candidate)
        else:
            titles.append(line)

    return dois, titles


def _write_title_report(title_results: list[dict], titles: list[str], path: Path) -> None:
    """Write a report for articles discovered by title."""
    if path.is_dir():
        try:
            import shutil
            shutil.rmtree(path)
        except Exception:
            path = path.parent / f"{path.stem}.txt"
    lines = [
        "=" * 70,
        "RELATÓRIO DE ARTIGOS PESQUISADOS PELO NOME",
        "=" * 70,
        "",
        f"Total de títulos encontrados: {len(titles)}",
        f"Total de pesquisas realizadas: {len(title_results)}",
        f"Downloads concluídos: {sum(1 for r in title_results if r.get('success'))}",
        f"Falhas: {sum(1 for r in title_results if not r.get('success'))}",
        "",
        "DETALHAMENTO:",
        "-" * 70,
        "",
    ]

    for index, result in enumerate(title_results, start=1):
        title = result.get("searched_title", result.get("title", "Título desconhecido"))
        resolved_doi = result.get("resolved_doi") or result.get("doi") or "Não encontrado"
        success = result.get("success", False)

        lines.append(f"{index}. TÍTULO: {title}")
        lines.append(f"   DOI encontrado: {resolved_doi}")

        resolution = result.get("title_resolution") or {}
        if resolution:
            resolver = resolution.get("resolver", "desconhecido")
            resolved_title = resolution.get("resolved_title")
            score = resolution.get("match_score")
            lines.append(f"   Resolvedor: {resolver}")
            if resolved_title:
                lines.append(f"   Título localizado: {resolved_title}")
            if score is not None:
                lines.append(f"   Score: {score}")

        if success:
            lines.append("   Status: DOWNLOAD CONCLUÍDO")
            if result.get("file"):
                lines.append(f"   Arquivo: {result['file']}")
            if result.get("source"):
                lines.append(f"   Fonte: {result['source']}")
        else:
            error = result.get("error") or {}
            lines.append("   Status: NÃO ENCONTRADO")
            lines.append(f"   Erro: {error.get('code', 'desconhecido')}")
            if error.get("message"):
                lines.append(f"   Detalhe: {error['message']}")

        lines.append("")

    lines.append("=" * 70)
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass

def main():
    global _format, _pretty, _stream, _request_id, _started_monotonic

    _started_monotonic = time.monotonic()
    _request_id = f"req_{uuid.uuid4().hex[:12]}"

    # Schema subcommand - handle before the main parser so we don't require a DOI.
    if len(sys.argv) >= 2 and sys.argv[1] == "schema":
        # Honor --pretty / --format if they follow.
        rest = sys.argv[2:]
        _pretty = "--pretty" in rest
        if "--format" in rest:
            i = rest.index("--format")
            if i + 1 < len(rest) and rest[i + 1] in ("json", "text"):
                _format = rest[i + 1]
            else:
                _format = _default_format()
        else:
            _format = _default_format()
        schema = build_schema()
        _emit(_envelope_ok(schema))
        sys.exit(EXIT_SUCCESS)

    ap = argparse.ArgumentParser(
        prog="paper-fetch",
        description="Fetch legal open-access PDFs by DOI via Unpaywall, Semantic Scholar, arXiv, Europe PMC, PMC, PubMed, and bioRxiv/medRxiv.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("doi", nargs="?", help="DOI to fetch (e.g. 10.1038/s41586-020-2649-2). Use '-' to read from stdin.")
    ap.add_argument("--title", metavar="TITLE", help="paper title; resolved to a DOI via Crossref before download. Mutually exclusive with positional DOI / --batch.")
    ap.add_argument("--batch", metavar="FILE", help="input file. Supports DOI-only files or mixed files: DOIs first, then article titles. Use '-' for DOI-only stdin.")
    ap.add_argument("--out", default="pdfs", metavar="DIR", help="output directory (default: pdfs)")
    ap.add_argument("--dry-run", action="store_true", help="resolve sources without downloading; preview the PDF URL and filename")
    ap.add_argument(
        "--format",
        choices=["json", "text"],
        default=None,
        dest="fmt",
        help="output format. json for agents, text for humans. Default: json when stdout is not a TTY, text otherwise.",
    )
    ap.add_argument("--pretty", action="store_true", help="pretty-print JSON output (2-space indent)")
    ap.add_argument("--stream", action="store_true", help="emit one NDJSON result per line on stdout as each DOI resolves (batch mode)")
    ap.add_argument("--overwrite", action="store_true", help="re-download even if the destination file already exists")
    ap.add_argument("--sources", metavar="SOURCES", default=None, help="comma-separated list of sources to query (e.g. 'unpaywall,openalex' or 'libgen')")
    ap.add_argument("--idempotency-key", metavar="KEY", default=None, help="safe-retry key; re-running with the same key replays the original envelope from <out>/.paper-fetch-idem/")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, metavar="SECONDS", help=f"HTTP timeout in seconds per request (default: {DEFAULT_TIMEOUT})")
    ap.add_argument("--version", action="version", version=f"paper-fetch {CLI_VERSION} (schema {SCHEMA_VERSION})")
    args = ap.parse_args()

    _format = args.fmt or _default_format()
    _pretty = args.pretty
    _stream = args.stream

    # Title-aware input mode:
    #   * no arguments -> DOI's.txt
    #   * --batch FILE -> same mixed DOI/title parser when trailing titles exist
    special_input: Path | None = None
    if len(sys.argv) == 1:
        default_doi_file = Path("DOI's.txt")
        if default_doi_file.is_file():
            special_input = default_doi_file
    elif args.batch and args.batch != "-":
        candidate = Path(args.batch)
        if candidate.is_file():
            _dois_probe, _titles_probe = _load_dois_and_titles_from_file(candidate)
            if _titles_probe:
                special_input = candidate

    if special_input is not None:
            # -----------------------------------------------------------
            # 1. Read DOI's.txt and separate DOIs from trailing titles.
            # -----------------------------------------------------------
            dois, article_titles = _load_dois_and_titles_from_file(special_input)

            if not dois:
                _emit(_envelope_err(
                    "validation_error",
                    "No DOIs found in default file DOI's.txt",
                ))
                sys.exit(EXIT_VALIDATION)

            print(
                f"{len(dois)} DOIs encontrados",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"{len(article_titles)} títulos encontrados no final do arquivo",
                file=sys.stderr,
                flush=True,
            )

            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)

            # Special mode is retained for the same behavior used by the
            # original automatic DOI processing path.
            os.environ["PAPER_FETCH_SPECIAL_MODE"] = "1"
            timeout_val = args.timeout

            # -----------------------------------------------------------
            # 2. Process EVERY DOI first.
            # -----------------------------------------------------------
            results: list[dict] = []

            print(
                "\n========== PROCESSANDO DOIs ==========",
                file=sys.stderr,
                flush=True,
            )

            for index, doi in enumerate(dois, start=1):
                print(
                    f"\n[DOI {index}/{len(dois)}] {doi}",
                    file=sys.stderr,
                    flush=True,
                )

                try:
                    result = fetch(
                        doi,
                        out_dir,
                        dry_run=False,
                        overwrite=args.overwrite,
                        timeout=timeout_val,
                    )
                except Exception as exc:
                    result = {
                        "doi": doi,
                        "success": False,
                        "error": {
                            "code": "internal_error",
                            "message": str(exc),
                        },
                    }
                    print(
                        f"ERRO processando DOI {doi}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )

                results.append(result)

            # -----------------------------------------------------------
            # 3. ONLY AFTER ALL DOIs ARE FINISHED, search the titles.
            # -----------------------------------------------------------
            title_results: list[dict] = []

            if article_titles:
                print(
                    "\n========== PROCESSANDO ARTIGOS PELO NOME ==========",
                    file=sys.stderr,
                    flush=True,
                )

                for index, title in enumerate(article_titles, start=1):
                    print(
                        f"\n[ARTIGO {index}/{len(article_titles)}] {title}",
                        file=sys.stderr,
                        flush=True,
                    )

                    try:
                        # Resolve title -> DOI using the existing Crossref /
                        # Semantic Scholar resolution chain.
                        print(
                            f"Pesquisando título: {title}",
                            file=sys.stderr,
                            flush=True,
                        )

                        resolved_doi, title_resolution = _resolve_title(
                            title,
                            timeout=timeout_val,
                        )

                        if not resolved_doi:
                            result = {
                                "title": title,
                                "searched_title": title,
                                "success": False,
                                "resolved_doi": None,
                                "title_resolution": title_resolution,
                                "search_method": "title",
                                "error": {
                                    "code": "title_resolve_failed",
                                    "message": (
                                        f"Nenhum DOI encontrado para o título: {title}"
                                    ),
                                },
                            }
                            title_results.append(result)

                            print(
                                f"Não foi possível encontrar DOI para: {title}",
                                file=sys.stderr,
                                flush=True,
                            )
                            continue

                        print(
                            f"DOI encontrado: {resolved_doi}",
                            file=sys.stderr,
                            flush=True,
                        )

                        # Use the exact same download pipeline as a normal DOI.
                        result = fetch(
                            resolved_doi,
                            out_dir,
                            dry_run=False,
                            overwrite=False,
                            timeout=timeout_val,
                        )

                        result["searched_title"] = title
                        result["title_resolution"] = title_resolution
                        result["resolved_doi"] = resolved_doi
                        result["search_method"] = "title"
                        title_results.append(result)

                        if result.get("success"):
                            print(
                                f"DOWNLOAD OK: {title}",
                                file=sys.stderr,
                                flush=True,
                            )
                        else:
                            print(
                                f"PDF não encontrado para: {title}",
                                file=sys.stderr,
                                flush=True,
                            )

                    except Exception as exc:
                        result = {
                            "title": title,
                            "searched_title": title,
                            "success": False,
                            "search_method": "title",
                            "error": {
                                "code": "internal_error",
                                "message": str(exc),
                            },
                        }
                        title_results.append(result)

                        print(
                            f"ERRO pesquisando título '{title}': {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
            else:
                print(
                    "\nNenhum título foi encontrado após o último DOI.",
                    file=sys.stderr,
                    flush=True,
                )

            # -----------------------------------------------------------
            # 4. Keep the original DOI report.
            # -----------------------------------------------------------
            report = generate_detailed_report(results, dois)
            Path("Relatório.txt").write_text(report, encoding="utf-8")

            # -----------------------------------------------------------
            # 5. Write a separate title-search report.
            # -----------------------------------------------------------
            _write_title_report(
                title_results,
                article_titles,
                Path("Relatório_artigos_por_nome.txt"),
            )

            # -----------------------------------------------------------
            # 6. Final summary.
            # -----------------------------------------------------------
            doi_successes = sum(1 for r in results if r.get("success"))
            title_successes = sum(1 for r in title_results if r.get("success"))
            title_failures = len(title_results) - title_successes

            print(
                "\n==========================================",
                file=sys.stderr,
                flush=True,
            )
            print(
                "PROCESSAMENTO FINALIZADO",
                file=sys.stderr,
                flush=True,
            )
            print(
                "==========================================",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"DOIs processados: {len(dois)}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"DOIs baixados com sucesso: {doi_successes}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"Artigos pesquisados por nome: {len(article_titles)}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"Artigos baixados com sucesso: {title_successes}",
                file=sys.stderr,
                flush=True,
            )
            print(
                f"Artigos não encontrados/baixados: {title_failures}",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Relatório dos DOIs: Relatório.txt",
                file=sys.stderr,
                flush=True,
            )
            print(
                "Relatório dos artigos por nome: Relatório_artigos_por_nome.txt",
                file=sys.stderr,
                flush=True,
            )

            os.environ.pop("PAPER_FETCH_NO_SCIHUB", None)
            os.environ.pop("PAPER_FETCH_SPECIAL_MODE", None)

            # Combine both result sets for the same exit-code behavior.
            sys.exit(_decide_exit(results + title_results))

    # One-time session header — lets agents detect schema drift on the very
    # first stderr line, before any per-DOI work or network I/O.
    _progress("session", cli_version=CLI_VERSION, schema_version=SCHEMA_VERSION)

    if not EMAIL:
        _progress("source_skip", source="unpaywall", reason="UNPAYWALL_EMAIL not set (top-level notice)")

    out_dir = Path(args.out)

    # Title resolution — runs before DOI loading so the rest of the pipeline
    # treats the resolved DOI as if it had been passed directly.
    title_resolution: dict | None = None
    if args.title:
        # Reject simultaneous title + DOI / --batch up front rather than later.
        if args.doi or args.batch:
            _emit(_envelope_err(
                "validation_error",
                "--title cannot be combined with a positional DOI or --batch.",
            ))
            sys.exit(EXIT_VALIDATION)
        resolved_doi, title_resolution = _resolve_title(args.title, timeout=args.timeout)
        if not resolved_doi:
            direct = fetch_title_direct(
                args.title,
                out_dir,
                timeout=args.timeout,
                overwrite=args.overwrite,
                sources=[s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None,
            )
            direct["title_resolution"] = {
                **(title_resolution or {}),
                "direct_recovery": direct.get("title_resolution"),
            }
            _emit(_envelope_ok(
                {"results": [direct], "summary": {
                    "total": 1,
                    "succeeded": 1 if direct.get("success") else 0,
                    "failed": 0 if direct.get("success") else 1,
                }},
                ok=True if direct.get("success") else "partial",
            ))
            sys.exit(EXIT_SUCCESS if direct.get("success") else EXIT_UNRESOLVED)
        # Inject the resolved DOI as the positional argument so downstream
        # logic (DOI validation, fetch loop, idempotency replay) is identical.
        # Clear args.title so the mutual-exclusion guard in _load_dois_from_args
        # doesn't trip on the (now consumed) title.
        args.doi = resolved_doi
        args.title = None

    loaded = _load_dois_from_args(args)
    if isinstance(loaded, dict):
        _emit(loaded)
        sys.exit(EXIT_VALIDATION)
    dois: list[str] = loaded

    # If a positional DOI argument was provided but it's actually an existing file,
    # treat it as a batch file containing DOIs (one per line)
    if args.doi is not None and not args.title and not args.batch:
        doi_as_file = Path(args.doi)
        if doi_as_file.is_file():
            try:
                with open(doi_as_file, 'r', encoding='utf-8') as f:
                    dois_from_file = [line.strip() for line in f if line.strip()]
                if dois_from_file:
                    dois = dois_from_file
            except Exception:
                # If we can't read the file, fall back to the original dois list
                pass

    # Idempotency replay — before any network I/O.
    if args.idempotency_key:
        cached = _idem_load(out_dir, args.idempotency_key)
        if cached is not None:
            # Re-stamp meta so the replayed envelope still reports current latency / request id.
            cached_meta = cached.get("meta", {}) or {}
            cached_meta.update({
                "request_id": _request_id,
                "latency_ms": _now_ms(),
                "replayed_from_idempotency_key": args.idempotency_key,
            })
            cached["meta"] = cached_meta
            _emit(cached)
            # Exit code mirrors the cached envelope's outcome.
            if cached.get("ok") is True:
                sys.exit(EXIT_SUCCESS)
            if cached.get("ok") == "partial":
                sys.exit(_decide_exit(cached.get("data", {}).get("results", [])))
            sys.exit(EXIT_VALIDATION if cached.get("error", {}).get("code") == "validation_error" else EXIT_UNRESOLVED)

    sources_list = [s.strip() for s in args.sources.split(",") if s.strip()] if args.sources else None
    results: list[dict] = []
    for d in dois:
        r = fetch(
            d,
            out_dir,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            timeout=args.timeout,
            sources=sources_list,
        )
        results.append(r)
        if _stream and _format == "json":
            _emit_ndjson({"ok": bool(r.get("success")), "data": r, "meta": _meta()})

    succeeded = sum(1 for r in results if r.get("success"))
    total = len(results)
    failed = total - succeeded

    if succeeded == total:
        ok_flag: bool | str = True
    elif succeeded == 0:
        ok_flag = False
    else:
        ok_flag = "partial"

    data = {
        "results": results,
        "summary": {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
        },
        "next": _next_hints(results, args),
    }

    sources_tried_union = sorted({s for r in results for s in r.get("sources_tried", [])})
    meta_extra = {"sources_tried": sources_tried_union}
    if not EMAIL:
        meta_extra["unpaywall_skipped"] = True
    if title_resolution is not None:
        meta_extra["title_resolution"] = title_resolution

    if ok_flag is False:
        # Total failure of a single-DOI call — downgrade to an error envelope
        # when the single result has an error with a code, so agents see
        # {ok:false, error:{...}} for the simple case.
        if total == 1 and results[0].get("error"):
            err = results[0]["error"]
            envelope = _envelope_err(
                err.get("code", "internal_error"),
                err.get("message", "failed"),
                retryable=err.get("retryable", False),
                **{k: v for k, v in err.items() if k not in ("code", "message", "retryable")},
                doi=results[0]["doi"],
                sources_tried=results[0].get("sources_tried", []),
            )
            envelope["meta"].update(meta_extra)
        else:
            envelope = _envelope_ok(data, ok=False, meta_extra=meta_extra)
    else:
        envelope = _envelope_ok(data, ok=ok_flag, meta_extra=meta_extra)

    # Stream mode already emitted per-item lines; final envelope still goes out as a summary.
    if _stream and _format == "json":
        print(_dump_json({"summary": data["summary"], "meta": envelope["meta"], "next": data["next"], "ok": ok_flag}), flush=True)
    else:
        _emit(envelope)

    # Store idempotency sidecar on completion (even for partial — replay returns same shape).
    if args.idempotency_key:
        _idem_store(out_dir, args.idempotency_key, envelope)

    sys.exit(_decide_exit(results))

# ===========================================================================
# TITLE RECOVERY V4 — PATTERN-DRIVEN + DIRECT TITLE DOWNLOAD (ADDITIVE)
# ===========================================================================
# The original implementation is preserved above. This layer fixes a key
# limitation of title-only searches: resolving a title to a DOI is not the same
# as finding a PDF. We therefore:
#   1) generate multiple deterministic title variants;
#   2) query independent metadata indexes concurrently;
#   3) keep ALL good candidates instead of only the first result;
#   4) rank candidates using title + year + author + journal signals;
#   5) optionally consult subscription APIs when keys are configured;
#   6) search NASA NTRS, which is particularly valuable for the project's
#      microgravity / space-biology corpus;
#   7) when DOI resolution succeeds but DOI download fails, directly search
#      repositories by title and try validated PDF candidates.

from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

TITLE_RECOVERY_VERSION = "4.0.0"
TITLE_RECOVERY_SOURCE_TIMEOUT = max(3, int(os.environ.get("PAPER_FETCH_TITLE_SOURCE_TIMEOUT", "6")))
TITLE_RECOVERY_WORKERS = max(2, int(os.environ.get("PAPER_FETCH_TITLE_SOURCE_WORKERS", "6")))
TITLE_VARIANT_COUNT = max(2, int(os.environ.get("PAPER_FETCH_TITLE_VARIANTS", "6")))
TITLE_STRONG_THRESHOLD = float(os.environ.get("PAPER_FETCH_TITLE_STRONG_THRESHOLD", "0.90"))
# More permissive than the previous resolver, but still bibliographically
# conservative. This allows old/format-shifted titles to resolve when the
# candidate is demonstrably the same work or strongly corroborated.
TITLE_ACCEPT_THRESHOLD = float(os.environ.get("PAPER_FETCH_TITLE_THRESHOLD", "0.75"))
TITLE_DIRECT_THRESHOLD = float(os.environ.get("PAPER_FETCH_TITLE_DIRECT_THRESHOLD", "0.75"))
TITLE_AMBIGUOUS_MARGIN = float(os.environ.get("PAPER_FETCH_TITLE_AMBIGUOUS_MARGIN", "0.02"))

# Optional paid/institutional APIs. They are skipped when the key is absent.
WOS_API_KEY = os.environ.get("WOS_API_KEY", "").strip()
SCOPUS_API_KEY = os.environ.get("SCOPUS_API_KEY", "").strip()
SPRINGER_API_KEY = os.environ.get("SPRINGER_API_KEY", "").strip()
SPRINGER_OA_API_KEY = os.environ.get("SPRINGER_OA_API_KEY", "").strip()
IEEE_API_KEY = os.environ.get("IEEE_API_KEY", "").strip()
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "").strip() or EMAIL
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "").strip()


def _title_variants(title: str) -> list[str]:
    """Create deterministic search variants without changing the user's source title."""
    raw = re.sub(r"\s+", " ", (title or "").strip())
    raw = re.sub(r"^[\[\(\{\"'‘“]+|[\]\)\}\"'’”\.]+$", "", raw).strip()
    if not raw:
        return []

    variants: list[str] = []
    def add(v: str):
        v = re.sub(r"\s+", " ", v).strip(" \t\r\n.,;:")
        if v and len(v) >= _MIN_TITLE_LEN and v not in variants:
            variants.append(v)

    add(raw)

    # Remove subtitles after colon / em dash only as a fallback, never as the primary query.
    for sep in (":", " — ", " – ", " - "):
        if sep in raw:
            add(raw.split(sep, 1)[0])

    # Parenthetical stripping often fixes older titles containing issue / model notes.
    no_paren = re.sub(r"\([^()]{1,100}\)", " ", raw)
    add(no_paren)

    # Normalize common scholarly punctuation variants.
    add(raw.replace("&", "and"))
    add(raw.replace(" and ", " & "))

    # De-duplicate whitespace/punctuation noise.
    add(re.sub(r"[\[\]{}<>]", " ", raw))

    return variants[:TITLE_VARIANT_COUNT]


def _candidate_key(rec: dict) -> str:
    doi = normalize_doi(str(rec.get("doi") or ""))
    if doi:
        return f"doi:{doi}"
    return f"title:{_norm_match_text(str(rec.get('title') or ''))}"


def _year_similarity(requested_year, candidate_year) -> float:
    try:
        ry = int(requested_year) if requested_year else None
        cy = int(candidate_year) if candidate_year else None
    except Exception:
        return 0.0
    if ry is None or cy is None:
        return 0.0
    if ry == cy:
        return 1.0
    if abs(ry - cy) == 1:
        return 0.5
    return 0.0


def _author_similarity(requested_author: str | None, candidate_author: str | None) -> float:
    if not requested_author or not candidate_author:
        return 0.0
    ra = _norm_match_text(requested_author).split()
    ca = _norm_match_text(candidate_author).split()
    if not ra or not ca:
        return 0.0
    return 1.0 if ra[-1] == ca[-1] else 0.0


def _rank_title_recovery_candidates(requested_title: str, records: list[dict]) -> list[dict]:
    """Rank title candidates using title coverage + source corroboration.

    A single weak API hit is not enough.  Independent sources confirming the
    same DOI/title receive a corroboration bonus.  This is what lets legacy,
    punctuation-heavy and subtitle-shifted titles resolve without reopening
    the old "first related result" false-positive problem.
    """
    grouped: dict[str, dict] = {}
    req_tokens = set(_norm_match_text(requested_title).split())

    for raw in records:
        ct = str(raw.get("title") or "").strip()
        if not ct:
            continue

        score = _title_similarity(requested_title, ct)
        cand_tokens = set(_norm_match_text(ct).split())
        coverage = (len(req_tokens & cand_tokens) / len(req_tokens)) if req_tokens else 0.0
        year_bonus = _year_similarity(raw.get("requested_year"), raw.get("year"))
        author_bonus = _author_similarity(raw.get("requested_author"), raw.get("author"))

        c = dict(raw)
        c["title_similarity"] = round(score, 6)
        c["title_coverage"] = round(coverage, 6)
        c["year_similarity"] = round(year_bonus, 6)
        c["author_similarity"] = round(author_bonus, 6)

        key = _candidate_key(c)
        existing = grouped.get(key)
        if existing is None:
            c["_resolvers"] = {str(c.get("resolver") or "").lower()} if c.get("resolver") else set()
            c["_source_count"] = 1
            grouped[key] = c
        else:
            resolver = str(c.get("resolver") or "").lower()
            if resolver:
                existing.setdefault("_resolvers", set()).add(resolver)
            existing["_source_count"] = len(existing.get("_resolvers", set()))
            # Keep the best metadata record for the candidate.
            current_base = (
                0.50 * float(existing.get("title_similarity") or 0.0)
                + 0.30 * float(existing.get("title_coverage") or 0.0)
                + 0.10 * float(existing.get("year_similarity") or 0.0)
                + 0.10 * float(existing.get("author_similarity") or 0.0)
            )
            candidate_base = (
                0.50 * score
                + 0.30 * coverage
                + 0.10 * year_bonus
                + 0.10 * author_bonus
            )
            if candidate_base > current_base:
                preserved_resolvers = set(existing.get("_resolvers", set()))
                preserved_resolvers.update(existing.get("resolver") and [str(existing["resolver"]).lower()] or [])
                preserved_resolvers.update(c.get("resolver") and [str(c["resolver"]).lower()] or [])
                c["_resolvers"] = preserved_resolvers
                c["_source_count"] = len(preserved_resolvers)
                grouped[key] = c
        # refresh source count after each insertion
        grouped[key]["_source_count"] = len(grouped[key].get("_resolvers", set()))

    ranked: list[dict] = []
    for c in grouped.values():
        source_count = int(c.get("_source_count") or 1)

        # Base score emphasizes identity of the requested title.
        total = (
            0.78 * float(c.get("title_similarity") or 0.0)
            + 0.12 * float(c.get("title_coverage") or 0.0)
            + 0.06 * float(c.get("year_similarity") or 0.0)
            + 0.04 * float(c.get("author_similarity") or 0.0)
        )

        # Independent-source corroboration is valuable, but modest so it
        # cannot rescue a clearly wrong title.
        corroboration_bonus = min(0.08, 0.02 * max(0, source_count - 1))
        total = min(1.0, total + corroboration_bonus)

        c["source_count"] = source_count
        c["source_corrob_bonus"] = round(corroboration_bonus, 6)
        c["rank_score"] = round(total, 6)
        c["resolvers"] = sorted(
            r for r in c.get("_resolvers", set())
            if r
        )
        c.pop("_resolvers", None)
        c.pop("_source_count", None)
        ranked.append(c)

    ranked.sort(key=lambda x: x.get("rank_score", 0.0), reverse=True)
    return ranked[:30]


def _ncb_query_url(query: str) -> str:
    params = {
        "db": "pubmed",
        "term": f"{query}[Title]",
        "retmode": "json",
        "retmax": "10",
        "tool": "paper-fetch",
        "email": NCBI_EMAIL,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)


def _try_pubmed_title_records(title: str, *, timeout: int) -> list[dict]:
    try:
        data = _get_json(_ncb_query_url(title), timeout=timeout)
        ids = ((data or {}).get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return []
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(ids[:10]),
            "retmode": "xml",
            "tool": "paper-fetch",
            "email": NCBI_EMAIL,
        }
        if NCBI_API_KEY:
            fetch_params["api_key"] = NCBI_API_KEY
        xml_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(fetch_params)
        xml_text = _get_text(xml_url, timeout=timeout)
        root = ET.fromstring(xml_text)
        out: list[dict] = []
        for article in root.findall(".//PubmedArticle"):
            title_node = article.find(".//ArticleTitle")
            title_value = "".join(title_node.itertext()).strip() if title_node is not None else ""
            doi = ""
            for aid in article.findall(".//ArticleId"):
                if aid.attrib.get("IdType") == "doi" and aid.text:
                    doi = aid.text.strip()
                    break
            author = None
            first = article.find(".//Author")
            if first is not None:
                author = first.findtext("LastName") or first.findtext("CollectiveName")
            year = article.findtext(".//PubDate/Year") or article.findtext(".//PubDate/MedlineDate")
            out.append({
                "resolver": "pubmed",
                "doi": normalize_doi(doi),
                "title": title_value,
                "year": year,
                "author": author,
                "pmid": article.findtext(".//PMID"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="pubmed", reason=str(exc))
        return []


def _try_ntrs_records(query: str, *, timeout: int) -> list[dict]:
    """NASA NTRS discovery, particularly useful for legacy space-biology papers."""
    base = os.environ.get("NTRS_API_BASE", "https://ntrs.nasa.gov/api").rstrip("/")
    params_options = [
        {"q": query, "page": 1, "pageSize": 10},
        {"query": query, "page": 1, "pageSize": 10},
    ]
    for params in params_options:
        try:
            url = base + "/citations/search?" + urllib.parse.urlencode(params)
            data = _get_json(url, timeout=timeout)
            raw_items = []
            if isinstance(data, dict):
                for key in ("results", "citations", "items", "data"):
                    value = data.get(key)
                    if isinstance(value, list):
                        raw_items = value
                        break
                    if isinstance(value, dict):
                        for key2 in ("results", "citations", "items", "data"):
                            if isinstance(value.get(key2), list):
                                raw_items = value[key2]
                                break
                        if raw_items:
                            break
            out: list[dict] = []
            for item in raw_items[:10]:
                if not isinstance(item, dict):
                    continue
                title_value = item.get("title") or item.get("documentTitle") or item.get("name")
                doi = item.get("doi") or item.get("DOI")
                citation_id = item.get("id") or item.get("citationId")
                author = None
                authors = item.get("authors") or item.get("author")
                if isinstance(authors, list) and authors:
                    a0 = authors[0]
                    author = a0.get("name") if isinstance(a0, dict) else str(a0)
                out.append({
                    "resolver": "ntrs",
                    "doi": normalize_doi(str(doi or "")),
                    "title": title_value,
                    "year": item.get("publicationDate") or item.get("published"),
                    "author": author,
                    "ntrs_id": citation_id,
                    "raw": item,
                })
            if out:
                return out
        except Exception:
            continue
    return []


def _try_wos_title_records(title: str, *, timeout: int) -> list[dict]:
    if not WOS_API_KEY:
        return []
    try:
        url = "https://api.clarivate.com/apis/wos-starter/v1/documents?" + urllib.parse.urlencode({
            "q": f'TI=("{title}")',
            "limit": 10,
        })
        data = _get_json_with_headers(url, timeout=timeout, headers={"X-ApiKey": WOS_API_KEY})
        docs = (data or {}).get("hits") or (data or {}).get("documents") or []
        out = []
        for doc in docs:
            out.append({
                "resolver": "web_of_science",
                "doi": normalize_doi(str(doc.get("doi") or ((doc.get("identifiers") or {}).get("doi") if isinstance(doc.get("identifiers"), dict) else ""))),
                "title": doc.get("title") or doc.get("displayName"),
                "year": doc.get("publicationYear") or doc.get("year"),
                "author": doc.get("firstAuthor") or doc.get("author"),
                "journal": doc.get("sourceTitle") or doc.get("journal"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="web_of_science", reason=str(exc))
        return []


def _try_scopus_title_records(title: str, *, timeout: int) -> list[dict]:
    if not SCOPUS_API_KEY:
        return []
    try:
        params = {
            "query": f'TITLE("{title}")',
            "count": 10,
            "httpAccept": "application/json",
        }
        url = "https://api.elsevier.com/content/search/scopus?" + urllib.parse.urlencode(params)
        data = _get_json_with_headers(url, timeout=timeout, headers={"X-ELS-APIKey": SCOPUS_API_KEY})
        entries = ((data or {}).get("search-results") or {}).get("entry") or []
        out = []
        for e in entries:
            out.append({
                "resolver": "scopus",
                "doi": normalize_doi(str(e.get("prism:doi") or "")),
                "title": e.get("dc:title"),
                "year": e.get("prism:coverDate") or e.get("prism:publicationDate"),
                "author": e.get("dc:creator"),
                "journal": e.get("prism:publicationName"),
                "eid": e.get("eid"),
                "pii": e.get("pii"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="scopus", reason=str(exc))
        return []


def _try_springer_title_records(title: str, *, timeout: int) -> list[dict]:
    key = SPRINGER_OA_API_KEY or SPRINGER_API_KEY
    if not key:
        return []
    try:
        params = {
            "api_key": key,
            "q": f'title:"{title}"',
            "s": 1,
            "p": 10,
            "format": "json",
        }
        url = "https://api.springernature.com/openaccess/json?" + urllib.parse.urlencode(params)
        data = _get_json(url, timeout=timeout)
        records = (data or {}).get("records") or []
        if not records:
            params["q"] = f'title:"{title}"'
            url = "https://api.springernature.com/meta/v2/json?" + urllib.parse.urlencode(params)
            data = _get_json(url, timeout=timeout)
            records = (data or {}).get("records") or []
        out = []
        for r in records:
            out.append({
                "resolver": "springer",
                "doi": normalize_doi(str(r.get("doi") or r.get("identifier") or "")),
                "title": r.get("title"),
                "year": r.get("publicationDate"),
                "author": ((r.get("creators") or [{}])[0].get("creator") if isinstance(r.get("creators"), list) and r.get("creators") else None),
                "journal": r.get("journalTitle"),
                "url": r.get("url"),
                "open_access": True,
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="springer", reason=str(exc))
        return []


def _try_ieee_title_records(title: str, *, timeout: int) -> list[dict]:
    if not IEEE_API_KEY:
        return []
    try:
        params = {
            "article_title": title,
            "apikey": IEEE_API_KEY,
            "start_record": 1,
            "max_records": 10,
        }
        url = "https://ieeexploreapi.ieee.org/api/v1/search/articles?" + urllib.parse.urlencode(params)
        data = _get_json(url, timeout=timeout)
        records = (data or {}).get("articles") or []
        out = []
        for r in records:
            out.append({
                "resolver": "ieee",
                "doi": normalize_doi(str(r.get("doi") or "")),
                "title": r.get("title"),
                "year": r.get("publication_year"),
                "author": ((r.get("authors") or [{}])[0].get("full_name") if isinstance(r.get("authors"), list) and r.get("authors") else None),
                "article_number": r.get("article_number"),
                "access_type": r.get("access_type"),
            })
        return out
    except Exception as exc:
        _progress("title_resolver_miss", resolver="ieee", reason=str(exc))
        return []


def _search_title_source(source: str, variant: str, timeout: int) -> list[dict]:
    """Single-source title search adapter used by the concurrent resolver."""
    try:
        if source == "crossref":
            _, _, rows = try_crossref_title(variant, timeout=timeout)
            return [{"resolver": "crossref", **r} for r in rows]
        if source == "semantic_scholar":
            doi, meta = try_semantic_scholar_match(variant, timeout=timeout)
            return [{"resolver": "semantic_scholar", **meta}] if doi else []
        if source == "openalex":
            params = {
                "search": variant,
                "per-page": 10,
                "select": "id,doi,title,publication_year,authorships,primary_location",
            }
            if os.environ.get("OPENALEX_MAILTO") or EMAIL:
                params["mailto"] = os.environ.get("OPENALEX_MAILTO") or EMAIL
            data = _get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params), timeout=timeout)
            out = []
            for item in (data or {}).get("results") or []:
                authorships = item.get("authorships") or []
                out.append({
                    "resolver": "openalex",
                    "doi": normalize_doi(str(item.get("doi") or "").replace("https://doi.org/", "").replace("http://doi.org/", "")),
                    "title": item.get("title"),
                    "year": item.get("publication_year"),
                    "author": ((authorships[0].get("author") or {}).get("display_name") if authorships else None),
                    "journal": ((item.get("primary_location") or {}).get("source") or {}).get("display_name"),
                })
            return out
        if source == "europe_pmc":
            params = {"query": f'TITLE:"{variant}"', "format": "json", "pageSize": 10}
            data = _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(params), timeout=timeout)
            return [{
                "resolver": "europe_pmc",
                "doi": normalize_doi(str(x.get("doi") or "")),
                "title": x.get("title"),
                "year": x.get("pubYear"),
                "author": x.get("authorString"),
                "journal": x.get("journalTitle"),
                "pmid": x.get("pmid"),
                "pmcid": x.get("pmcid"),
            } for x in ((data or {}).get("resultList", {}).get("result") or [])]
        if source == "pubmed":
            return _try_pubmed_title_records(variant, timeout=timeout)
        if source == "openaire":
            _, meta, raw = try_openaire(title=variant, timeout=timeout)
            rows = []
            for item in raw:
                rows.append({"resolver": "openaire", "doi": normalize_doi(str(item.get("doi") or meta.get("doi") or "")), "title": item.get("title") or meta.get("title"), "year": item.get("publicationDate") or meta.get("year"), "author": meta.get("author")})
            return rows or ([{"resolver": "openaire", **meta}] if meta.get("title") else [])
        if source == "hal":
            _, meta, raw = try_hal(title=variant, timeout=timeout)
            rows = []
            for item in raw:
                rows.append({"resolver": "hal", "doi": normalize_doi(str(item.get("doi") or meta.get("doi") or "")), "title": item.get("title") or meta.get("title"), "year": item.get("year") or meta.get("year"), "author": meta.get("author")})
            return rows or ([{"resolver": "hal", **meta}] if meta.get("title") else [])
        if source == "zenodo":
            _, meta, raw = try_zenodo(title=variant, timeout=timeout)
            rows = []
            for item in raw:
                im = item.get("metadata") if isinstance(item, dict) else {}
                rows.append({"resolver": "zenodo", "doi": normalize_doi(str((im or {}).get("doi") or meta.get("doi") or "")), "title": (im or {}).get("title") or meta.get("title"), "year": (im or {}).get("publication_date") or meta.get("year"), "author": meta.get("author")})
            return rows or ([{"resolver": "zenodo", **meta}] if meta.get("title") else [])
        if source == "datacite":
            _, meta, raw = try_datacite(title=variant, timeout=timeout)
            rows = []
            for item in raw:
                attrs = item.get("attributes") if isinstance(item, dict) else {}
                titles = (attrs or {}).get("titles") or []
                rows.append({"resolver": "datacite", "doi": normalize_doi(str((attrs or {}).get("doi") or "")), "title": (titles[0].get("title") if titles and isinstance(titles[0], dict) else None) or meta.get("title"), "year": (attrs or {}).get("publicationYear") or meta.get("year"), "author": meta.get("author")})
            return rows or ([{"resolver": "datacite", **meta}] if meta.get("title") else [])
        if source == "doaj":
            _, meta, raw = try_doaj(title=variant, timeout=timeout)
            rows = []
            for item in raw:
                bib = (item.get("bibjson") or {}) if isinstance(item, dict) else {}
                rows.append({"resolver": "doaj", "doi": normalize_doi(str((bib.get("identifier") or [{}])[0].get("id") if isinstance(bib.get("identifier"), list) and bib.get("identifier") else "")), "title": bib.get("title") or meta.get("title"), "year": bib.get("year") or meta.get("year"), "author": ((bib.get("author") or [{}])[0].get("name") if bib.get("author") else meta.get("author"))})
            return rows or ([{"resolver": "doaj", **meta}] if meta.get("title") else [])
        if source == "ntrs":
            return _try_ntrs_records(variant, timeout=timeout)
        if source == "wos":
            return _try_wos_title_records(variant, timeout=timeout)
        if source == "scopus":
            return _try_scopus_title_records(variant, timeout=timeout)
        if source == "springer":
            return _try_springer_title_records(variant, timeout=timeout)
        if source == "ieee":
            return _try_ieee_title_records(variant, timeout=timeout)
    except Exception as exc:
        _progress("title_resolver_miss", resolver=source, reason=str(exc))
    return []


def _resolve_title_v4(title: str, *, timeout: int) -> tuple[str | None, dict]:
    """Resolve title via multi-variant, multi-index, evidence-based ranking."""
    variants = _title_variants(title)
    if not variants:
        return None, {"query": title, "resolver": "v4", "low_confidence": True, "low_confidence_reason": "invalid_title"}

    sources = [
        "crossref", "semantic_scholar", "openalex", "europe_pmc", "pubmed",
        "openaire", "hal", "zenodo", "datacite", "doaj", "ntrs",
    ]
    if WOS_API_KEY:
        sources.append("wos")
    if SCOPUS_API_KEY:
        sources.append("scopus")
    if SPRINGER_API_KEY or SPRINGER_OA_API_KEY:
        sources.append("springer")
    if IEEE_API_KEY:
        sources.append("ieee")

    records: list[dict] = []
    
    # 1. FAST PATH: Consult the best sources first
    fast_sources = ["crossref", "semantic_scholar", "openalex"]
    remaining_sources = [s for s in sources if s not in fast_sources]
    
    def check_strong_hit(current_records):
        ranked = _rank_title_recovery_candidates(title, current_records)
        if ranked and float(ranked[0].get("rank_score", 0.0)) >= TITLE_STRONG_THRESHOLD:
            return ranked
        return None

    # First pass: Fast sources
    with ThreadPoolExecutor(max_workers=len(fast_sources)) as pool:
        jobs = [pool.submit(_search_title_source, s, variants[0], min(timeout, TITLE_RECOVERY_SOURCE_TIMEOUT)) for s in fast_sources]
        for fut in as_completed(jobs):
            try:
                records.extend(fut.result() or [])
                # Early stopping check
                strong_hit = check_strong_hit(records)
                if strong_hit:
                    # Cancel remaining if supported
                    for j in jobs: j.cancel()
                    break
            except Exception:
                pass
                
    ranked = check_strong_hit(records)
    
    # 2. PARALLEL DISCOVERY if FAST PATH failed
    if not ranked and remaining_sources:
        with ThreadPoolExecutor(max_workers=min(TITLE_RECOVERY_WORKERS, len(remaining_sources))) as pool:
            jobs = [pool.submit(_search_title_source, s, variants[0], min(timeout, TITLE_RECOVERY_SOURCE_TIMEOUT)) for s in remaining_sources]
            for fut in as_completed(jobs):
                try:
                    records.extend(fut.result() or [])
                    strong_hit = check_strong_hit(records)
                    if strong_hit:
                        for j in jobs: j.cancel()
                        break
                except Exception:
                    pass
                    
    ranked = _rank_title_recovery_candidates(title, records)

    # 3. If no strong candidate exists, use title variants progressively.
    if not ranked or float(ranked[0].get("rank_score") or 0.0) < TITLE_STRONG_THRESHOLD:
        for variant in variants[1:]:
            if ranked and float(ranked[0].get("rank_score") or 0.0) >= TITLE_STRONG_THRESHOLD:
                break
            with ThreadPoolExecutor(max_workers=min(TITLE_RECOVERY_WORKERS, len(sources))) as pool:
                jobs = [pool.submit(_search_title_source, source, variant, min(timeout, TITLE_RECOVERY_SOURCE_TIMEOUT)) for source in sources]
                for fut in as_completed(jobs):
                    try:
                        records.extend(fut.result() or [])
                        strong_hit = check_strong_hit(records)
                        if strong_hit:
                            for j in jobs: j.cancel()
                            break
                    except Exception:
                        pass
            ranked = _rank_title_recovery_candidates(title, records)

    tried = sorted({r.get("resolver") for r in records if r.get("resolver")})
    resolution = {
        "query": title,
        "resolver": "v4_multi_variant",
        "resolver_version": TITLE_RECOVERY_VERSION,
        "variants": variants,
        "resolvers_tried": tried,
        "candidates": ranked[:15],
        "low_confidence": True,
    }
    if not ranked:
        resolution["low_confidence_reason"] = "no_candidate"
        _progress("title_resolve_miss", query=title, resolver="v4_multi_variant")
        return None, resolution

    best = ranked[0]
    score = float(best.get("rank_score") or 0.0)
    second = float(ranked[1].get("rank_score") or 0.0) if len(ranked) > 1 else 0.0
    gap = score - second
    resolution.update({
        "resolved_doi": best.get("doi"),
        "resolved_title": best.get("title"),
        "match_score": score,
        "title_similarity": best.get("title_similarity"),
        "candidate_gap": round(gap, 6),
        "resolver_selected": best.get("resolver"),
    })

    direct_urls = _deep_find_pdf_urls(best.get("raw") or best)

    if not best.get("doi") and score < TITLE_DIRECT_THRESHOLD:
        resolution["low_confidence_reason"] = "top_candidate_without_doi"
        return None, resolution

    if score < TITLE_ACCEPT_THRESHOLD:
        resolution["low_confidence_reason"] = "title_similarity_below_threshold"
        return None, resolution

    if gap < TITLE_AMBIGUOUS_MARGIN and score < TITLE_STRONG_THRESHOLD:
        # Allow an ambiguous candidate only when at least two independent
        # sources corroborate it. Otherwise keep the old conservative behavior.
        if int(best.get("source_count") or 1) < 2:
            resolution["low_confidence_reason"] = "ambiguous_top_candidates"
            return None, resolution

    resolution["direct_pdf_candidates"] = direct_urls[:12]
    resolution["low_confidence"] = False
    _progress(
        "title_resolve_hit",
        query=title,
        resolver=best.get("resolver"),
        doi=best.get("doi"),
        title=best.get("title"),
        score=score,
    )
    return (
        normalize_doi(str(best["doi"])) if best.get("doi") else None,
        resolution,
    )


# Install the V4 resolver last so existing callers transparently use it.
_resolve_title_v4_previous = _resolve_title
_resolve_title = _resolve_title_v4


def _ntrs_pdf_urls_for_record(record: dict, timeout: int) -> list[str]:
    """Expand an NTRS citation record into downloadable PDF URLs."""
    urls = _deep_find_pdf_urls(record)
    citation_id = record.get("ntrs_id") or record.get("id")
    if not citation_id:
        return list(dict.fromkeys(urls))
    base = os.environ.get("NTRS_API_BASE", "https://ntrs.nasa.gov/api").rstrip("/")
    try:
        data = _get_json(f"{base}/citations/{urllib.parse.quote(str(citation_id), safe='')}/downloads", timeout=timeout)
        urls.extend(_deep_find_pdf_urls(data))
        if isinstance(data, dict):
            for item in (data.get("downloads") or data.get("data") or []):
                if isinstance(item, dict):
                    fname = item.get("filename") or item.get("name")
                    if fname:
                        urls.append(f"{base}/citations/{urllib.parse.quote(str(citation_id), safe='')}/downloads/{urllib.parse.quote(str(fname), safe='')}")
    except Exception:
        pass
    return list(dict.fromkeys(urls))


def fetch_title_direct(
    title: str,
    out_dir: Path,
    *,
    timeout: int = 15,
    overwrite: bool = False,
    sources: list[str] | None = None,
) -> dict:
    """Resolve and download a title directly, even when no DOI is available.

    This is the critical fallback for title-only inputs.  It searches every
    configured title source, keeps candidates with strong bibliographic
    similarity, extracts any direct PDF/landing URLs available in the source
    records, and only then falls back to DOI-level fetching.

    The function intentionally treats "similar title" as a bibliographic
    variation of the requested work, not merely a paper on the same topic.
    """
    variants = _title_variants(title)
    if not variants:
        return {
            "searched_title": title,
            "success": False,
            "error": {"code": "title_resolve_failed", "message": "Invalid title"},
        }

    allowed = {x.strip().lower() for x in sources} if sources else None
    source_names = [
        "crossref",
        "semantic_scholar",
        "openalex",
        "europe_pmc",
        "pubmed",
        "core",
        "openaire",
        "hal",
        "zenodo",
        "datacite",
        "doaj",
        "ntrs",
        "arxiv",
        "biorxiv",
        "medrxiv",
        "springer",
        "wos",
        "scopus",
        "ieee",
    ]
    if allowed is not None:
        source_names = [s for s in source_names if s in allowed]

    records: list[dict] = []

    # Search all title variants, not just the raw title. This is deliberately
    # additive: older indexes frequently omit subtitles or normalize punctuation.
    for variant_index, variant in enumerate(variants, start=1):
        try:
            with ThreadPoolExecutor(
                max_workers=min(TITLE_RECOVERY_WORKERS, max(1, len(source_names)))
            ) as pool:
                futs = [
                    pool.submit(
                        _search_title_source,
                        source,
                        variant,
                        min(timeout, TITLE_RECOVERY_SOURCE_TIMEOUT),
                    )
                    for source in source_names
                ]
                for fut in as_completed(futs):
                    try:
                        records.extend(fut.result() or [])
                    except Exception as exc:
                        _progress(
                            "title_resolver_miss",
                            resolver="direct",
                            variant=variant_index,
                            reason=str(exc),
                        )
        except Exception as exc:
            _progress("title_direct_search_error", variant=variant_index, error=str(exc))

        ranked = _rank_title_recovery_candidates(title, records)
        if ranked and float(ranked[0].get("rank_score") or 0.0) >= TITLE_STRONG_THRESHOLD:
            break

    ranked = _rank_title_recovery_candidates(title, records)
    tried = sorted({r.get("resolver") for r in records if r.get("resolver")})

    if not ranked:
        return {
            "searched_title": title,
            "success": False,
            "search_method": "title_direct_all_sources",
            "title_resolution": {
                "resolver": "title_direct_all_sources",
                "resolvers_tried": tried,
                "low_confidence": True,
                "low_confidence_reason": "no_candidate",
            },
            "error": {
                "code": "title_not_found",
                "message": f"Nenhum candidato bibliográfico encontrado para: {title}",
            },
        }

    # Build a ranked list of PDF candidates. A candidate can have:
    # - a DOI that can be passed through the mature DOI fetch chain;
    # - one or more explicit PDF URLs;
    # - a landing/download URL that may resolve to a PDF.
    pdf_candidates: list[tuple[str, str, dict]] = []
    seen_urls: set[tuple[str, str]] = set()
    seen_dois: set[str] = set()

    for rec in ranked[:25]:
        score = float(rec.get("rank_score") or 0.0)
        if score < TITLE_DIRECT_THRESHOLD:
            continue

        source = str(rec.get("resolver") or "unknown")

        urls: list[str] = []

        # Pull URLs from the normalized record and from raw API payloads.
        urls.extend(_deep_find_pdf_urls(rec))
        raw = rec.get("raw")
        if raw is not None:
            urls.extend(_deep_find_pdf_urls(raw))

        # Explicit common URL fields.
        for field in (
            "pdf_url",
            "download_url",
            "url",
            "landing_page_url",
            "fulltext_url",
            "full_text_url",
            "content_url",
        ):
            value = rec.get(field)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)
            elif isinstance(value, list):
                urls.extend(
                    x for x in value
                    if isinstance(x, str) and x.startswith(("http://", "https://"))
                )

        # DOI recovery is preferred because the mature DOI pipeline knows
        # Unpaywall/OpenAlex/PMC/etc. better than the generic direct URL path.
        doi = normalize_doi(str(rec.get("doi") or ""))
        if doi and score >= TITLE_ACCEPT_THRESHOLD and doi not in seen_dois:
            seen_dois.add(doi)
            try:
                doi_result = _fetch_original(
                    doi,
                    out_dir,
                    dry_run=False,
                    overwrite=overwrite,
                    timeout=timeout,
                    sources=sources,
                )
                if doi_result.get("success"):
                    doi_result["searched_title"] = title
                    doi_result["resolved_doi"] = doi
                    doi_result["title_resolution"] = {
                        "resolver": source,
                        "record": {
                            k: v for k, v in rec.items()
                            if k != "raw"
                        },
                        "resolvers_tried": tried,
                        "match_score": score,
                    }
                    return doi_result
            except Exception as exc:
                _progress("title_direct_doi_fetch_error", doi=doi, error=str(exc))

        for url in urls:
            key = (source, url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            pdf_candidates.append((source, url, rec))

    # Direct URL candidates are attempted after bibliographic ranking.
    # This permits title-only records with no DOI to be recovered.
    for source, url, rec in pdf_candidates:
        fname_meta = {
            "title": rec.get("title") or title,
            "year": rec.get("year"),
            "author": rec.get("author"),
            "doi": rec.get("doi"),
        }
        dest = out_dir / _filename(fname_meta)

        if not overwrite and dest.exists():
            try:
                if validate_pdf_data(dest.read_bytes())[0]:
                    return {
                        "searched_title": title,
                        "resolved_doi": normalize_doi(str(rec.get("doi") or "")) or None,
                        "success": True,
                        "source": "cache",
                        "file": str(dest),
                        "meta": fname_meta,
                    }
            except Exception:
                pass

        try:
            if _download(url, dest, timeout=timeout) is None:
                try:
                    ok, _, _ = validate_pdf_data(dest.read_bytes())
                except Exception:
                    ok = False
                if ok:
                    return {
                        "searched_title": title,
                        "resolved_doi": normalize_doi(str(rec.get("doi") or "")) or None,
                        "success": True,
                        "source": source,
                        "pdf_url": url,
                        "file": str(dest),
                        "title_resolution": {
                            "resolver": source,
                            "record": {
                                k: v for k, v in rec.items()
                                if k != "raw"
                            },
                            "resolvers_tried": tried,
                            "match_score": rec.get("rank_score"),
                        },
                    }
        except Exception as exc:
            _progress(
                "title_direct_download_error",
                source=source,
                url=url,
                error=str(exc),
            )

    best = ranked[0]
    return {
        "searched_title": title,
        "success": False,
        "resolved_doi": normalize_doi(str(best.get("doi") or "")) or None,
        "search_method": "title_direct_all_sources",
        "title_resolution": {
            "resolver": best.get("resolver"),
            "resolvers_tried": tried,
            "match_score": best.get("rank_score"),
            "title_similarity": best.get("title_similarity"),
            "title_coverage": best.get("title_coverage"),
            "source_count": best.get("source_count"),
            "candidates": [
                {
                    k: v for k, v in rec.items()
                    if k != "raw"
                }
                for rec in ranked[:15]
            ],
        },
        "error": {
            "code": "title_candidate_pdf_unavailable",
            "message": (
                "Candidatos bibliográficos foram encontrados, "
                "mas nenhum PDF pôde ser recuperado."
            ),
        },
    }
_fetch_with_expanded_previous = fetch


def fetch(doi: str, out_dir: Path, *, dry_run: bool, overwrite: bool, timeout: int, sources: list[str] | None = None) -> dict:
    result = _fetch_with_expanded_previous(
        doi, out_dir, dry_run=dry_run, overwrite=overwrite, timeout=timeout, sources=sources
    )
    if result.get("success") or dry_run:
        return result

    meta = result.get("meta") or {}
    title = meta.get("title")
    if not title:
        return result

    # Do not launch another round for already-terminal publisher/HTTP failures
    # unless an explicit recovery flag is enabled.
    if os.environ.get("PAPER_FETCH_TITLE_RECOVERY", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return result

    recovery = fetch_title_direct(title, out_dir, timeout=min(timeout, 12), overwrite=overwrite, sources=sources)
    if recovery.get("success"):
        recovery.setdefault("recovery", {})["from_doi"] = doi
        recovery["recovery"]["strategy"] = "canonical_title_repository_search"
        return recovery

    result.setdefault("recovery", recovery)
    return result


# HTTP helpers for optional authenticated metadata APIs used by TITLE RECOVERY V4.
def _get_text(url: str, *, timeout: int) -> str:
    return _get(url, accept="application/json,application/xml,text/plain,*/*", timeout=timeout).decode("utf-8", "replace")


def _get_json_with_headers(url: str, *, timeout: int, headers: dict[str, str]) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        _emit(_envelope_err("internal_error", str(e)))
        sys.exit(EXIT_TRANSPORT)
