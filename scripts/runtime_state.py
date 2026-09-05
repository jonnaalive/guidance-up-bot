"""Persist non-secret bot state to a separate Git branch; fail closed on read errors.

GitHub Contents API uses SHA compare-and-swap. Workflows additionally serialize runs.
No webhook URLs, environment variables, or source text are included in this archive.
"""
import argparse
import base64
import gzip
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["restore", "save"])
    parser.add_argument("branch", choices=["screening-state", "digest-state"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    session = requests.Session()
    session.headers.update({"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"})
    base = "https://api.github.com/repos/" + os.environ["GITHUB_REPOSITORY"]
    endpoint = base + "/contents/runtime.json.gz"
    response = session.get(endpoint, params={"ref": args.branch}, timeout=30)
    if response.status_code != 404:
        response.raise_for_status()
    current = response.json() if response.ok else None
    metadata = Path(".runtime-" + args.branch + ".json")
    allowed = ["guidance.db", "latest_signals.json"] if args.branch == "screening-state" else ["delivery.json"]
    if args.mode == "restore":
        metadata.write_text(json.dumps({"sha": current["sha"] if current else None}))
        if current:
            if current.get("encoding") != "base64":
                blob = session.get(base + "/git/blobs/" + current["sha"], timeout=30)
                blob.raise_for_status()
                current["content"] = blob.json()["content"]
            raw = base64.b64decode(current["content"])
            payload = json.loads(gzip.decompress(raw))
            if set(payload) - set(allowed) or allowed[0] not in payload:
                raise RuntimeError("Invalid state archive")
            args.directory.mkdir(parents=True, exist_ok=True)
            for name, value in payload.items():
                (args.directory / name).write_bytes(base64.b64decode(value, validate=True))
            if args.branch == "screening-state":
                # A restored authoritative DB must not replay an older cache's WAL.
                for suffix in ("-wal", "-shm"):
                    (args.directory / ("guidance.db" + suffix)).unlink(missing_ok=True)
        if os.getenv("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as out:
                out.write(f"restored={'true' if current else 'false'}\n")
        print("Persistent state restored" if current else "First migration: persistent state does not exist")
        return
    previous = json.loads(metadata.read_text())
    if (current["sha"] if current else None) != previous["sha"]:
        raise RuntimeError("Concurrent state change: refusing to overwrite")
    if not (args.directory / allowed[0]).is_file():
        raise RuntimeError("Required state file missing")
    if args.branch == "screening-state":
        with sqlite3.connect(args.directory / "guidance.db") as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    payload = {name: base64.b64encode((args.directory / name).read_bytes()).decode()
        for name in allowed if (args.directory / name).is_file()}
    compressed = gzip.compress(json.dumps(payload).encode(), mtime=0)
    blob_sha = hashlib.sha1(f"blob {len(compressed)}\0".encode() + compressed).hexdigest()
    if current and current["sha"] == blob_sha:
        print("Persistent state unchanged")
        return
    content = base64.b64encode(compressed).decode()
    if not current:
        ref = session.get(base + "/git/ref/heads/main", timeout=30)
        ref.raise_for_status()
        created = session.post(base + "/git/refs", json={"ref": "refs/heads/" + args.branch,
            "sha": ref.json()["object"]["sha"]}, timeout=30)
        if created.status_code != 422:
            created.raise_for_status()
    body = {"message": "Persist bot delivery state", "branch": args.branch, "content": content}
    if current:
        body["sha"] = current["sha"]
    saved = session.put(endpoint, json=body, timeout=30)
    saved.raise_for_status()
    print("Persistent state saved")


if __name__ == "__main__":
    main()
