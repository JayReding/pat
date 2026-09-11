#!/usr/bin/env python3
import argparse
import getpass
import sys

from werkzeug.security import generate_password_hash

import store

ROLES = ('admin', 'case_manager', 'user')


def cmd_create(args):
    username = args.username.strip()
    if store.get_user_by_username(username):
        print(f"Error: user '{username}' already exists.")
        sys.exit(1)
    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(1)
    store.create_user(username, generate_password_hash(password), args.role, args.email or None)
    print(f"Created user '{username}' with role '{args.role}'.")


def cmd_list(args):
    users = store.list_users()
    if not users:
        print("No users.")
        return
    fmt = "{:<5} {:<20} {:<15} {:<30} {:<8} {}"
    print(fmt.format("ID", "Username", "Role", "Email", "Active", "Created At"))
    print("-" * 100)
    for u in users:
        print(fmt.format(
            u['id'], u['username'], u['role'],
            u.get('email') or '', 'yes' if u['active'] else 'no', u['created_at']
        ))


def cmd_set_role(args):
    user = store.get_user_by_username(args.username)
    if not user:
        print(f"Error: user '{args.username}' not found.")
        sys.exit(1)
    store.set_user_role(user['id'], args.role)
    print(f"Updated '{args.username}' role to '{args.role}'.")


def cmd_reset_password(args):
    user = store.get_user_by_username(args.username)
    if not user:
        print(f"Error: user '{args.username}' not found.")
        sys.exit(1)
    password = args.password
    if not password:
        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.")
            sys.exit(1)
    store.reset_user_password(user['id'], generate_password_hash(password))
    print(f"Password reset for '{args.username}'.")


def cmd_set_active(args):
    user = store.get_user_by_username(args.username)
    if not user:
        print(f"Error: user '{args.username}' not found.")
        sys.exit(1)
    store.set_user_active(user['id'], args.active)
    print(f"'{args.username}' is now {'active' if args.active else 'deactivated'}.")


def cmd_delete(args):
    user = store.get_user_by_username(args.username)
    if not user:
        print(f"Error: user '{args.username}' not found.")
        sys.exit(1)
    if not args.yes:
        answer = input(f"Delete user '{args.username}'? [y/N] ")
        if answer.strip().lower() != 'y':
            print("Aborted.")
            return
    store.delete_user(user['id'])
    print(f"Deleted user '{args.username}'.")


def _get_user(args):
    user = store.get_user_by_username(args.username)
    if not user:
        print(f"Error: user '{args.username}' not found.")
        sys.exit(1)
    return user


def _get_master(args):
    try:
        return store.get_master_by_id(args.master_id)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmds_grants(args):
    user = _get_user(args)
    grants = store.list_all_grants()
    mine = [g for g in grants if g['user_id'] == user['id']]
    if not mine:
        print(f"No grants for '{args.username}'.")
        return
    fmt = "{:<8} {:<10} {}"
    print(fmt.format("Level", "Master ID", "Subcase ID"))
    print("-" * 40)
    for g in mine:
        print(fmt.format(g['level'], g['master_case_id'] or '-', g['subcase_id'] or '-'))


def cmd_grant_master(args):
    user = _get_user(args)
    _get_master(args)
    store.grant_master(user['id'], args.master_id)
    print(f"Granted '{args.username}' master case {args.master_id} (covers its subcases).")


def cmd_revoke_master(args):
    user = _get_user(args)
    store.revoke_master(user['id'], args.master_id)
    print(f"Revoked master case {args.master_id} from '{args.username}'.")


def cmd_grant_subcase(args):
    user = _get_user(args)
    store.grant_subcase(user['id'], args.subcase_id)
    print(f"Granted '{args.username}' subcase {args.subcase_id}.")


def cmd_revoke_subcase(args):
    user = _get_user(args)
    store.revoke_subcase(user['id'], args.subcase_id)
    print(f"Revoked subcase {args.subcase_id} from '{args.username}'.")


def main():
    parser = argparse.ArgumentParser(description="Manage Preference Analysis Tool users.")
    subs = parser.add_subparsers(dest='command')

    p_create = subs.add_parser('create-user', help='Create a new user')
    p_create.add_argument('username')
    p_create.add_argument('--role', choices=ROLES, default='user')
    p_create.add_argument('--email')
    p_create.add_argument('--password')
    p_create.set_defaults(func=cmd_create)

    p_list = subs.add_parser('list-users', help='List all users')
    p_list.set_defaults(func=cmd_list)

    p_role = subs.add_parser('set-role', help='Change a user role')
    p_role.add_argument('username')
    p_role.add_argument('role', choices=ROLES)
    p_role.set_defaults(func=cmd_set_role)

    p_reset = subs.add_parser('reset-password', help='Reset a user password')
    p_reset.add_argument('username')
    p_reset.add_argument('--password')
    p_reset.set_defaults(func=cmd_reset_password)

    p_active = subs.add_parser('set-active', help='Activate/deactivate a user')
    p_active.add_argument('username')
    p_active.add_argument('--active', action=argparse.BooleanOptionalAction, default=True)
    p_active.set_defaults(func=cmd_set_active)

    p_del = subs.add_parser('delete', help='Delete a user')
    p_del.add_argument('username')
    p_del.add_argument('-y', '--yes', action='store_true', help='Skip confirmation')
    p_del.set_defaults(func=cmd_delete)

    p_gm = subs.add_parser('grant-master', help='Grant a master case to a user')
    p_gm.add_argument('username')
    p_gm.add_argument('master_id', type=int)
    p_gm.set_defaults(func=cmd_grant_master)

    p_rm = subs.add_parser('revoke-master', help='Revoke a master case from a user')
    p_rm.add_argument('username')
    p_rm.add_argument('master_id', type=int)
    p_rm.set_defaults(func=cmd_revoke_master)

    p_gs = subs.add_parser('grant-subcase', help='Grant a single subcase to a user')
    p_gs.add_argument('username')
    p_gs.add_argument('subcase_id', type=int)
    p_gs.set_defaults(func=cmd_grant_subcase)

    p_rs = subs.add_parser('revoke-subcase', help='Revoke a single subcase from a user')
    p_rs.add_argument('username')
    p_rs.add_argument('subcase_id', type=int)
    p_rs.set_defaults(func=cmd_revoke_subcase)

    p_lg = subs.add_parser('list-grants', help='List grants for a user')
    p_lg.add_argument('username')
    p_lg.set_defaults(func=cmds_grants)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == '__main__':
    main()
