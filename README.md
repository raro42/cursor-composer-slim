# cursor-composer-slim

Export the useful part of Cursor Composer chats, then delete the fat `store.db` tape.

**Private for review.** Intended to go public after a pass for secrets and tone.

## Why

Composer stores chats in `~/.cursor/chats/<workspace>/<chat>/store.db`.

| Kind | ~Share | Keep? |
| --- | --- | --- |
| Tool results + binary/protobuf plumbing | ~95% | No (resume tape) |
| `user` + `assistant` JSON | ~1–4% | Yes |

Cursor needs the tape to **resume** an agent. It does not need to keep every Shell/Read dump forever for history or training. A slim JSONL already exists at:

`~/.cursor/projects/*/agent-transcripts/*.jsonl`

This tool exports user/assistant from `store.db`, then prunes those DBs.

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

## Related

Broader Mac disk housekeeping (caches, aerials, Rust `target/`) lives in private notes, not here. This repo is **Cursor-only**.

## License

MIT — see [LICENSE](LICENSE).
