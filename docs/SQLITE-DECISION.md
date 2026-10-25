# Decision: SQLite is the database

**Decided:** October 2026. **Status:** in force. Revisit if the club grows past about ten times its present size, or wants two servers.

## What was decided

The club office stores everything in one SQLite file, in write-ahead-log mode, on the server's disk. PostgreSQL is not supported.

## Why

* **Size.** Margate Yacht Club has about 110 members. The whole database, years of invoices and bank lines included, is a few megabytes. A
  busy day is a handful of people using the site at once.
* **Fewer things to break.** There is no database server to patch, secure, back up separately or pay for. The club's volunteers can understand
  "one file": stop the app, copy the file, you have a backup.
* **Backups are simple and provable.** SQLite's online backup makes a consistent copy while the site is running; the restore drill opens that
  copy and checks it (see [OPERATIONS.md](OPERATIONS.md)). A restore is a file copy.
* **Speed.** Reads never block the single writer in WAL mode; at this size every page is a few milliseconds.
* **Cost.** The demo runs on the smallest free virtual machine.

## What it costs us

* **One server only.** Two copies of the app cannot share one SQLite file safely. That is fine for a club; it means no load-balanced hosting.
* **One writer at a time.** Writes queue for a few milliseconds. The jobs timer, the website and an import cannot all write at the same instant,
  so long imports run when the club is quiet, inside one transaction.
* **Disk loss loses the database** unless a backup has been copied elsewhere. This is why the nightly job copies each backup off the server
  and why the go / no-go checklist fails until an off-server copy and a restore drill exist.
* **Migrations** are done by adding columns and tables when the app starts (`ensure_schema`); SQLite cannot drop constraints in place, so
  occasionally a table is rebuilt. Each rebuild is tested.

## When to change

Move to PostgreSQL if any of these becomes true: more than one server is needed; more than a few dozen people use the site at the same moment
(for example a very large event ticket sale); or the file grows beyond several gigabytes. The SQLAlchemy models are written to be portable, and
`app/backup.py` already knows `pg_dump`, but a PostgreSQL deployment has not been tested and is not promised.
