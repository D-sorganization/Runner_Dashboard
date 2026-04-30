# Push Subscriptions Database Rollback

If a new deployment introduces a broken database migration in `backend/push.py`, you may need to rollback to the previous version. Because we follow the "Reversible: additive ship → removal next release" rule (see [CLAUDE.md](../../CLAUDE.md)), downgrading the code is safe:

1. **Downgrade Dashboard Version**
   Revert the dashboard service to the previous Git commit/tag.
   ```bash
   git checkout <previous-tag>
   sudo systemctl restart runner-dashboard
   ```

2. **Database State**
   Because additive schema changes (e.g., adding a new column) do not break older code that uses `SELECT *` without expecting the column (thanks to sqlite3 `Row` mapping), the older code will simply ignore the new column. 
   The `schema_migrations` table will track that the database is at a newer version. When the older code connects, its internal `MIGRATIONS` list will end at an earlier version. Because `version > current_version` evaluates to false, it will skip migrations and function normally.

3. **Data Loss Warning**
   If you wrote data to the new additive column while the new version was active, that data will remain in the database but will be ignored by the older code. This is safe and expected. 
   **Never DROP columns or tables** during a migration. If you must remove a column, first deploy a version that stops reading/writing it. Then, in the *next* release, deploy the `ALTER TABLE ... DROP COLUMN` migration.

4. **Manual SQLite Intervention (If required)**
   If a migration completely corrupts the DB and you must restore from backup or manually fix it:
   ```bash
   # The database is located at RUNNER_DASHBOARD_PUSH_DB (defaults to ~/.gemini/antigravity/push_subscriptions.sqlite3 or similar)
   sqlite3 /path/to/push_subscriptions.sqlite3
   ```
   If you need to manually rewind the migration version so a fix script can run it again:
   ```sql
   DELETE FROM schema_migrations WHERE version > <target_version>;
   ```
