import sys
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parents[1] / "warehouse" / "analyst.duckdb"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit('usage: python scripts/sql.py "<query>"')
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        con.sql(sys.argv[1]).show()
    finally:
        con.close()


if __name__ == "__main__":
    main()