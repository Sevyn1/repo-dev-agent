# Verification

The local suite checks sibling-prefix escapes, symlink escapes, common credential paths, root listing, read bounds, patch refusal/backup, replacement loops, ambiguous/empty changes, concurrent edits, review-size limits, shell approval, image transmission refusal/request shape, offline CLI startup, and SDK/SQLite session persistence.

All tests run on temporary sample files. The image response is mocked. No personal application code, private prompts, credentials, or session data is included in this repository. Live OpenAI execution is not verified by these checks.

18 local tests passed. Offline CLI inspection passed without an API key or state creation. SDK tool registration and SQLite persistence used real local components; the image provider was mocked.

Hosted CI also passed: https://github.com/Sevyn1/repo-dev-agent/actions/runs/37266463584.
