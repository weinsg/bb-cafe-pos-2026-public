"""Server commands for staff logins. Passwords are typed in, never written in a file.

On the server:
  docker compose -f docker-compose.prod.yml exec app python -m app.manage add-staff admin_john admin
  docker compose -f docker-compose.prod.yml exec app python -m app.manage set-password admin
  docker compose -f docker-compose.prod.yml exec app python -m app.manage list-staff
"""
import argparse
import getpass
import sys
import uuid

from sqlmodel import Session, select

from .auth import hash_password
from .database import create_db_and_tables, engine
from .models import AuditLog, User

ROLES = ("barista", "booth_lead", "admin")


def ask_password(read=getpass.getpass) -> str:
    first = read("New password (at least 8 characters): ")
    if len(first) < 8:
        raise SystemExit("Password too short: use at least 8 characters.")
    if read("Type it again: ") != first:
        raise SystemExit("The two passwords differ. Nothing changed.")
    return first


def add_staff(session: Session, username: str, role: str, password: str) -> User:
    username = username.strip()
    if not username or len(username) > 50 or not username.replace("_", "").replace(".", "").isalnum():
        raise SystemExit("Use letters, digits, '_' or '.' for the username (up to 50).")
    if role not in ROLES:
        raise SystemExit(f"Role must be one of: {', '.join(ROLES)}")
    if session.exec(select(User).where(User.username == username)).first():
        raise SystemExit(f"{username} already exists. Use set-password to change its password.")
    user = User(username=username, password_hash=hash_password(password), role=role)
    session.add(user)
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username="server", action="STAFF_ADDED",
                         details=f"Login {username} added with role {role}"))
    session.commit()
    return user


def set_password(session: Session, username: str, password: str) -> None:
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise SystemExit(f"No login called {username}.")
    user.password_hash = hash_password(password)
    session.add(user)
    session.add(AuditLog(id=str(uuid.uuid4()), staff_username="server", action="PASSWORD_CHANGED",
                         details=f"Password changed for {username}"))
    session.commit()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.manage", description="Staff logins")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add-staff", help="add a personal login")
    add.add_argument("username")
    add.add_argument("role", choices=ROLES)
    change = commands.add_parser("set-password", help="change a login's password")
    change.add_argument("username")
    commands.add_parser("list-staff", help="list logins and roles")
    args = parser.parse_args(argv)
    create_db_and_tables()
    with Session(engine) as session:
        if args.command == "list-staff":
            for user in session.exec(select(User).order_by(User.role, User.username)).all():
                print(f"{user.username:24} {user.role}")
        elif args.command == "add-staff":
            add_staff(session, args.username, args.role, ask_password())
            print(f"Added {args.username} ({args.role}).")
        else:
            set_password(session, args.username, ask_password())
            print(f"Password changed for {args.username}.")


if __name__ == "__main__":
    main(sys.argv[1:])
