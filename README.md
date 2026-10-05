# Repository Development Agent

[![Tool checks](https://github.com/Sevyn1/repo-dev-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Sevyn1/repo-dev-agent/actions/workflows/ci.yml)

A Python CLI development assistant built around the OpenAI Agents SDK. It discovers a selected repository, reads bounded source files, proposes reviewed patches, runs operator-approved commands, and can analyze an approved screenshot with the Responses API. Project context and SQLite session memory persist between live sessions.

Recovered from existing personal developer tooling and repaired with Codex assistance for publication. This repository contains the generic assistant only—no application source, credentials, personal prompts, session databases, or project screenshots.

## Run without a key

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python dev_agent.py --check
```

`--check` lists a small set of current-project files. It makes no model calls and creates no state directory. The test suite covers the actual file tools, patch behavior, shell approval, image request shape, CLI startup, and SQLite persistence.

## Live mode

Run the script from the repository you intend to work on. Configure `OPENAI_API_KEY` in the environment or that repository's `.env` file, then run:

```sh
python /path/to/repo-dev-agent/dev_agent.py
```

The default model remains `gpt-4.1-mini`. Live mode sends user prompts and selected tool results to OpenAI and may incur usage charges. Do not use it on code you are not authorized to share with the provider.

Edit `.dev_agent/<project>_prompt.txt` to describe your repository and constraints. Session memory, history, and backups are kept under the selected repository's `.dev_agent` directory; keep that directory out of version control. The included ignore rules cover it when this repository is used directly.

Patches display their complete bounded diff for operator approval and preserve a backup. Shell execution and image transmission also require an explicit `y`; refusal leaves the operation unperformed.

## Repairs made during the audit

- Resolve paths before checking containment; reject sibling-prefix and symlink escapes.
- List root files correctly and prune dependency/state directories.
- Block common credential paths from file tools and bound text reads.
- Replace against the original content once, avoiding repeated replacement of newly inserted text.
- Reject empty/ambiguous patches, preserve edits made during review, and back up before writing.
- Use a string data URL and the appropriate image MIME type in the Responses request.
- Defer live initialization so offline checks do not need an API key.
- Provide SQLiteSession with an explicit database path so memory survives process restarts.

The image request follows the [official OpenAI Python API example](https://developers.openai.com/api/reference/python), where `input_image.image_url` is a URL/data-URL string.

## Boundaries

File-tool restrictions are not an operating-system sandbox. An approved shell command runs with the operator's permissions and can access resources beyond the repository. The common credential deny list is not a complete data-loss-prevention system. Review commands and patches; use an isolated working copy for experiments.

Provider behavior is mocked in the image test. No live model calls were made during verification, so model quality and end-to-end API execution are not claimed as tested.

See [verification](docs/VERIFICATION.md) and [design decisions](docs/DESIGN_DECISIONS.md). The source preserves the original CLI feedback, context-file workflow, and tool architecture while recording the publication repairs separately.
