"""
`tally`: command-line access to a Talleyrand case, for agents working without
a browser.

    tally login
    tally cases
    tally import case.json
    tally export CASE
    tally outline CASE
    tally read CASE 1.2
    tally ask CASE "What comes next?" --under 1
    tally suggest CASE 1
    tally accept CASE SUGGESTION_ID
    tally decline CASE SUGGESTION_ID --reason too_basic
    tally doc add CASE notes.txt --under 1
    tally report CASE --guidance "focus on the cost tradeoffs"
"""

from __future__ import annotations

import argparse
import sys

import httpx

from talleyrand.cli import commands
from talleyrand.cli.client import TallyClient
from talleyrand.cli.errors import TallyError

DECLINE_REASONS = ["know_this", "too_basic", "off_topic", "unclear"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tally", description="Work a Talleyrand case from the command line."
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Backend base URL (default: $TALLY_URL or http://127.0.0.1:8000/backend)",
    )
    parser.add_argument(
        "--tokens",
        default=None,
        help="Path to the tokens file (default: $TALLY_TOKENS or ~/.config/tally/tokens.json)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="Machine-readable JSON output")
        return sub

    add("login", "Sign in and save tokens").set_defaults(handler=commands.cmd_login)

    add("cases", "List cases").set_defaults(handler=commands.cmd_cases)

    p = add("import", "Create a case from an export file")
    p.add_argument("file", help="Path to a case export JSON file")
    p.set_defaults(handler=commands.cmd_import)

    p = add("export", "Print a case's full JSON")
    p.add_argument("case", help="Case id")
    p.set_defaults(handler=commands.cmd_export)

    p = add("outline", "Print a case's numbered question tree")
    p.add_argument("case", help="Case id")
    p.set_defaults(handler=commands.cmd_outline)

    p = add("read", "Print one question's answer, highlights, and sources")
    p.add_argument("case", help="Case id")
    p.add_argument("number", help="Outline number, e.g. 1.2")
    p.add_argument(
        "--no-mark-read",
        action="store_true",
        help="Look without recording a read event (leaves the case's read history untouched)",
    )
    p.set_defaults(handler=commands.cmd_read)

    p = add("ask", "Ask a question and wait for its answer")
    p.add_argument("case", help="Case id")
    p.add_argument("text", help="The question text")
    p.add_argument(
        "--under",
        default=None,
        metavar="N",
        help="Outline number to file this under (default: new root question)",
    )
    p.add_argument(
        "--model",
        default=None,
        help="Model id (default: the case's last question's model, moved to the same "
        "depth on a provider the server can run)",
    )
    p.add_argument("--no-wait", action="store_true", help="Start generation and return immediately")
    p.add_argument(
        "--timeout", type=float, default=600.0, help="Seconds to wait for the answer (default: 600)"
    )
    p.add_argument(
        "--no-mark-read",
        action="store_true",
        help="Don't record a read event once the answer lands",
    )
    p.set_defaults(handler=commands.cmd_ask)

    p = add("suggest", "Generate follow-up suggestions for a question")
    p.add_argument("case", help="Case id")
    p.add_argument("number", help="Outline number to suggest follow-ups for")
    p.add_argument("--more", action="store_true", help="Ask for more, past what's already pending")
    p.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for suggestions (default: 300)",
    )
    p.set_defaults(handler=commands.cmd_suggest)

    p = add("accept", "Accept a pending suggestion as a question")
    p.add_argument("case", help="Case id")
    p.add_argument("suggestion_id", help="Suggestion id (from `outline` or `suggest`)")
    p.set_defaults(handler=commands.cmd_accept)

    p = add("decline", "Decline a pending suggestion")
    p.add_argument("case", help="Case id")
    p.add_argument("suggestion_id", help="Suggestion id (from `outline` or `suggest`)")
    p.add_argument(
        "--reason", choices=DECLINE_REASONS, default=None, help="Why (omit to decline without one)"
    )
    p.set_defaults(handler=commands.cmd_decline)

    doc_parser = subparsers.add_parser("doc", help="Case document commands")
    doc_subparsers = doc_parser.add_subparsers(dest="doc_command", required=True)
    p = doc_subparsers.add_parser("add", help="Attach a text document")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p.add_argument("case", help="Case id")
    p.add_argument("file", help="Path to a .txt or .md file")
    p.add_argument(
        "--under",
        default=None,
        metavar="N",
        help="Outline number to attach it to (default: the whole case)",
    )
    p.add_argument("--name", default=None, help="Display name (default: the file's name)")
    p.set_defaults(handler=commands.cmd_doc_add)

    p = add("report", "Generate the case's Markdown report")
    p.add_argument("case", help="Case id")
    p.add_argument("--guidance", default=None, help="How to focus the report")
    p.set_defaults(handler=commands.cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    with TallyClient(url=args.url, tokens_file=args.tokens) as client:
        try:
            args.handler(client, args)
        except TallyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except httpx.TransportError as exc:
            print(
                f"error: cannot reach Talleyrand at {client.http.base_url}: {exc}", file=sys.stderr
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
