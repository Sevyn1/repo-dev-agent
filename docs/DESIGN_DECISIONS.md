# Design decisions

## Resolve paths instead of comparing prefixes

A string prefix cannot distinguish a repository from a sibling whose name starts the same way. Resolve the candidate and require it to be relative to the resolved root. Resolution also catches symlinks pointing outside the root. This is a file-tool boundary, not a sandbox for shell execution.

## Review an immutable proposal

Replacement is computed from the original file exactly once. The patch must fit the complete review display; large changes are rejected for splitting. After approval, the source is checked again so a concurrent edit is preserved. A separate backup is stored before writing.

## Keep state local and private

SQLiteSession receives an explicit database path. The project context, conversation database, command history, and backups live in a gitignored state directory in the selected repository. They are runtime data, not public portfolio assets.

## Separate tool verification from model verification

The tests exercise filesystem behavior, a benign approved command, SDK tool registration, SQLite persistence, and mocked image request construction. They do not measure the model's coding ability or prove live account access.
