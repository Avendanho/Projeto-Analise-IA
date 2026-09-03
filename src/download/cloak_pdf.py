#!/usr/bin/env python3
"""Fetch a publicly accessible PDF through CloakBrowser.

Usage:
    cloak_pdf.py <url> [timeout_seconds]

Stdout:
    Raw PDF bytes only.

Stderr:
    Diagnostic messages.

The helper uses CloakBrowser's Playwright-compatible synchronous API.
It navigates Chromium to the target URL and captures the final response,
avoiding cross-origin CORS failures from page-level fetch().
"""

from __future__ import annotations

import ipaddress
import os
import sys
import time
from urllib.parse import urlparse

MAX_PDF_SIZE = 50 * 1024 * 1024

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "metadata.aws.internal",
    "metadata",
}


def _err(message: str) -> None:
    print(f"[cloak] {message}", file=sys.stderr, flush=True)


def _url_is_safe(url: str) -> tuple[bool, str]:
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return False, "scheme_not_allowed"

        if parsed.port is not None and parsed.port not in (80, 443):
            return False, "port_not_allowed"

        host = (parsed.hostname or "").lower()
        if not host:
            return False, "empty_host"

        if host in _BLOCKED_HOSTS:
            return False, "blocked_host"

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
                return False, "private_ip"
            return True, ""

        import socket

        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return False, "dns_error"

        for info in infos:
            try:
                resolved = ipaddress.ip_address(info[4][0])
            except ValueError:
                continue

            if (
                resolved.is_private
                or resolved.is_loopback
                or resolved.is_link_local
                or resolved.is_reserved
                or resolved.is_multicast
                or resolved.is_unspecified
            ):
                return False, "private_ip"

        return True, ""

    except ValueError:
        return False, "invalid_url"
    except Exception as exc:
        return False, f"url_check_error:{exc}"


def _wait_for_challenge(page, timeout_s: int) -> None:
    deadline = time.monotonic() + min(timeout_s, 40)

    while time.monotonic() < deadline:
        try:
            title = (page.title() or "").lower()
        except Exception:
            title = ""

        if (
            not title
            or (
                "just a moment" not in title
                and not title.startswith("loading")
                and "checking your browser" not in title
            )
        ):
            return

        time.sleep(1)


def main() -> int:
    if not (2 <= len(sys.argv) <= 3):
        _err("usage: cloak_pdf.py <url> [timeout_seconds]")
        return 1

    url = sys.argv[1]

    try:
        timeout_s = max(5, int(sys.argv[2])) if len(sys.argv) == 3 else 60
    except ValueError:
        _err("timeout_seconds must be an integer")
        return 1

    safe, reason = _url_is_safe(url)
    if not safe:
        _err(f"refusing unsafe URL ({reason})")
        return 1

    try:
        from cloakbrowser import launch
    except ImportError as exc:
        _err(f"cloakbrowser import failed: {exc}")
        _err("install with: python -m pip install -U cloakbrowser")
        return 1

    headed = bool(os.environ.get("PAPER_FETCH_CLOAK_HEADED"))
    browser = None

    try:
        _err(
            "launching CloakBrowser "
            f"({'headed' if headed else 'headless'})"
        )

        browser = launch(
            headless=not headed,
            humanize=True,
        )

        page = browser.new_page()

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}/"

        _err(f"opening origin: {origin}")
        
        # Otimização: abortar recursos não essenciais
        page.route("**/*", lambda route: route.abort() 
            if route.request.resource_type in ["image", "media", "font", "stylesheet"] 
            else route.continue_()
        )
        
        try:
            page.goto(
                origin,
                wait_until="domcontentloaded",
                timeout=timeout_s * 1000,
            )
        except Exception as exc:
            _err(f"origin navigation warning: {exc}")

        _wait_for_challenge(page, timeout_s)

        _err(f"navigating to target: {url}")

        response = None
        last_error = None

        for attempt in range(2):
            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_s * 1000,
                )
                break
            except Exception as exc:
                last_error = exc
                _err(f"target navigation attempt {attempt + 1} failed: {exc}")
                if attempt == 0:
                    time.sleep(2)

        if response is None:
            _err(f"target navigation failed: {last_error}")
            return 1

        status = getattr(response, "status", None)

        try:
            final_url = response.url or ""
        except Exception:
            final_url = ""

        try:
            content_type = (response.headers.get("content-type") or "").lower()
        except Exception:
            content_type = ""

        _err(
            f"target response: HTTP {status}, "
            f"content-type={content_type!r}, final_url={final_url!r}"
        )

        if status != 200:
            _err(f"browser navigation returned HTTP {status}")
            return 1

        body = response.body()

        if not isinstance(body, (bytes, bytearray)):
            _err(f"unexpected response body type: {type(body)!r}")
            return 1

        body = bytes(body)

        if len(body) > MAX_PDF_SIZE:
            _err(f"response exceeds {MAX_PDF_SIZE} byte limit")
            return 1

        if not body.startswith(b"%PDF"):
            preview = body[:120].decode("utf-8", "replace").replace("\n", " ")
            _err(
                "browser returned a non-PDF response "
                f"(content-type={content_type!r}, bytes={len(body)}, "
                f"preview={preview!r})"
            )
            return 1

        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

        _err(f"success: {len(body)} bytes")
        return 0

    except Exception as exc:
        _err(f"failed: {exc}")
        return 1

    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception as exc:
                _err(f"browser close warning: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())