"""Run on the server to look after people:  docker compose exec gymlog-server python -m app.cli <command>"""
import argparse
import datetime
import sys

from .config import Settings
from .service import NameTaken, NotFound, Service


def _when(ms):
    return "never" if not ms else datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def _find(service: Service, who: str):
    """A user by id or by name (any capitals)."""
    for u in service.list_users():
        if u["id"] == who or u["name"].lower() == who.strip().lower():
            return u
    raise NotFound(f"No user '{who}'.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli", description="Manage the people who can use this GymLog server.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-user", help="Make a new user and show their token (once)")
    a.add_argument("name")
    sub.add_parser("list-users", help="Show everyone and how much they have stored")
    for name, text in (("reset-token", "Give a user a new token (the old one stops working)"), ("disable-user", "Switch a user off"), ("enable-user", "Switch a user back on")):
        c = sub.add_parser(name, help=text)
        c.add_argument("user", help="name or id")
    d = sub.add_parser("delete-user", help="Delete a user and ALL of their backups")
    d.add_argument("user", help="name or id")
    d.add_argument("--yes", action="store_true", help="really do it")
    sub.add_parser("show-admin-token", help="Print the admin token")
    args = p.parse_args(argv)

    service = Service(Settings())
    try:
        if args.cmd == "add-user":
            user, token = service.create_user(args.name)
            print(f"Created '{user['name']}' (id {user['id']}).\nToken (copy it now, it cannot be shown again):\n{token}")
        elif args.cmd == "list-users":
            users = service.list_users()
            if not users:
                print("No users yet. Add one with:  python -m app.cli add-user \"Name\"")
            for u in users:
                print(f"{u['name']:<20} {u['id']}  {'OFF' if u['disabled'] else 'on ':<3} {u['backups']:>3} backups  {u['bytes'] / 1e6:8.1f} MB  last {_when(u['lastBackupAt'])}")
        elif args.cmd == "reset-token":
            u = _find(service, args.user)
            print(f"New token for '{u['name']}' (the old one no longer works):\n{service.reset_token(u['id'])}")
        elif args.cmd in ("disable-user", "enable-user"):
            u = _find(service, args.user)
            service.set_disabled(u["id"], args.cmd == "disable-user")
            print(f"'{u['name']}' is now {'OFF' if args.cmd == 'disable-user' else 'on'}.")
        elif args.cmd == "delete-user":
            u = _find(service, args.user)
            if not args.yes:
                print(f"This deletes '{u['name']}' and {u['backups']} backups for good. Run again with --yes to do it.")
                return 1
            service.delete_user(u["id"])
            print(f"Deleted '{u['name']}'.")
        elif args.cmd == "show-admin-token":
            print(service.admin_token)
    except (NameTaken, NotFound, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
