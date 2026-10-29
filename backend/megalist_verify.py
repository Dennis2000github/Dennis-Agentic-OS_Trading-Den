"""Controlled, SSRF-resistant source-link verification.

Only the hostnames configured in the owner's catalog settings are eligible.
Every URL is validated before the request, and every redirect is validated
again. Loopback, private, link-local, reserved, multicast and unspecified
destinations are refused. Responses are read with a hard byte cap.

A failed request is *not* treated as proof that an API is gone: only a
definitive 404/410 becomes ``unavailable``. Timeouts, 403/429 and 5xx become
``needs_review`` so a temporary provider problem cannot remove a healthy entry
from the catalog.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "DENNIS-OS-API-Mega-List-Verifier/1.0"
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 4096
DEFAULT_HOSTS = ("apify.com", "www.apify.com", "apimesa.com", "www.apimesa.com")


class VerifyError(Exception):
    def __init__(self, message, code="verify_blocked", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _allowed_hosts(allowed):
    hosts = tuple(host.lower() for host in (allowed or DEFAULT_HOSTS) if host)
    return hosts or DEFAULT_HOSTS


def _host_allowed(host, allowed):
    host = (host or "").lower().strip(".")
    if not host:
        return False
    for candidate in _allowed_hosts(allowed):
        candidate = candidate.lower().strip(".")
        if host == candidate or host.endswith("." + candidate):
            return True
    return False


def _is_public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return False
    if hasattr(ip, "is_global") and not ip.is_global:
        return False
    return True


def validate_url(url, allowed=None):
    """Validate scheme, host, port and every resolved address."""
    raw = str(url or "").strip()
    if not raw:
        raise VerifyError("A URL is required.", "url_invalid")
    if len(raw) > 2048:
        raise VerifyError("That URL is too long.", "url_invalid")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        raise VerifyError("That URL cannot be parsed.", "url_invalid")
    if parsed.scheme.lower() not in ("http", "https"):
        raise VerifyError("Only http(s) links can be checked.", "url_scheme")
    if parsed.username or parsed.password:
        raise VerifyError("URLs with embedded credentials are refused.", "url_credentials")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise VerifyError("That URL has no host.", "url_host")
    if not _host_allowed(host, allowed):
        raise VerifyError(
            "Only the configured catalog hosts can be checked.", "host_not_allowed"
        )
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        raise VerifyError("That URL has an invalid port.", "url_port")
    if port not in (80, 443):
        raise VerifyError("Only standard HTTP(S) ports are checked.", "url_port")
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise VerifyError("Cannot resolve that host: %s" % exc, "dns_failed")
    if not infos:
        raise VerifyError("That host did not resolve.", "dns_failed")
    for info in infos:
        address = info[4][0]
        if not _is_public_ip(address):
            raise VerifyError(
                "That host resolves to a non-public destination.", "ssrf_blocked"
            )
    return raw


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self, allowed):
        self.allowed = allowed
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.allowed)
        # Drop the fragment and re-use no credentials: only our user agent and
        # accept headers travel with the request.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _request(url, timeout, allowed, method="HEAD"):
    opener = urllib.request.build_opener(_SafeRedirectHandler(allowed))
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "Accept-Language": "en-US,en;q=0.8",
            "Range": "bytes=0-%d" % (MAX_BODY_BYTES - 1),
        },
    )
    started = time.time()
    with opener.open(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 0) or 0)
        final_url = response.geturl()
        body = b""
        if method != "HEAD":
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                body = body[:MAX_BODY_BYTES]
        response_headers = dict(response.headers.items())
    return {
        "status": status,
        "final_url": final_url,
        "bytes": len(body),
        "content_type": response_headers.get("Content-Type", ""),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def check_url(url, timeout=8, allowed=None):
    """Check one URL. Returns a result dict; never raises for network failures."""
    try:
        safe = validate_url(url, allowed)
    except VerifyError as exc:
        return {
            "status": "needs_review", "http_status": None, "method": "",
            "final_url": "", "error": "%s (%s)" % (str(exc), exc.code),
            "blocked": True,
        }

    attempts = []
    for method in ("HEAD", "GET"):
        try:
            result = _request(safe, timeout, allowed, method=method)
            code = result["status"]
            if method == "HEAD" and code in (400, 403, 405, 501):
                attempts.append("HEAD %s" % code)
                continue  # some providers reject HEAD; try a capped GET
            return {
                "status": "source_link_checked" if 200 <= code < 400 else (
                    "unavailable" if code in (404, 410) else "needs_review"
                ),
                "http_status": code,
                "method": method,
                "final_url": result["final_url"],
                "error": "" if 200 <= code < 400 else "HTTP %s" % code,
                "elapsed_ms": result["elapsed_ms"],
            }
        except urllib.error.HTTPError as exc:
            code = int(exc.code or 0)
            if method == "HEAD" and code in (400, 403, 405, 501):
                attempts.append("HEAD %s" % code)
                continue
            return {
                "status": "unavailable" if code in (404, 410) else "needs_review",
                "http_status": code,
                "method": method,
                "final_url": getattr(exc, "url", safe),
                "error": "HTTP %s" % code,
            }
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            attempts.append("%s %s" % (method, reason))
            if method == "GET":
                break
            continue

    return {
        "status": "needs_review",
        "http_status": None,
        "method": "HEAD+GET" if attempts else "",
        "final_url": safe,
        "error": "; ".join(str(item) for item in attempts)[:400] or "Request failed",
    }


def verify_entry(entry, timeout=8, allowed=None, extra_urls=None):
    """Check an entry's source URL (and explicit documentation URL if present)."""
    if not isinstance(entry, dict):
        raise VerifyError("A catalog entry is required.", "entry_required")
    source = entry.get("source_url")
    if not source:
        return {
            "status": "needs_review", "http_status": None, "method": "",
            "error": "The entry has no source URL.", "final_url": "",
        }
    result = check_url(source, timeout=timeout, allowed=allowed)
    docs = entry.get("documentation_url")
    if result.get("status") == "source_link_checked" and docs:
        docs_result = check_url(docs, timeout=timeout, allowed=allowed)
        result["documentation"] = docs_result
        if docs_result.get("status") == "source_link_checked":
            result["status"] = "documentation_available"
            result["error"] = ""
    for url in extra_urls or []:
        if url and result.get("status") == "source_link_checked":
            check = check_url(url, timeout=timeout, allowed=allowed)
            if check.get("status") != "source_link_checked":
                result["status"] = "needs_review"
                result["error"] = "Linked resource failed: %s" % check.get("error", "")
    return result
