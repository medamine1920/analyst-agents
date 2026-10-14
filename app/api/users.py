"""Manage console accounts. There is no sign-up page: the operator creates every account.

    python -m app.api.users add <name>        (asks for the password twice; --admin to make an admin)
    python -m app.api.users remove <name>
    python -m app.api.users list

On the server, run it inside the console container, so it writes the database the console uses:

    docker exec -it analyst-agents-console python -m app.api.users add <name>
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.api.security import SecurityStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["add", "remove", "list"])
    parser.add_argument("name", nargs="?")
    parser.add_argument("--admin", action="store_true", help="the account can invite and manage others")
    args = parser.parse_args(argv)
    store = SecurityStore()
    if args.action == "list":
        print("\n".join(store.users()) or "No accounts yet.")
        return 0
    if not args.name:
        parser.error(f"{args.action} needs a user name")
    if args.action == "remove":
        print("Removed." if store.remove_user(args.name) else "No such account.")
        return 0
    password = getpass.getpass(f"Password for {args.name}: ")
    if password != getpass.getpass("Repeat it: "):
        print("The passwords do not match; nothing changed.")
        return 1
    try:
        store.add_user(args.name, password, role="admin" if args.admin else None)
    except ValueError as exc:
        print(exc)
        return 1
    print(f"Account '{args.name}' is ready (existing sessions for it were signed out).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
