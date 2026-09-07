#!/usr/bin/env python3
"""Gmail over IMAP: CLI for recon + MCP server for Claude Code.

Auth: Google App Password (requires 2FA on the account).
Stored in the macOS Keychain, never on disk in plaintext.

    security add-generic-password -a <email> -s gmail-imap-mcp -w

Override for one-off runs with GMAIL_APP_PASSWORD / GMAIL_ADDRESS.

CLI (stdlib only, no install needed):
    python3 gmail_tool.py senders --query "newer_than:90d"
    python3 gmail_tool.py search  --query "from:wolt newer_than:90d"
    python3 gmail_tool.py show    --uid 12345

MCP server (needs the mcp package):
    uv run --with mcp gmail_tool.py serve
"""

from __future__ import annotations

import argparse
import email
import email.policy
import imaplib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from email.header import decode_header, make_header
from html.parser import HTMLParser

IMAP_HOST = "imap.gmail.com"
KEYCHAIN_SERVICE = "gmail-imap-mcp"
DEFAULT_ADDRESS = "s.s.filatov94@gmail.com"

# imaplib caps literals at 10k by default; receipt bodies can exceed it.
imaplib._MAXLINE = 10_000_000


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------

def get_address() -> str:
    return os.environ.get("GMAIL_ADDRESS") or DEFAULT_ADDRESS


def get_password(address: str) -> str:
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if pw:
        return pw.replace(" ", "")
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-a", address,
             "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip().replace(" ", "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit(
            f"No app password found for {address}.\n"
            f"Create one at https://myaccount.google.com/apppasswords then run:\n"
            f"  security add-generic-password -a {address} "
            f"-s {KEYCHAIN_SERVICE} -w"
        )


# --------------------------------------------------------------------------
# imap plumbing
# --------------------------------------------------------------------------

class Mailbox:
    """Connects, selects Gmail's All Mail folder, exposes Gmail-syntax search."""

    def __init__(self, folder: str | None = None, readonly: bool = True) -> None:
        self.address = get_address()
        self.imap = imaplib.IMAP4_SSL(IMAP_HOST, 993)
        self.imap.login(self.address, get_password(self.address))
        self.imap.select(folder or self._all_mail(), readonly=readonly)

    def _all_mail(self) -> str:
        """Find All Mail by its \\All attribute -- the name is localised."""
        typ, boxes = self.imap.list()
        if typ == "OK":
            for raw in boxes:
                line = raw.decode("utf-8", "replace")
                if r"\All" in line:
                    return '"%s"' % line.split(' "/" ')[-1].strip('"')
        return '"[Gmail]/All Mail"'

    def close(self) -> None:
        try:
            self.imap.logout()
        except Exception:
            pass

    def search(self, gmail_query: str, limit: int = 200) -> list[bytes]:
        """Search using native Gmail syntax (from:, newer_than:, has:, ...)."""
        if gmail_query.isascii():
            typ, data = self.imap.uid(
                "SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", _quote(gmail_query))
        else:
            # imaplib cannot inline non-ASCII args; hand it over as a literal.
            self.imap.literal = gmail_query.encode("utf-8")
            typ, data = self.imap.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
        if typ != "OK" or not data or not data[0]:
            return []
        uids = sorted(data[0].split(), key=int, reverse=True)
        return uids[:limit]  # newest first

    def headers(self, uids: list[bytes]) -> list[dict]:
        """Batch-fetch just the envelope fields we care about."""
        rows: list[dict] = []
        for chunk in _chunks(uids, 200):
            ids = b",".join(chunk)
            typ, data = self.imap.uid(
                "FETCH", ids,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])",
            )
            if typ != "OK":
                continue
            for item in data:
                if not isinstance(item, tuple):
                    continue
                uid = _uid_of(item[0])
                msg = email.message_from_bytes(item[1], policy=email.policy.default)
                rows.append({
                    "uid": uid,
                    "from": _decode(msg.get("From")),
                    "subject": _decode(msg.get("Subject")),
                    "date": _decode(msg.get("Date")),
                })
        order = {u.decode(): i for i, u in enumerate(uids)}
        rows.sort(key=lambda r: order.get(r["uid"], 1 << 30))
        return rows

    def body(self, uid: str, max_chars: int = 20000) -> dict:
        typ, data = self.imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            raise SystemExit(f"message {uid} not found")
        msg = email.message_from_bytes(data[0][1], policy=email.policy.default)
        return {
            "uid": uid,
            "from": _decode(msg.get("From")),
            "to": _decode(msg.get("To")),
            "subject": _decode(msg.get("Subject")),
            "date": _decode(msg.get("Date")),
            "body": _body_text(msg)[:max_chars],
        }


def _quote(q: str) -> str:
    return '"%s"' % q.replace("\\", "\\\\").replace('"', '\\"')


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _uid_of(prefix: bytes) -> str:
    m = re.search(rb"UID (\d+)", prefix)
    return m.group(1).decode() if m else ""


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


# --------------------------------------------------------------------------
# body extraction
# --------------------------------------------------------------------------

class _Text(HTMLParser):
    SKIP = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "table"):
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append("\t")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append(data.strip())


def _html_to_text(html: str) -> str:
    p = _Text()
    try:
        p.feed(html)
    except Exception:
        return html
    text = " ".join(p.out)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _body_text(msg) -> str:
    """Prefer text/plain; fall back to flattened HTML."""
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", "replace")
        (plain if ctype == "text/plain" else html).append(content)
    if plain and len("".join(plain).strip()) > 40:
        return "\n".join(plain).strip()
    if html:
        return _html_to_text("\n".join(html))
    return "\n".join(plain).strip()


# --------------------------------------------------------------------------
# operations shared by CLI and MCP
# --------------------------------------------------------------------------

def op_senders(query: str, scan_limit: int = 4000) -> list[dict]:
    """Who sends mail matching `query`, ranked by volume -- the recon tool."""
    mb = Mailbox()
    try:
        rows = mb.headers(mb.search(query, limit=scan_limit))
    finally:
        mb.close()
    counter: Counter[str] = Counter()
    names: dict[str, str] = {}
    samples: dict[str, str] = {}
    for r in rows:
        addr = _addr(r["from"])
        counter[addr] += 1
        names.setdefault(addr, _name(r["from"]))
        samples.setdefault(addr, r["subject"])
    return [
        {"address": a, "name": names.get(a, ""), "count": c,
         "sample_subject": samples.get(a, "")}
        for a, c in counter.most_common()
    ]


def op_search(query: str, limit: int = 50) -> list[dict]:
    mb = Mailbox()
    try:
        return mb.headers(mb.search(query, limit=limit))
    finally:
        mb.close()


def op_show(uid: str, max_chars: int = 20000) -> dict:
    mb = Mailbox()
    try:
        return mb.body(uid, max_chars=max_chars)
    finally:
        mb.close()


def _addr(from_header: str) -> str:
    m = re.search(r"<([^>]+)>", from_header)
    return (m.group(1) if m else from_header).strip().lower()


def _name(from_header: str) -> str:
    return re.sub(r"<[^>]*>", "", from_header).strip().strip('"')


# --------------------------------------------------------------------------
# MCP server
# --------------------------------------------------------------------------

def serve() -> None:
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server

    mcp = Server("gmail")

    @mcp.tool()
    def list_senders(query: str = "newer_than:90d", scan_limit: int = 4000) -> str:
        """Rank senders of messages matching a Gmail search query.

        Use this to discover which services actually email receipts.
        `query` uses native Gmail syntax, e.g. "newer_than:90d category:purchases".
        """
        return json.dumps(op_senders(query, scan_limit), ensure_ascii=False, indent=1)

    @mcp.tool()
    def search_mail(query: str, limit: int = 50) -> str:
        """List messages matching a Gmail search query (newest first).

        Returns uid/from/subject/date. Gmail syntax: from:, subject:, has:attachment,
        newer_than:30d, label:receipts, category:purchases.
        """
        return json.dumps(op_search(query, limit), ensure_ascii=False, indent=1)

    @mcp.tool()
    def get_message(uid: str, max_chars: int = 20000) -> str:
        """Fetch one message's full text by uid (from search_mail). HTML is flattened."""
        return json.dumps(op_show(uid, max_chars), ensure_ascii=False, indent=1)

    mcp.run()


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("senders", help="rank senders matching a query")
    p.add_argument("--query", default="newer_than:90d")
    p.add_argument("--scan-limit", type=int, default=4000)

    p = sub.add_parser("search", help="list messages matching a query")
    p.add_argument("--query", required=True)
    p.add_argument("--limit", type=int, default=50)

    p = sub.add_parser("show", help="print one message body")
    p.add_argument("--uid", required=True)
    p.add_argument("--max-chars", type=int, default=20000)

    sub.add_parser("check", help="verify credentials work")
    sub.add_parser("serve", help="run as an MCP server over stdio")

    args = ap.parse_args()

    if args.cmd == "serve":
        return serve()
    if args.cmd == "check":
        mb = Mailbox()
        mb.close()
        print(f"OK: connected to {get_address()}")
        return

    if args.cmd == "senders":
        result = op_senders(args.query, args.scan_limit)
    elif args.cmd == "search":
        result = op_search(args.query, args.limit)
    else:
        result = op_show(args.uid, args.max_chars)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
