#!/usr/bin/env python3
# GoByPASS403: Fast & Comprehensive WAF (HTTP 403/401) Bypass Engine in Python.
#
# Optimized for high-throughput paper fetching with instant short-circuiting:
# 1. Browser Profile Fingerprints (Chrome 126, Safari 17, Firefox 126 with Client Hints)
# 2. Consolidated High-Impact IP Spoofing (X-Forwarded-For, X-Real-IP, CF-Connecting-IP, True-Client-IP, RFC 7239)
# 3. Scheme & Port Spoofing (X-Forwarded-Proto, Front-End-Https, X-Forwarded-Port)
# 4. URL Rewriting & Routing Headers (X-Original-URL, X-Rewrite-URL, X-Forwarded-URI)
# 5. Raw URL Preservation & Path Mutations (curl --path-as-is, ?download=true, #.pdf fragment ACL bypass)
# 6. Method Switching (POST with Content-Length: 0)

from __future__ import annotations

import gzip
import ipaddress
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

try:
    from curl_cffi import requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError:
        requests = None
        HTTPAdapter = None
        Retry = None
    CURL_CFFI_AVAILABLE = False


MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB
PROBE_TIMEOUT = 3  # Fast 3s timeout per probe to avoid stalling worker threads

BROWSER_PROFILES = [
    {
        "name": "chrome_win",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,application/xml;q=0.8,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Sec-Ch-Ua": chr(34) + "Not/A)Brand" + chr(34) + ";v=" + chr(34) + "8" + chr(34) + ", " + chr(34) + "Chromium" + chr(34) + ";v=" + chr(34) + "126" + chr(34) + ", " + chr(34) + "Google Chrome" + chr(34) + ";v=" + chr(34) + "126" + chr(34),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": chr(34) + "Windows" + chr(34),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        },
    },
    {
        "name": "chrome_mac",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,application/xml;q=0.8,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": chr(34) + "Chromium" + chr(34) + ";v=" + chr(34) + "125" + chr(34) + ", " + chr(34) + "Not.A/Brand" + chr(34) + ";v=" + chr(34) + "24" + chr(34) + ", " + chr(34) + "Google Chrome" + chr(34) + ";v=" + chr(34) + "125" + chr(34),
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": chr(34) + "macOS" + chr(34),
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        },
    },
    {
        "name": "safari_mac",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
            "Connection": "keep-alive",
        },
    },
]

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.aws.internal",
    "metadata",
}


def is_safe_url(url: str) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"scheme_not_allowed:{parsed.scheme}"
        if parsed.port is not None and parsed.port not in (80, 443):
            return False, f"port_not_allowed:{parsed.port}"
        host = (parsed.hostname or "").lower()
        if not host:
            return False, "empty_host"
        if host in _BLOCKED_HOSTS:
            return False, f"blocked_host:{host}"
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if (
                literal.is_private
                or literal.is_loopback
                or literal.is_link_local
                or literal.is_reserved
                or literal.is_multicast
                or literal.is_unspecified
            ):
                return False, f"private_ip:{literal}"
        return True, ""
    except Exception as exc:
        return False, f"url_parse_error:{exc}"


def validate_pdf_data(data: bytes) -> tuple[bool, bytes, str]:
    """Strictly validates that data is a complete, uncorrupted, parseable PDF."""
    if not data or len(data) < 1024:
        return False, b"", "file_too_small"

    # Decompress gzip payload if needed
    if len(data) >= 2 and data[:2] == bytes([0x1f, 0x8b]):
        try:
            data = gzip.decompress(data)
        except Exception as exc:
            return False, b"", f"gzip_decompress_error:{exc}"

    if len(data) > MAX_PDF_SIZE:
        return False, b"", "size_exceeded"

    if not data.startswith(b"%PDF"):
        return False, b"", "invalid_pdf_header"

    # Check for EOF marker within the last 2048 bytes
    if b"%%EOF" not in data[-2048:]:
        return False, b"", "missing_eof_marker"

    # In-memory parsing verification with pypdf (if available)
    try:
        import io
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        if len(reader.pages) == 0:
            return False, b"", "empty_pdf_pages"
    except ImportError:
        pass
    except Exception as exc:
        return False, b"", f"corrupted_pdf_structure:{exc}"

    return True, data, ""


@dataclass
class BypassResult:
    success: bool
    status_code: int
    data: bytes | None = None
    module_used: str = ""
    technique: str = ""
    error: str | None = None
    url: str = ""
    headers_used: dict[str, str] = field(default_factory=dict)


class GoByPASS403Engine:
    def __init__(
        self,
        *,
        timeout: int = 15,
        enable_curl: bool = True,
    ):
        self.timeout = timeout
        self.enable_curl = enable_curl and (shutil.which("curl") is not None)
        self._session: requests.Session | None = None
        self._success_cache = {}
        
        self.proxies = None
        proxy_url = os.environ.get("PROXY_URL", "").strip()
        if proxy_url:
            self.proxies = {"http": proxy_url, "https": proxy_url}

        if requests is not None:
            if CURL_CFFI_AVAILABLE:
                self._session = requests.Session(impersonate="chrome", proxies=self.proxies)
            else:
                self._session = requests.Session()
                adapter = HTTPAdapter(pool_connections=25, pool_maxsize=25, max_retries=1)
                self._session.mount("http://", adapter)
                self._session.mount("https://", adapter)
                if self.proxies:
                    self._session.proxies.update(self.proxies)

    def _get_base_headers(self, url: str) -> dict[str, str]:
        profile = random.choice(BROWSER_PROFILES)
        headers = dict(profile["headers"])
        parsed = urllib.parse.urlparse(url)
        headers["Host"] = parsed.netloc
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

        # Elsevier / ScienceDirect Official API Authentication Headers
        if "api.elsevier.com" in url:
            els_key = os.environ.get("ELSEVIER_API_KEY", "").strip()
            if els_key:
                headers["X-ELS-APIKey"] = els_key
            els_inst = os.environ.get("ELSEVIER_INST_TOKEN", "").strip()
            if els_inst:
                headers["X-ELS-Insttoken"] = els_inst
            els_bearer = os.environ.get("ELSEVIER_BEARER_TOKEN", "").strip()
            if els_bearer:
                headers["Authorization"] = f"Bearer {els_bearer}"

        # Polite pool mailto headers for CrossRef & OpenAlex
        cr_mailto = os.environ.get("CROSSREF_MAILTO", "").strip()
        if cr_mailto and "crossref.org" in url:
            headers["User-Agent"] = f"{headers.get('User-Agent', '')} (mailto:{cr_mailto})"

        oa_mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
        if oa_mailto and "openalex.org" in url:
            headers["User-Agent"] = f"{headers.get('User-Agent', '')} (mailto:{oa_mailto})"

        return headers

    def _is_valid_pdf(self, data: bytes) -> bool:
        ok, _, _ = validate_pdf_data(data)
        return ok

    def _exec_curl(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: int,
        raw_path: bool = True,
    ) -> tuple[int, bytes, str]:
        if not self.enable_curl:
            return 0, b"", "curl_not_available"

        cmd = [
            "curl",
            "-sSL",
            "--compressed",
            "--max-time",
            str(timeout),
            "-w",
            chr(10) + "%{http_code}",
        ]
        if raw_path:
            cmd.append("--path-as-is")

        for k, v in headers.items():
            cmd.extend(["-H", f"{k}: {v}"])

        cmd.append(url)

        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout + 2,
            )
            out = res.stdout
            if not out:
                return 0, b"", res.stderr.decode("utf-8", "ignore")

            parts = out.rsplit(bytes([10]), 1)
            if len(parts) == 2:
                body, code_str = parts[0], parts[1].strip().decode("ascii", "ignore")
                try:
                    status_code = int(code_str)
                except ValueError:
                    status_code = 200 if body.startswith(b"%PDF") else 0
                return status_code, body, ""
            return 200, out, ""
        except Exception as exc:
            return 0, b"", str(exc)

    def _exec_requests(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout: int,
        method: str = "GET",
    ) -> tuple[int, bytes, str]:
        if self._session is None:
            return 0, b"", "requests_not_installed"

        try:
            if method.upper() == "GET":
                resp = self._session.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                )
            elif method.upper() == "POST":
                resp = self._session.post(
                    url,
                    headers=headers,
                    data=b"",
                    timeout=timeout,
                    allow_redirects=True,
                )
            else:
                resp = self._session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=timeout,
                    allow_redirects=True,
                )

            return resp.status_code, resp.content, ""
        except Exception as exc:
            return 0, b"", str(exc)

    def execute_request(
        self,
        url: str,
        *,
        timeout: int | None = None,
        custom_headers: dict[str, str] | None = None,
        require_pdf: bool = False,
    ) -> BypassResult:
        t_out = timeout or self.timeout
        p_out = t_out if require_pdf else min(t_out, PROBE_TIMEOUT)

        safe, reason = is_safe_url(url)
        if not safe:
            return BypassResult(success=False, status_code=0, error=f"unsafe_url:{reason}", url=url)

        base_headers = self._get_base_headers(url)
        if custom_headers:
            base_headers.update(custom_headers)

        parsed = urllib.parse.urlparse(url)
        path = parsed.path or "/"

        # -------------------------------------------------------------------
        # 1. Standard Modern Browser Request
        # -------------------------------------------------------------------
        status, data, err = self._exec_requests(url, base_headers, timeout=p_out)
        if status in (200, 206) and data:
            if not require_pdf or self._is_valid_pdf(data):
                return BypassResult(
                    success=True,
                    status_code=status,
                    data=data,
                    module_used="browser_profile",
                    technique="standard_headers",
                    url=url,
                    headers_used=base_headers,
                )

        if status == 404:
            return BypassResult(success=False, status_code=404, error="not_found", url=url)

        # -------------------------------------------------------------------
        # 2. Consolidated High-Impact IP + Scheme + Routing Spoofing
        # -------------------------------------------------------------------
        spoof_headers = dict(base_headers)
        spoof_headers.update({
            "X-Forwarded-For": "127.0.0.1, 10.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "CF-Connecting-IP": "127.0.0.1",
            "True-Client-IP": "127.0.0.1",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Port": "443",
            "X-Original-URL": path,
            "X-Rewrite-URL": path,
        })

        status, data, err = self._exec_requests(url, spoof_headers, timeout=p_out)
        if status in (200, 206) and data:
            if not require_pdf or self._is_valid_pdf(data):
                return BypassResult(
                    success=True,
                    status_code=status,
                    data=data,
                    module_used="headers_ip_scheme_url",
                    technique="consolidated_spoof",
                    url=url,
                    headers_used=spoof_headers,
                )

        # -------------------------------------------------------------------
        # 3. Raw Curl Engine with --path-as-is + Spoofed Headers
        # -------------------------------------------------------------------
        if self.enable_curl:
            status, data, err = self._exec_curl(url, spoof_headers, timeout=p_out, raw_path=True)
            if status in (200, 206) and data:
                if not require_pdf or self._is_valid_pdf(data):
                    return BypassResult(
                        success=True,
                        status_code=status,
                        data=data,
                        module_used="curl_raw_path_as_is",
                        technique="curl_raw",
                        url=url,
                        headers_used=spoof_headers,
                    )

        # -------------------------------------------------------------------
        # 4. Top 3 Path & Fragment Variations
        # -------------------------------------------------------------------
        path_variants = [
            f"{url}#",
            f"{url}#.pdf",
            f"{url}?download=true" if "?" not in url else f"{url}&download=true",
        ]
        for variant_url in path_variants:
            status, data, err = self._exec_requests(variant_url, spoof_headers, timeout=p_out)
            if status in (200, 206) and data:
                if not require_pdf or self._is_valid_pdf(data):
                    return BypassResult(
                        success=True,
                        status_code=status,
                        data=data,
                        module_used="path_fragment_bypass",
                        technique=variant_url,
                        url=variant_url,
                        headers_used=spoof_headers,
                    )

        # -------------------------------------------------------------------
        # 5. Method Switching (POST with Content-Length: 0)
        # -------------------------------------------------------------------
        post_headers = dict(spoof_headers)
        post_headers["Content-Length"] = "0"
        status, data, err = self._exec_requests(url, post_headers, timeout=p_out, method="POST")
        if status in (200, 206) and data:
            if not require_pdf or self._is_valid_pdf(data):
                return BypassResult(
                    success=True,
                    status_code=status,
                    data=data,
                    module_used="http_methods",
                    technique="POST",
                    url=url,
                    headers_used=post_headers,
                )

        return BypassResult(
            success=False,
            status_code=status or 403,
            error="bypass_exhausted",
            url=url,
        )

    def download_pdf(
        self,
        url: str,
        dest: Path,
        *,
        timeout: int = 35,
    ) -> tuple[bool, str | None]:
        res = self.execute_request(url, timeout=timeout, require_pdf=True)
        if not res.success or not res.data:
            return False, res.error or "download_failed"

        valid, clean_data, reason = validate_pdf_data(res.data)
        if not valid:
            return False, reason

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_name(f".{dest.name}.tmp.{os.getpid()}_{random.randint(1000, 9999)}")
            tmp_dest.write_bytes(clean_data)
            tmp_dest.replace(dest)
            return True, None
        except OSError as exc:
            return False, f"io_error:{exc}"


default_engine = GoByPASS403Engine()


def bypass_get(url: str, *, timeout: int = 15, headers: dict[str, str] | None = None) -> BypassResult:
    return default_engine.execute_request(url, timeout=timeout, custom_headers=headers, require_pdf=False)


def bypass_download_pdf(url: str, dest: Path, *, timeout: int = 20) -> tuple[bool, str | None]:
    return default_engine.download_pdf(url, dest, timeout=timeout)
