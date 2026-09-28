# Local data retention

Rivet retains project data, original files, quote revisions, approvals, jobs, and exports in the local workspace until the operator intentionally removes them. There is no automatic expiry or external sending. Model context may be sent to OpenAI when an assistant run is explicitly started. Responses use `store: false`; provider account data controls still apply.

Back up the PostgreSQL database and private blobs together. Stop the API and worker before a consistent local backup; PostgreSQL itself can remain running. `scripts/backup.py` writes a compressed database dump and a blob archive under a timestamped backup directory. It records a manifest of file hashes. Keep backups access-restricted and outside source control.

Restore into a new disposable database first, using `pg_restore`, and compare the restored projects, versions, and blob hashes before replacing any active workspace. A sample backup/restore check was run during implementation. Automated production backup/restore scheduling and authorized retention/deletion APIs remain pilot work.

Original uploads are immutable while retained. Immutable does not mean exempt from deliberate authorized deletion. Removing a project requires deleting its dependent records and blobs consistently; the prototype does not expose a destructive project-delete button.
