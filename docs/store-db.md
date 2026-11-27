# What is inside Composer `store.db`

## Short answer

Most bytes are the **agent session tape** (tool I/O + execution graph). A small slice is the human-readable dialogue.

## Measured mix (typical heavy chat)

| Kind | Share of bytes | Keep for training / history? |
| --- | --- | --- |
| `textish` / `binary` | ~55–60% | No — protobuf-ish calls, thoughts, IDs |
| `json_tool` | ~30–42% | No — full Shell/Read/Grep results |
| `json_user` + `json_assistant` | **~1–4%** | **Yes** |
| `json_system` | ≪1% | Usually product boilerplate |

Example: one ~381 MB chat held ~4 MB of user/assistant/system JSON (~1.1%).

## Does Cursor need the ~95%?

- **Yes while the chat is live / resumable.** The model must see prior tool output.
- **No forever for “chat history” as you read it.** Prefer `agent-transcripts/*.jsonl` or this tool’s export.

## Why so unstructured?

Cursor uses an **append-only blob store** (conversation DAG) for agent fidelity, not a small messages-only table with TTL. Slimming locally is reasonable until product retention improves.

## You are not alone

Many heavy Agent users hit the same wall. Cursor staff say so on the forum.

Examples (forum.cursor.com):

| Report | Size |
| --- | --- |
| `state.vscdb` 3.1 GB → 97 GB | macOS |
| Agent DB unbounded / ~96 GB climbing | self-fork copies |
| Storage ~125 GB | Mac |
| `state.vscdb` ~237 GB, only 46 GB free | GC needs ~2× free space |
| Feature request: storage caps | auto-purge still backlog |

Staff answers (paraphrase):

- No automatic retention for a long time; cleanup was manual (`Delete Old Chats…`, `GC Agent KV Blobs`, full Quit).
- Growth is a **known tracked issue**, not user misconfiguration.
- Auto-expire / size caps were “on the radar” / backlog with **no ETA**.
- IDE 3.25+ started reclaiming unused state-DB space on quit and “Rewrite Now” after Delete Old Chats — still not a full “keep only useful text” policy.

Official UI tools target mainly **`state.vscdb`**. The separate `~/.cursor/chats/**/store.db` trees (this repo) are the same class of problem: fat agent tape, weak retention. That is why a small export+prune tool still helps.

## Open tip

Read with SQLite `immutable=1` so a stale `-wal` lock does not block export.
