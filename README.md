# cursor-composer-slim

Export the useful part of Cursor Composer chats, then delete the fat `store.db` tape.

## Motivation

Heavy Cursor Agent use fills the disk with **agent session tape**, not with the short dialogue you actually read.

Composer writes each chat to `~/.cursor/chats/<workspace>/<chat>/store.db`. Most of that file is tool output and execution plumbing (Shell, Read, Grep, call graphs). Only a thin slice is `user` / `assistant` text. Cursor needs the fat tape to **resume** a live agent. It does not need to keep every dump forever for history or training.

On one Mac notebook, Composer chats alone were about **44 Gi**. After export + prune they were about **25 Mi**. Useful turns kept: about **54k** lines of JSONL. A parallel slim corpus already sits at `~/.cursor/projects/*/agent-transcripts/*.jsonl`.

This is a **known product gap**, not a one-off misconfiguration. Cursor staff on the forum say chat/agent DBs can grow without automatic retention; cleanup was mostly manual for a long time. Official commands focus on `state.vscdb`. This tool targets the separate `store.db` trees under `~/.cursor/chats/`.

### Forum reports (same class of problem)

| Thread | Link |
| --- | --- |
| `state.vscdb` 3.1 GB → 97 GB (macOS) | https://forum.cursor.com/t/state-vscdb-grew-from-3-1-gb-97-gb-macos/166013 |
| Agent chat DB grows without bound (~96 GB) | https://forum.cursor.com/t/agent-chat-db-state-vscdb-grows-without-bound-50-bubble-rows-per-visible-message-96-gb-and-climbing/170508 |
| Cursor storage ~125 GB on Mac | https://forum.cursor.com/t/cursor-storage-has-grown-to-125gb-on-my-mac-how-can-i-safely-reduce-it-without-losing-history-context/172127 |
| Unlimited DB bloat / slowdown (staff: no auto retention yet) | https://forum.cursor.com/t/have-cursor-solved-the-problem-of-unlimitedly-bloat-db-and-slowdown-for-long-conversations/171972 |
| Excessive storage / ~38 GB `state.vscdb` | https://forum.cursor.com/t/excessive-storage-in-macbook/173914 |
| Feature request: storage caps for chat agent DB | https://forum.cursor.com/t/add-storage-caps-for-cursors-chat-agent-database/176074 |
| Safely reduce 237 GB `state.vscdb` (GC needs ~2× free space) | https://forum.cursor.com/t/how-to-safely-reduce-a-237-gb-state-vscdb-with-only-46-gb-free/177244 |
| How to remove local archived chats | https://forum.cursor.com/t/how-to-remove-local-archived-chats/158574 |

IDE 3.25+ improves reclaim on quit for local state DBs. That helps `state.vscdb`. It still does not replace “export useful text, drop the tape” for `store.db`.

## What this tool does

| Kind | ~Share | Keep? |
| --- | --- | --- |
| Tool results + binary/protobuf plumbing | ~95% | No (resume tape) |
| `user` + `assistant` JSON | ~1–4% | Yes |

1. Export user/assistant turns to JSONL under `~/Archives/cursor-training/`
2. Delete `store.db` (+ `-wal` / `-shm`); keep `meta.json` / `prompt_history.json`
3. Thin local Time Machine snapshots so APFS returns the space

## Requirements

- macOS (uses `df`, `du`, `tmutil`)
- Python 3.9+
- Standard library only

## Install

```bash
git clone git@github.com:raro42/cursor-composer-slim.git
cd cursor-composer-slim
chmod +x cursor-composer-slim.py
```

## Usage

```bash
python3 cursor-composer-slim.py status
python3 cursor-composer-slim.py audit --limit 40
python3 cursor-composer-slim.py slim          # export + dry-run prune
python3 cursor-composer-slim.py slim --apply  # export + delete store.db + reclaim
```

Archive layout:

```text
~/Archives/cursor-training/<hostname>/<UTC-stamp>/
  composer-useful.jsonl
  composer-useful.manifest.json
  agent-transcripts.index.json
  README.md
  EXPORT_OK
  PRUNE_OK.json          # after --apply
```

`LATEST` under the hostname folder points at the last run.

## What stays / what goes

| Path | Action |
| --- | --- |
| `store.db` (+ `-wal` / `-shm`) | Deleted after export |
| `meta.json`, `prompt_history.json` | Kept |
| `~/.cursor/projects/**/agent-transcripts/` | Kept (not copied; indexed) |
| `state.vscdb` | Not touched (use Cursor: Export Chat → Delete Old Chats → GC Agent KV Blobs → Quit) |

## Safety

- Default is dry-run for deletes (`--apply` required).
- Prune refuses without `EXPORT_OK` (unless `--force`).
- Scrub the JSONL before any share or public fine-tune (secrets, customer text).
- Active Cursor chats may recreate a few `store.db` files after prune. That is normal.

## Docs

- [docs/store-db.md](docs/store-db.md) — what the blobs are
- [docs/fleet.md](docs/fleet.md) — run on more than one host
- [cursor-composer-slim.py](cursor-composer-slim.py) — each function has a docstring (helpers, store.db I/O, workflows, CLI)

## License

MIT — see [LICENSE](LICENSE).
