"""Online, consistent SQLite backup: python -m app.backup /data/backup.db."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def backup_database(source: Path, destination: Path) -> None:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination in (source, Path(str(source) + "-wal"), Path(str(source) + "-shm")):
        raise ValueError("Il backup deve avere un percorso diverso dal database originale.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents accidentally overwriting a previous backup.
    with destination.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(str(destination))) as copy:
                original.backup(copy, pages=256, sleep=0.05)
                if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Verifica di integrità del backup fallita.")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup coerente online del database Chronos.")
    parser.add_argument("destination", type=Path, help="Nuovo file di backup; non deve già esistere.")
    parser.add_argument("--source", type=Path, default=Path(os.environ.get("CHRONOS_DB_PATH", "data/timelines.db")))
    args = parser.parse_args()
    backup_database(args.source, args.destination)
    print(f"Backup verificato: {args.destination}")


if __name__ == "__main__":
    main()
