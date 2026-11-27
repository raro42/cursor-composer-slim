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

Examples ([forum.cursor.com](https://forum.cursor.com)):

| Report | Link |
| --- | --- |
| `state.vscdb` 3.1 GB → 97 GB (macOS) | https://forum.cursor.com/t/state-vscdb-grew-from-3-1-gb-97-gb-macos/166013 |
| Agent chat DB grows without bound (~96 GB) | https://forum.cursor.com/t/agent-chat-db-state-vscdb-grows-without-bound-50-bubble-rows-per-visible-message-96-gb-and-climbing/170508 |
| Cursor storage ~125 GB on Mac | https://forum.cursor.com/t/cursor-storage-has-grown-to-125gb-on-my-mac-how-can-i-safely-reduce-it-without-losing-history-context/172127 |
| Unlimited DB bloat (staff: no auto retention) | https://forum.cursor.com/t/have-cursor-solved-the-problem-of-unlimitedly-bloat-db-and-slowdown-for-long-conversations/171972 |
| Excessive storage / ~38 GB `state.vscdb` | https://forum.cursor.com/t/excessive-storage-in-macbook/173914 |
| Feature request: storage caps | https://forum.cursor.com/t/add-storage-caps-for-cursors-chat-agent-database/176074 |
| Safely reduce 237 GB `state.vscdb` | https://forum.cursor.com/t/how-to-safely-reduce-a-237-gb-state-vscdb-with-only-46-gb-free/177244 |
| Remove local archived chats | https://forum.cursor.com/t/how-to-remove-local-archived-chats/158574 |

Full list also in the [README](../README.md#forum-reports-same-class-of-problem).

Staff answers (paraphrase):

- No automatic retention for a long time; cleanup was manual (`Delete Old Chats…`, `GC Agent KV Blobs`, full Quit).
- Growth is a **known tracked issue**, not user misconfiguration.
- Auto-expire / size caps were “on the radar” / backlog with **no ETA**.
- IDE 3.25+ started reclaiming unused state-DB space on quit and “Rewrite Now” after Delete Old Chats — still not a full “keep only useful text” policy.

Official UI tools target mainly **`state.vscdb`**. The separate `~/.cursor/chats/**/store.db` trees (this repo) are the same class of problem: fat agent tape, weak retention. That is why a small export+prune tool still helps.

## Open tip

Read with SQLite `immutable=1` so a stale `-wal` lock does not block export.
