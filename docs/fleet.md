# Fleet rollout

Run **one host at a time**. Prefer idle agents (or accept that open chats may rewrite a few `store.db` files).

## Per host

```bash
git clone git@github.com:raro42/cursor-composer-slim.git
cd cursor-composer-slim
python3 cursor-composer-slim.py status
python3 cursor-composer-slim.py slim --apply
```

Confirm:

`~/Archives/cursor-training/<hostname>/LATEST/PRUNE_OK.json`

## Suggested order

1. Dev notebook (done once as the pilot)
2. Lab / build hosts
3. App servers that run `cursor-agent`

Do not prune mid long-running job if you still need to **resume** that exact fat chat.

## Not in scope

- `state.vscdb` — use Cursor UI: Export Chat → Delete Old Chats → GC Agent KV Blobs → full Quit
