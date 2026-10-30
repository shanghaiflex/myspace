# Background AI jobs

Since 30 October 2026, all BoW background generation uses Codex CLI through
`scripts/codex_ask.py`: health notes, weekly reviews, mixes, films, books,
lectures, articles, races (extraction and selection), French (all three passes),
recipes, and dish photo selection. Existing launchd schedules and web refresh
buttons call the same shell scripts as before.

The default model is `gpt-6.1-sol`; race extraction uses `gpt-6-luna`. Configure
`BOW_CODEX_MODEL` and `BOW_CODEX_EXTRACT_MODEL` in the mini's `~/movies/.env`.
Existing per-job variables (`HEALTH_MODEL`, `MIX_RECS_MODEL`, `TASTE_RECS_MODEL`,
`READS_MODEL`, `RACES_MODEL`, `RACES_EXTRACT_MODEL`, `FRENCH_MODEL`, `PANTRY_MODEL`)
still override them. Old `opus`/`sonnet`/`haiku` aliases resolve to the corresponding
OpenAI model, so a stale local setting cannot select an Anthropic model. Output
metadata records the resolved model. `BOW_CODEX_TIMEOUT` defaults to 600 seconds.

The runner uses an ephemeral temporary workspace and read-only sandbox, ignores
user configuration and project instructions, and disables shell, plugins, apps,
MCP configuration, browsing, and subagents. It receives the existing digest via
stdin and returns only the final answer, never progress output. Failed, empty,
invalid JSON, or timed-out responses exit nonzero before the caller applies them.
Photos are attached with `--image` and an ordered filename list; no file tool is
needed. Existing catalog validators, source checks, quotas, and feedback remain
in the Python modules. Historical `claude-rec` tags and the `claude` lecture channel
ID remain stable to preserve existing data; visible labels say Codex.

## Mini setup and authentication

Install a native Codex CLI at `~/.local/bin/codex` (the mini is macOS arm64 and
has no Homebrew or developer tools). `codex login` authenticates with ChatGPT;
`codex login status` checks it. For this private trusted runner, the existing
laptop ChatGPT CLI credentials were transferred over SSH to the mini's
`~/.codex/auth.json`, mode 0600, directory mode 0700. Codex refreshes them there.
Never commit, print, or sync this file through the public repository. No
Anthropic token is needed. If authentication expires, sign in again on the mini.

The CLI must support `exec --ignore-user-config --ephemeral --output-last-message`;
the migration uses the same native CLI build as the laptop. When copying only the
binary out of the desktop app bundle, re-sign the detached executable on the mini
with `codesign --force --sign - ~/.local/bin/codex`; otherwise macOS rejects its
bundle signature (exit 137). To verify without
changing any catalogs or health data:

```sh
ssh mini 'printf '\''Return exactly {"ok":true} as JSON.'\'' | ~/.local/python312/bin/python3 ~/movies/scripts/codex_ask.py --json'
```

Health can be checked end to end with
`ssh mini 'cd movies && sh scripts/health_review.sh --force'`. Write manual run
output to the terminal, not `logs/*.log`; launchd must own those logs (see
`docs/deploy.md`). Never execute a real investment plan just to test this migration.

## Investment strategy

Both investment jobs (`ru.invest.morning` and `ru.invest.evening`) run
`~/invest/scripts/daily.sh` → `uv run invest plan --execute`. This is a Python
strategy without any Claude or other LLM call. Its account mode, STOP switch,
schedule, positions, and trading rules are unaffected. The BoW page reads its
result files; see `docs/invest.md`.

Official references: [non-interactive Codex](https://learn.chatgpt.com/docs/non-interactive-mode),
[CLI options](https://learn.chatgpt.com/docs/cli/reference), and
[model availability](https://learn.chatgpt.com/docs/models).
