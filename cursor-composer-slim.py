#!/usr/bin/env python3
"""Slim Cursor Composer chat storage: keep useful text, drop agent tape.

Composer `~/.cursor/chats/**/store.db` is mostly tool results and execution
plumbing (~95%). User/assistant text is ~1–4%. This tool:

  1. Exports user + assistant turns to JSONL under ~/Archives/cursor-training/
  2. Deletes store.db (+ wal/shm), keeping meta.json / prompt_history.json
  3. Thins local Time Machine snapshots so APFS returns the space

Agent transcripts under ~/.cursor/projects/**/agent-transcripts/ are left
in place (already a slim JSONL corpus).

Commands:
  status            Disk + Cursor hotspots
  audit             Classify store.db blob mix (sample)
  export            Write user/assistant JSONL archive
  prune             Delete store.db after EXPORT_OK
  slim              Export → prune → reclaim (one shot)
  reclaim           Thin local TM snapshots only

Destructive steps need --apply.

Examples:
  python3 cursor-composer-slim.py status
  python3 cursor-composer-slim.py slim --apply
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Set, TextIO, Tuple

HOME = Path.home()
DATA_VOL = Path("/System/Volumes/Data")
CHATS = HOME / ".cursor" / "chats"
PROJECTS = HOME / ".cursor" / "projects"
DEFAULT_ARCHIVE_ROOT = HOME / "Archives" / "cursor-training"
STATE_VSCDB = (
    HOME
    / "Library"
    / "Application Support"
    / "Cursor"
    / "User"
    / "globalStorage"
    / "state.vscdb"
)

# Roles kept by default for training / readable history.
KEEP_ROLES_DEFAULT = frozenset({"user", "assistant"})
# Opt-in via --include-system (often Cursor boilerplate).
KEEP_ROLES_WITH_SYSTEM = frozenset({"user", "assistant", "system"})


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def human(n: float) -> str:
    """Format a byte count as a short human string (B, Ki, Mi, Gi, Ti)."""
    units = ["B", "Ki", "Mi", "Gi", "Ti"]
    x = float(n)
    for u in units:
        if abs(x) < 1024 or u == units[-1]:
            return f"{x:.1f} {u}"
        x /= 1024
    return f"{n} B"


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    """Run a shell command. Capture text stdout/stderr. Do not raise on failure."""
    return subprocess.run(cmd, text=True, capture_output=True)


def hostname() -> str:
    """Return the short host name used in archive paths."""
    return socket.gethostname().split(".")[0] or platform.node() or "host"


def df_data() -> Tuple[str, str, str]:
    """Return (used, avail, capacity) strings for the macOS Data volume.

    Parses `df -h /System/Volumes/Data`. Returns ("?", "?", "?") on failure.
    """
    out = run(["df", "-h", str(DATA_VOL)]).stdout.strip().splitlines()
    if len(out) < 2:
        return "?", "?", "?"
    parts = out[1].split()
    return parts[2], parts[3], parts[4]


def du_bytes(path: Path) -> int:
    """Return allocated size of a path via `du -sk` (bytes).

    Prefer this for directories and sparse files. Returns 0 if missing or
    if `du` fails.
    """
    if not path.exists():
        return 0
    cp = run(["du", "-sk", str(path)])
    if cp.returncode != 0 or not cp.stdout.strip():
        return 0
    try:
        return int(cp.stdout.split()[0]) * 1024
    except (ValueError, IndexError):
        return 0


def path_size(path: Path) -> int:
    """Return size of a file (`st_size`) or directory (`du_bytes`).

    Returns 0 if the path does not exist.
    """
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    return du_bytes(path)


def cursor_app_running() -> bool:
    """True if the Cursor.app GUI process is running on this Mac."""
    cp = run(["pgrep", "-f", "/Applications/Cursor.app/Contents/MacOS/Cursor"])
    return cp.returncode == 0 and bool(cp.stdout.strip())


def warn_cursor() -> None:
    """Print a one-line warning when Cursor.app is open during export/prune."""
    if cursor_app_running():
        print(
            "Note: Cursor.app is running. Export uses immutable reads. "
            "Prune deletes store.db; active chats may rewrite some files."
        )


def archive_run_dir(root: Path) -> Path:
    """Create and return `root/<hostname>/<UTC-stamp>/` for one export run."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = root.expanduser() / hostname() / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_archive_readme(out_dir: Path, manifest: dict) -> None:
    """Write a short README.md into the archive folder for humans."""
    (out_dir / "README.md").write_text(
        f"""# Cursor Composer extract — {manifest.get('host')}

Created: {manifest.get('created')} (UTC)

## Keep

- `composer-useful.jsonl` — user + assistant text from `store.db`
- `composer-useful.manifest.json` — counts
- `agent-transcripts.index.json` — paths to live JSONL under `~/.cursor/projects`

## Dropped

Tool results and binary/protobuf agent plumbing (~95% of `store.db`).

## Tool

https://github.com/raro42/cursor-composer-slim
"""
    )


def remove_path(path: Path, apply: bool, quiet: bool = False) -> int:
    """Delete a file or directory when apply is True.

    Args:
        path: File or directory to remove.
        apply: If False, only print a dry-run line (unless quiet).
        quiet: If True, do not print each path.

    Returns:
        Size in bytes of the path before delete (0 if missing).
    """
    n = path_size(path)
    if n == 0 and not path.exists():
        return 0
    if not quiet:
        print(f"  [{'DELETE' if apply else 'DRY'}] {human(n):>10}  {path}")
    if apply and path.exists():
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except OSError as e:
                if not quiet:
                    print(f"    warn: {e}")
    return n


# ---------------------------------------------------------------------------
# store.db access and classification
# ---------------------------------------------------------------------------


def open_store_readonly(store: Path) -> Optional[sqlite3.Connection]:
    """Open a Composer store.db for read-only export.

    Tries SQLite URI `immutable=1` first so a stale `-wal` lock does not block.
    Falls back to a temp copy of the main file only.

    Returns:
        An open connection, or None if the DB cannot be opened.
        Temp-copy connections stash the temp path on `con._ccs_tmp` for cleanup.
    """
    for uri in (
        f"file:{store}?mode=ro&immutable=1",
        f"file:{store}?mode=ro",
    ):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=5)
            con.execute("SELECT 1 FROM blobs LIMIT 1")
            return con
        except sqlite3.Error:
            try:
                con.close()
            except Exception:
                pass
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix="store-", suffix=".db")
        os.close(fd)
        shutil.copy2(store, tmp)
        con = sqlite3.connect(f"file:{tmp}?mode=ro&immutable=1", uri=True, timeout=5)
        con.execute("SELECT 1 FROM blobs LIMIT 1")
        con._ccs_tmp = tmp  # type: ignore[attr-defined]
        return con
    except Exception:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return None


def close_store(con: Optional[sqlite3.Connection]) -> None:
    """Close a store connection and remove any temp copy from open_store_readonly."""
    if not con:
        return
    tmp = getattr(con, "_ccs_tmp", None)
    try:
        con.close()
    except Exception:
        pass
    if tmp:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def classify_blob(data: bytes) -> Tuple[str, Optional[dict]]:
    """Classify one `blobs.data` row from store.db.

    Returns:
        (kind, parsed_dict_or_None). Kinds include:
        `json_user`, `json_assistant`, `json_system`, `json_tool`,
        `json_other`, `json_bad`, `textish`, `binary`, `empty`.

        Only JSON role messages return a dict. textish/binary return None.
    """
    if not data:
        return "empty", None
    if data[:1] in (b"{", b"["):
        try:
            obj = json.loads(data)
        except Exception:
            return "json_bad", None
        if isinstance(obj, dict) and obj.get("role") in (
            "user",
            "assistant",
            "system",
            "tool",
        ):
            return f"json_{obj['role']}", obj
        return "json_other", obj if isinstance(obj, dict) else None
    sample = data[:200]
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    if printable / max(len(sample), 1) > 0.85:
        return "textish", None
    return "binary", None


def iter_store_dbs() -> Iterator[Path]:
    """Yield every `~/.cursor/chats/<workspace>/<chat>/store.db` path.

    Order is sorted by workspace, then chat folder. Yields nothing if CHATS
    is missing.
    """
    if not CHATS.is_dir():
        return
    for ws in sorted(CHATS.iterdir()):
        if not ws.is_dir():
            continue
        for store in sorted(ws.glob("*/store.db")):
            yield store


def extract_text(content) -> str:
    """Pull plain text out of a message `content` field.

    Handles a bare string or a list of parts with `type: text` / `text` keys.
    Returns "" for other shapes.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text") or "")
            elif isinstance(p, dict) and "text" in p:
                parts.append(str(p.get("text") or ""))
        return "\n".join(parts)
    return ""


def export_one_store(
    store: Path, out_fp: TextIO, keep_roles: Set[str]
) -> Tuple[int, int]:
    """Export kept roles from one store.db into an open JSONL file.

    Args:
        store: Path to store.db.
        out_fp: Open text file for JSONL lines.
        keep_roles: Roles to keep (usually user + assistant).

    Returns:
        (kept_count, skipped_count). Both 0 if the DB could not be opened.
    """
    con = open_store_readonly(store)
    if not con:
        return 0, 0
    kept = skipped = 0
    meta_path = store.parent / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            meta = {}
    try:
        for (data,) in con.execute("SELECT data FROM blobs"):
            data = bytes(data)
            kind, obj = classify_blob(data)
            if not obj or not kind.startswith("json_"):
                skipped += 1
                continue
            role = obj.get("role")
            if role not in keep_roles:
                skipped += 1
                continue
            text = extract_text(obj.get("content"))
            if not text.strip():
                skipped += 1
                continue
            rec = {
                "role": role,
                "text": text,
                "source": "composer-store",
                "host": hostname(),
                "workspace": store.parents[1].name,
                "chat": store.parent.name,
                "title": meta.get("title"),
                "cwd": meta.get("cwd"),
            }
            out_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kept += 1
    except sqlite3.Error:
        pass
    finally:
        close_store(con)
    return kept, skipped


def index_agent_transcripts(out_dir: Path) -> dict:
    """Write agent-transcripts.index.json (paths + sizes only; no file copy).

    Scans `~/.cursor/projects/*/agent-transcripts/**/*.jsonl`.

    Returns:
        The index dict written to disk.
    """
    items = []
    total = 0
    for p in sorted(PROJECTS.glob("*/agent-transcripts/**/*.jsonl")):
        if not p.is_file():
            continue
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        total += sz
        items.append({"path": str(p), "bytes": sz})
    idx = {
        "host": hostname(),
        "count": len(items),
        "bytes": total,
        "note": "Files left under ~/.cursor/projects; paths only.",
        "files": items,
    }
    (out_dir / "agent-transcripts.index.json").write_text(json.dumps(idx, indent=2) + "\n")
    return idx


# ---------------------------------------------------------------------------
# Core workflows
# ---------------------------------------------------------------------------


def do_export(
    out_dir: Path,
    include_system: bool = False,
    limit: int = 0,
    verbose: bool = False,
) -> Tuple[int, dict]:
    """Export all (or limited) store.db files into out_dir.

    Writes composer-useful.jsonl, manifest, EXPORT_OK, archive README, and
    updates hostname/LATEST. Also indexes agent-transcript paths.

    Args:
        out_dir: Archive run directory (created if needed).
        include_system: Keep system prompts as well as user/assistant.
        limit: Max stores to open (0 = all).
        verbose: Print per-chat kept/skipped lines.

    Returns:
        (exit_code, manifest_dict). exit_code 1 if stores exist but none kept.
    """
    warn_cursor()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "composer-useful.jsonl"
    keep_roles = KEEP_ROLES_WITH_SYSTEM if include_system else KEEP_ROLES_DEFAULT
    manifest = {
        "created": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "host": hostname(),
        "keep_roles": sorted(keep_roles),
        "chats_root": str(CHATS),
        "archive_dir": str(out_dir),
        "kept": 0,
        "skipped": 0,
        "stores": 0,
        "failed_open": 0,
        "store_db_before_bytes": du_bytes(CHATS),
        "tool": "cursor-composer-slim",
    }
    stores = list(iter_store_dbs())
    total_planned = len(stores) if not limit else min(limit, len(stores))
    print(f"Export → {out_file}")
    print(f"Host={hostname()}  keep={sorted(keep_roles)}  stores≈{total_planned}")
    t0 = time.time()
    with out_file.open("w", encoding="utf-8") as fp:
        for store in stores:
            if limit and manifest["stores"] >= limit:
                break
            kept, skipped = export_one_store(store, fp, keep_roles)
            if kept == 0 and skipped == 0:
                manifest["failed_open"] += 1
            else:
                manifest["stores"] += 1
                manifest["kept"] += kept
                manifest["skipped"] += skipped
            done = manifest["stores"] + manifest["failed_open"]
            if verbose:
                print(f"  {store.parent.name}: kept={kept} skipped={skipped}")
            elif done % 100 == 0 and done:
                print(
                    f"  … {done}/{total_planned}  kept={manifest['kept']}  "
                    f"{time.time()-t0:.0f}s"
                )
    idx = index_agent_transcripts(out_dir)
    manifest["agent_transcript_files"] = idx["count"]
    manifest["agent_transcript_bytes"] = idx["bytes"]
    (out_dir / "composer-useful.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    (out_dir / "EXPORT_OK").write_text(manifest["created"] + "\n")
    latest = out_dir.parent / "LATEST"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(out_dir.name)
    except OSError:
        latest.write_text(out_dir.name + "\n")
    write_archive_readme(out_dir, manifest)
    print(
        f"Done in {time.time()-t0:.0f}s: stores={manifest['stores']} "
        f"kept={manifest['kept']} failed_open={manifest['failed_open']}"
    )
    print(f"Archive: {out_dir}")
    if manifest["kept"] < 1 and total_planned > 0:
        print("ERROR: no useful turns exported.")
        return 1, manifest
    if total_planned == 0:
        print("No store.db files found (already slim, or empty).")
        (out_dir / "EXPORT_OK").write_text(manifest["created"] + "\n")
    return 0, manifest


def do_prune(export_dir: Path, apply: bool, force: bool = False) -> int:
    """Delete store.db (+ wal/shm) after a successful export.

    Requires EXPORT_OK in export_dir unless force is True. Keeps meta.json and
    prompt_history.json. On apply, runs reclaim and writes PRUNE_OK.json.

    Returns:
        0 on success, 2 if EXPORT_OK is missing and force is False.
    """
    marker = export_dir / "EXPORT_OK"
    if not marker.exists() and not force:
        print(f"Refuse prune: missing {marker}")
        return 2
    warn_cursor()
    before = du_bytes(CHATS)
    print(f"Prune ({'APPLY' if apply else 'dry-run'}) on {hostname()}")
    print(f"Export dir: {export_dir}")
    print(f"Chats before: {human(before)}")
    total = 0
    n_files = 0
    n_stores = 0
    for store in iter_store_dbs():
        n_stores += 1
        for suf in ("", "-wal", "-shm"):
            p = Path(str(store) + suf) if suf else store
            if p.exists():
                total += remove_path(p, apply, quiet=True)
                n_files += 1
        if n_stores % 500 == 0:
            print(f"  … {n_stores} chats scanned")
    print(f"Stores: {n_stores}  files: {n_files}  bytes: {human(total)}")
    if apply:
        cmd_reclaim(argparse.Namespace(bytes=10**12))
        after = du_bytes(CHATS)
        print(f"Chats after: {human(after)}")
        used, avail, cap = df_data()
        print(f"Data volume: used={used} free={avail} capacity={cap}")
        rec = {
            "pruned_at": datetime.now(timezone.utc).isoformat(),
            "host": hostname(),
            "bytes_removed_estimate": total,
            "chats_before_bytes": before,
            "chats_after_bytes": after,
        }
        (export_dir / "PRUNE_OK.json").write_text(json.dumps(rec, indent=2) + "\n")
    else:
        print("Re-run with --apply to delete.")
    return 0


def resolve_export_dir(path: str) -> Path:
    """Resolve a prune/export path to a concrete run directory with EXPORT_OK.

    Accepts:
      - a run dir that already has EXPORT_OK
      - a host dir with LATEST → stamp
      - the archive root with <hostname>/LATEST

    Returns the input path unchanged if nothing resolves.
    """
    p = Path(path).expanduser()
    if (p / "EXPORT_OK").exists():
        return p
    latest = p / "LATEST"
    if latest.exists():
        name = os.readlink(latest) if latest.is_symlink() else latest.read_text().strip()
        cand = p / name
        if (cand / "EXPORT_OK").exists():
            return cand
    host_latest = p / hostname() / "LATEST"
    if host_latest.exists():
        name = (
            os.readlink(host_latest)
            if host_latest.is_symlink()
            else host_latest.read_text().strip()
        )
        cand = p / hostname() / name
        if (cand / "EXPORT_OK").exists():
            return cand
    return p


# ---------------------------------------------------------------------------
# CLI command handlers (argparse entry points)
# ---------------------------------------------------------------------------


def cmd_status(_: argparse.Namespace) -> int:
    """CLI `status`: print disk free space and Cursor storage hotspots."""
    used, avail, cap = df_data()
    print(f"Host: {hostname()}")
    print(f"Data volume: used={used}  free={avail}  capacity={cap}")
    print(f"Cursor.app running: {cursor_app_running()}")
    print()
    rows = [
        ("Composer chats", CHATS),
        ("Cursor projects (transcripts)", PROJECTS),
        ("state.vscdb (IDE KV)", STATE_VSCDB),
    ]
    print(f"{'Hotspot':<36} {'Size':>10}")
    for label, p in rows:
        sz = du_bytes(p) if p.is_dir() or p.name.endswith(".vscdb") else path_size(p)
        if p.suffix == ".vscdb" or p.name == "state.vscdb":
            sz = du_bytes(p) if p.exists() else 0
            if p.is_file():
                try:
                    sz = p.stat().st_size
                except OSError:
                    sz = 0
        print(f"{label:<36} {human(sz):>10}  {p}")
    n_stores = sum(1 for _ in CHATS.glob("*/*/store.db")) if CHATS.is_dir() else 0
    print(f"\nComposer store.db count: {n_stores}")
    print("\nAgent transcripts stay on disk. This tool only slims store.db.")
    print(
        "state.vscdb needs Cursor UI: Export Chat → Delete Old Chats → "
        "GC Agent KV Blobs → Quit."
    )
    return 0


def cmd_reclaim(args: argparse.Namespace) -> int:
    """CLI `reclaim`: thin/delete local Time Machine snapshots so APFS frees bytes.

    Args:
        args.bytes: Urgency hint passed to `tmutil thinlocalsnapshots`.
    """
    nbytes = getattr(args, "bytes", 10**12)
    print(f"Thin local snapshots (urge={nbytes}) …")
    cp = run(["tmutil", "thinlocalsnapshots", "/", str(nbytes), "4"])
    if cp.stdout.strip():
        print(cp.stdout.strip())
    if cp.stderr.strip():
        print(cp.stderr.strip())
    dates = run(["tmutil", "listlocalsnapshotdates", "/"]).stdout.splitlines()
    for line in dates:
        line = line.strip()
        if line.startswith("20"):
            print(f"Delete snapshot {line}")
            run(["tmutil", "deletelocalsnapshots", line])
    used, avail, cap = df_data()
    print(f"Data volume: used={used} free={avail} capacity={cap}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    """CLI `audit`: sample store.db blobs and print kind / size shares.

    Args:
        args.limit: Max number of successfully opened store.db files.
    """
    print("Composer store.db audit")
    print("Keep for training: json_user + json_assistant")
    print("Bloat: json_tool, textish, binary\n")
    counts: Counter = Counter()
    nbytes: Counter = Counter()
    files = 0
    failed = 0
    for store in iter_store_dbs():
        if args.limit and files >= args.limit:
            break
        con = open_store_readonly(store)
        if not con:
            failed += 1
            continue
        files += 1
        try:
            for (data,) in con.execute("SELECT data FROM blobs"):
                data = bytes(data)
                kind, _ = classify_blob(data)
                counts[kind] += 1
                nbytes[kind] += len(data)
        except sqlite3.Error:
            failed += 1
        finally:
            close_store(con)
    tot = sum(nbytes.values()) or 1
    print(f"Opened {files} store.db  (open-fail {failed})")
    print(f"{'kind':<18} {'count':>8} {'bytes':>12} {'share':>7}")
    for kind, n in nbytes.most_common():
        print(f"{kind:<18} {counts[kind]:8d} {human(n):>12} {100 * n / tot:6.1f}%")
    useful = (
        nbytes.get("json_user", 0)
        + nbytes.get("json_assistant", 0)
        + nbytes.get("json_system", 0)
    )
    print(
        f"\nKeep share (user+assistant+system): {100 * useful / tot:.1f}%  "
        f"({human(useful)})"
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """CLI `export`: create a new archive run and export useful JSONL."""
    out_dir = archive_run_dir(Path(args.out).expanduser())
    code, _ = do_export(
        out_dir,
        include_system=args.include_system,
        limit=args.limit,
        verbose=args.verbose,
    )
    return code


def cmd_prune(args: argparse.Namespace) -> int:
    """CLI `prune`: delete store.db after EXPORT_OK (dry-run unless --apply)."""
    return do_prune(
        resolve_export_dir(args.export_dir), apply=args.apply, force=args.force
    )


def cmd_slim(args: argparse.Namespace) -> int:
    """CLI `slim`: one shot — export, then prune (and reclaim if --apply)."""
    root = Path(args.out).expanduser()
    print(f"=== cursor-composer-slim on {hostname()} ===")
    used, avail, cap = df_data()
    print(f"Before: used={used} free={avail} capacity={cap}")
    print(f"Composer chats: {human(du_bytes(CHATS))}")
    if not args.apply:
        print("Dry-run prune after export. Pass --apply to delete store.db.")
    out_dir = archive_run_dir(root)
    code, _ = do_export(
        out_dir,
        include_system=args.include_system,
        limit=args.limit,
        verbose=args.verbose,
    )
    if code != 0:
        return code
    return do_prune(out_dir, apply=args.apply, force=False)


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree and wire each subcommand to its handler."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="Disk + Cursor hotspots")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("audit", help="Classify store.db blob mix")
    s.add_argument("--limit", type=int, default=80)
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("export", help="Export user/assistant JSONL")
    s.add_argument("--out", default=str(DEFAULT_ARCHIVE_ROOT))
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--include-system", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("prune", help="Delete store.db after export")
    s.add_argument("--export-dir", default=str(DEFAULT_ARCHIVE_ROOT))
    s.add_argument("--apply", action="store_true")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_prune)

    s = sub.add_parser("slim", help="Export + prune + reclaim")
    s.add_argument("--out", default=str(DEFAULT_ARCHIVE_ROOT))
    s.add_argument("--apply", action="store_true")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--include-system", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_slim)

    s = sub.add_parser("reclaim", help="Thin local TM snapshots")
    s.add_argument("--bytes", type=int, default=10**12)
    s.set_defaults(func=cmd_reclaim)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Parse argv and run the selected subcommand. Return its exit code."""
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
