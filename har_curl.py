#!/usr/bin/env python3
"""HAR → curl: stdlib CLI that emits curated curl commands from a HAR export."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

# Headers redacted by default (case-insensitive name match).
SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "proxy-authorization",
    }
)

REDACTED = "REDACTED"
MAX_BODY_BYTES = 64 * 1024  # skip / mark bodies larger than this


def shell_single_quote(s: str) -> str:
    """Escape a string for use inside single quotes in a POSIX shell."""
    return "'" + s.replace("'", "'\\''") + "'"


def looks_like_bearer(value: str) -> bool:
    v = value.strip()
    if len(v) < 8:
        return False
    lower = v.lower()
    if lower.startswith("bearer ") and len(v) > 15:
        return True
    return False


def should_redact_header(name: str, value: str, keep_secrets: bool) -> bool:
    if keep_secrets:
        return False
    if name.lower() in SENSITIVE_HEADER_NAMES:
        return True
    if looks_like_bearer(value):
        return True
    return False


def is_binary_mime(mime: str) -> bool:
    m = (mime or "").lower().split(";")[0].strip()
    if not m:
        return False
    if m.startswith("text/"):
        return False
    if m in (
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/x-www-form-urlencoded",
        "application/javascript",
        "application/graphql",
    ):
        return False
    if m.endswith("+json") or m.endswith("+xml"):
        return False
    if m.startswith("multipart/"):
        return True
    if m.startswith("image/") or m.startswith("audio/") or m.startswith("video/"):
        return True
    if m in ("application/octet-stream", "application/pdf", "application/zip"):
        return True
    return False


def compact_json_if_possible(text: str, mime: str) -> str:
    m = (mime or "").lower()
    if "json" not in m and not text.lstrip().startswith(("{", "[")):
        return text
    try:
        return json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
    except (ValueError, TypeError):
        return text


def header_pairs(headers: Any) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not isinstance(headers, list):
        return out
    for h in headers:
        if not isinstance(h, dict):
            continue
        name = h.get("name")
        value = h.get("value")
        if name is None or value is None:
            continue
        out.append((str(name), str(value)))
    return out


def filter_headers(
    headers: Sequence[Tuple[str, str]],
    keep_secrets: bool,
    include_headers: Optional[Sequence[str]],
    all_headers: bool,
) -> List[Tuple[str, str]]:
    allow = None
    if include_headers:
        allow = {x.lower() for x in include_headers if x}
    result: List[Tuple[str, str]] = []
    for name, value in headers:
        # Always skip Content-Length; curl recalculates.
        if name.lower() == "content-length":
            continue
        if not all_headers and allow is not None:
            if name.lower() not in allow:
                continue
        if should_redact_header(name, value, keep_secrets):
            result.append((name, REDACTED))
        else:
            result.append((name, value))
    return result


def build_curl(
    method: str,
    url: str,
    headers: Sequence[Tuple[str, str]],
    body_text: Optional[str],
    body_note: Optional[str],
    pretty: bool,
) -> str:
    parts: List[str] = ["curl"]
    method_u = method.upper()
    if method_u != "GET":
        parts.extend(["-X", method_u])
    parts.append(shell_single_quote(url))
    for name, value in headers:
        parts.extend(["-H", shell_single_quote("%s: %s" % (name, value))])
    if body_note:
        # Comment-like note for the user; keep as a trailing shell comment.
        pass
    if body_text is not None:
        parts.extend(["--data-raw", shell_single_quote(body_text)])

    if pretty:
        # Multiline with backslash continuations.
        lines = ["curl \\"]
        # Rebuild for pretty layout
        lines = ["curl"]
        if method_u != "GET":
            lines.append("  -X %s \\" % method_u)
        lines.append("  %s \\" % shell_single_quote(url))
        for name, value in headers:
            lines.append(
                "  -H %s \\" % shell_single_quote("%s: %s" % (name, value))
            )
        if body_text is not None:
            lines.append("  --data-raw %s" % shell_single_quote(body_text))
        else:
            # Drop trailing backslash on last line
            if lines[-1].endswith(" \\"):
                lines[-1] = lines[-1][:-2]
        # Ensure last line has no trailing \
        if lines[-1].endswith(" \\"):
            lines[-1] = lines[-1][:-2]
        cmd = "\n".join(lines)
        if body_note:
            cmd = "# %s\n%s" % (body_note, cmd)
        return cmd

    cmd = " ".join(parts)
    if body_note:
        cmd = "# %s\n%s" % (body_note, cmd)
    return cmd


def url_host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def url_path(url: str) -> str:
    try:
        p = urlparse(url)
        path = p.path or "/"
        if p.query:
            return path + "?" + p.query
        return path
    except Exception:
        return ""


def is_http_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    lower = url.strip().lower()
    if lower.startswith("data:"):
        return False
    if lower.startswith("blob:"):
        return False
    if lower.startswith("javascript:"):
        return False
    if lower.startswith("about:"):
        return False
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def extract_body(post_data: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return (body_text_or_None, note_or_None)."""
    if not isinstance(post_data, dict):
        return None, None
    mime = str(post_data.get("mimeType") or "")
    text = post_data.get("text")
    if text is None:
        # params-only form without text — skip emitting body
        if post_data.get("params"):
            return None, "body omitted: form params present but no text field"
        return None, None
    if not isinstance(text, str):
        return None, "body omitted: non-text postData"
    if is_binary_mime(mime):
        return None, "body omitted: binary mimeType %s" % (mime or "(empty)")
    raw_bytes = len(text.encode("utf-8", errors="replace"))
    if raw_bytes > MAX_BODY_BYTES:
        return None, "body omitted: %d bytes exceeds %d limit" % (
            raw_bytes,
            MAX_BODY_BYTES,
        )
    return compact_json_if_possible(text, mime), None


def load_har(source: str) -> Dict[str, Any]:
    if source == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(source, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            raise SystemExit("error: cannot read HAR file: %s" % e)
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SystemExit("error: invalid HAR JSON: %s" % e)
    if not isinstance(data, dict):
        raise SystemExit("error: HAR root must be a JSON object")
    return data


def iter_entries(har: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    log = har.get("log")
    if not isinstance(log, dict):
        raise SystemExit("error: HAR missing log object")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("error: HAR log.entries must be a list")
    for e in entries:
        if isinstance(e, dict):
            yield e


def parse_include_headers(values: Optional[List[str]]) -> Optional[List[str]]:
    if not values:
        return None
    names: List[str] = []
    for v in values:
        for part in v.split(","):
            part = part.strip()
            if part:
                names.append(part)
    return names or None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="har_curl",
        description="Convert a browser HAR export into curated curl commands. "
        "Auth-related headers are redacted by default.",
    )
    parser.add_argument(
        "har",
        help="Path to HAR file, or '-' to read from stdin",
    )
    parser.add_argument(
        "--host",
        metavar="SUBSTR",
        help="Only include requests whose host contains SUBSTR",
    )
    parser.add_argument(
        "--method",
        metavar="METHOD",
        help="Only include requests with this HTTP method (e.g. GET, POST)",
    )
    parser.add_argument(
        "--path",
        metavar="SUBSTR",
        dest="path_substr",
        help="Only include requests whose path+query contains SUBSTR",
    )
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Emit at most N curl commands",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Multiline curl with backslash continuations",
    )
    parser.add_argument(
        "--include-headers",
        action="append",
        metavar="NAME[,NAME...]",
        help="Only emit these request headers (comma-separated; repeatable). "
        "Default without this flag: emit all non-redacted headers "
        "(still redacting secrets unless --keep-secrets).",
    )
    parser.add_argument(
        "--all-headers",
        action="store_true",
        help="Emit all headers (still redacted unless --keep-secrets). "
        "Overrides --include-headers allowlist.",
    )
    parser.add_argument(
        "--keep-secrets",
        action="store_true",
        help="DANGEROUS: do not redact Authorization/Cookie/token headers",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.limit is not None and args.limit < 0:
        print("error: --limit must be >= 0", file=sys.stderr)
        return 2

    har = load_har(args.har)
    include = parse_include_headers(args.include_headers)
    # Default: emit all headers (with redaction). --include-headers narrows.
    # --all-headers forces full set even if include was set.
    all_headers = bool(args.all_headers) or include is None

    method_filter = args.method.upper() if args.method else None
    emitted = 0
    blocks: List[str] = []

    for entry in iter_entries(har):
        req = entry.get("request")
        if not isinstance(req, dict):
            continue
        url = req.get("url")
        if not isinstance(url, str) or not is_http_url(url):
            continue
        method = str(req.get("method") or "GET")
        if method_filter and method.upper() != method_filter:
            continue
        if args.host and args.host.lower() not in url_host(url).lower():
            continue
        if args.path_substr and args.path_substr not in url_path(url):
            continue

        headers = filter_headers(
            header_pairs(req.get("headers")),
            keep_secrets=args.keep_secrets,
            include_headers=include,
            all_headers=all_headers,
        )
        body_text, body_note = extract_body(req.get("postData"))
        cmd = build_curl(
            method, url, headers, body_text, body_note, pretty=args.pretty
        )
        blocks.append(cmd)
        emitted += 1
        if args.limit is not None and emitted >= args.limit:
            break

    sep = "\n\n" if args.pretty else "\n"
    if blocks:
        sys.stdout.write(sep.join(blocks))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
