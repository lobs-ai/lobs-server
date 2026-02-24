#!/usr/bin/env python3
"""Workflow CRUD helper for scripts/agents.

Examples:
  python bin/workflow_tool.py list
  python bin/workflow_tool.py get --id <workflow-id>
  python bin/workflow_tool.py export --id <workflow-id> --out wf.json
  python bin/workflow_tool.py apply --file wf.json
  python bin/workflow_tool.py apply --file wf.json --id <workflow-id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
import sqlite3


def env_token() -> str | None:
    return os.getenv("LOBS_API_TOKEN") or os.getenv("MISSION_CONTROL_TOKEN")


def db_token(db_path: str = "data/lobs.db") -> str | None:
    try:
        con = sqlite3.connect(db_path)
        row = con.execute("select token from api_tokens where active=1 order by created_at desc limit 1").fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def request(method: str, base: str, path: str, token: str | None, payload: Any | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, f"{base}{path}", headers=headers, json=payload, timeout=30)
    if not resp.ok:
        print(resp.text, file=sys.stderr)
        resp.raise_for_status()
    if resp.text:
        return resp.json()
    return None


def strip_read_only(doc: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "name",
        "description",
        "nodes",
        "edges",
        "trigger",
        "metadata",
        "is_active",
    }
    return {k: v for k, v in doc.items() if k in keep}


def cmd_list(args):
    data = request("GET", args.base, "/api/workflows?active_only=false", args.token)
    for wf in data:
        print(f"{wf['id']}\t{wf['name']}\tv{wf['version']}\tactive={wf.get('is_active')}")


def cmd_get(args):
    data = request("GET", args.base, f"/api/workflows/{args.id}", args.token)
    print(json.dumps(data, indent=2))


def cmd_export(args):
    data = request("GET", args.base, f"/api/workflows/{args.id}", args.token)
    payload = strip_read_only(data)
    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out}")


def cmd_apply(args):
    payload = json.loads(Path(args.file).read_text())

    if args.id:
        data = request("PUT", args.base, f"/api/workflows/{args.id}", args.token, payload)
        print(f"updated {data['id']} v{data['version']} {data['name']}")
        return

    # upsert by name
    all_wf = request("GET", args.base, "/api/workflows?active_only=false", args.token)
    existing = next((w for w in all_wf if w.get("name") == payload.get("name")), None)
    if existing:
        data = request("PUT", args.base, f"/api/workflows/{existing['id']}", args.token, payload)
        print(f"updated {data['id']} v{data['version']} {data['name']}")
    else:
        data = request("POST", args.base, "/api/workflows", args.token, payload)
        print(f"created {data['id']} v{data['version']} {data['name']}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default=os.getenv("LOBS_API_BASE", "http://localhost:8000"))
    p.add_argument("--token", default=env_token() or db_token())

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    g = sub.add_parser("get")
    g.add_argument("--id", required=True)

    e = sub.add_parser("export")
    e.add_argument("--id", required=True)
    e.add_argument("--out", required=True)

    a = sub.add_parser("apply")
    a.add_argument("--file", required=True)
    a.add_argument("--id")

    args = p.parse_args()

    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "get":
        cmd_get(args)
    elif args.cmd == "export":
        cmd_export(args)
    elif args.cmd == "apply":
        cmd_apply(args)


if __name__ == "__main__":
    main()
