# Privacy scrub of committed artifacts (2026-10-05)

This repository is public. Several committed result artifacts recorded machine-local details from
the machine that ran B6–B8 and the Step-7 / call-collapse / executor studies. Those details were
pseudonymized in place. **No measured value changed.**

This is a deliberate exception to the house rule that frozen artifacts are never edited silently.
It is documented here, and the values are untouched.

## What changed

| Pseudonymized | Replacement | Where |
|---|---|---|
| The recording machine's home directory, including the `-Users-<name>-` project-slug form | `/Users/<user>` and `-Users-<user>-` | 10 files under `corpus/analysis/`, plus `papers/generated/results_summary.json` |
| One company MCP server name in the B8 admission list | `mcp__<company-mcp>__*` | `b8v2-config-{N,T}.json` |
| 14 B7 interactive-session labels (truncated project-directory names of personal and company projects) | `interactive-01` … `interactive-14`, stable per project | `b7-cache-replay-interactive.json` |

The Django task labels are public SWE-bench identifiers and were kept.

## How it was verified

- **JSON tree diff.** Every scrubbed file was compared with its pre-scrub version as a JSON tree. 612 string leaves changed. There were 0 numeric changes and 0 structural changes (keys, list lengths, types).
- **Paper assets.** `papers/scripts/generate_assets.py` (Python 3.13) regenerates `papers/generated/` byte for byte, except for the one scrubbed label inside `results_summary.json`.
- **Test suite.** `python -m pytest -q` passes: 532 passed, 4 skipped.

## Code that depended on the scrubbed strings

- **`contextruntime.cachemodel.resolve_home`** maps `/Users/<user>/…` onto the local home directory. On the recording machine, `load_b6_sessions` and the B8 harness test therefore still find the B6 transcripts at the same home-relative path.
- **`corpus/b6_live_ab.py`** no longer names the company MCP server. To reproduce the original T arm, set `CR_DISALLOW_EXTRA="mcp__<name>__*"`.

## Keeping it clean

Run `python3 scripts/privacy_sweep.py --staged` before every commit. Built in, it flags:

- real home-directory paths;
- non-noreply e-mail addresses.

It also flags every name listed in the gitignored `.privacy-patterns` file. That file is
deliberately not tracked, because a public list of sensitive names would leak those names. Known
public strings, such as reporters' paths inside SWE-bench issue text, are listed in
`scripts/privacy_allow.txt`.

The previous sweep command, documented in `docs/HANDOVER-2026-08-16.md`, spelled out the names it
was guarding. It has been replaced with this script.

## Not done (owner's decision)

- **Git history.** Earlier commits still contain the original strings. Sixty commits also carry a
  work e-mail as the author address.
- **Rewriting history.** Fixing that needs `git filter-repo` plus a force-push, and every clone,
  including the other working machine's, would have to re-clone. Forks and caches made earlier
  cannot be recalled.
- **Reverse map.** A reverse map of the pseudonyms is kept outside version control on the owner's
  machine.
