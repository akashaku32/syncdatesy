import csv
import os

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.elttool.models import Role, RoleAccess, User


class Command(BaseCommand):
    help = "Reset Role and RoleAccess tables to the 3 required roles and load RoleAccess rows from a CSV."

    DESIRED_ROLES = [
        {"role_id": 1, "role_name": "Administrator"},
        {"role_id": 2, "role_name": "Analyst"},
        {"role_id": 3, "role_name": "Billing"},
    ]

    # Default RoleAccess rows (from `role_access_table values.csv` export).
    DEFAULT_ROLE_ACCESSES = [
        {"role_access_id": 1, "type": "CONNECTOR_VIEW", "role_id": 1},
        {"role_access_id": 2, "type": "CONNECTOR_MANAGE", "role_id": 1},
        {"role_access_id": 3, "type": "DESTINATION_VIEW", "role_id": 1},
        {"role_access_id": 4, "type": "DESTINATION_MANAGE", "role_id": 1},
        {"role_access_id": 5, "type": "TABLE_MANAGE", "role_id": 1},
        {"role_access_id": 6, "type": "SYNC_LOG_VIEW", "role_id": 1},
        {"role_access_id": 7, "type": "USER_MANAGE", "role_id": 1},
        {"role_access_id": 8, "type": "BILLING_ACCESS", "role_id": 1},
        {"role_access_id": 9, "type": "CONNECTOR_VIEW", "role_id": 2},
        {"role_access_id": 10, "type": "DESTINATION_VIEW", "role_id": 2},
        {"role_access_id": 11, "type": "TABLE_MANAGE", "role_id": 2},
        {"role_access_id": 12, "type": "SYNC_LOG_VIEW", "role_id": 2},
        {"role_access_id": 13, "type": "BILLING_ACCESS", "role_id": 3},
    ]

    def _to_int(self, value):
        if value is None:
            raise ValueError("missing int")
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            raise ValueError("empty int")
        return int(text)

    def _to_str(self, value):
        if value is None:
            return ""
        return str(value).strip()

    def add_arguments(self, parser):
        parser.add_argument(
            "--access-csv",
            dest="access_csv_path",
            default=None,
            help="Optional path to RoleAccess CSV (headers: role_access_id,type,role_id). If omitted, uses built-in defaults.",
        )
        parser.add_argument(
            "--clear-existing",
            action="store_true",
            help="Actually delete extra roles + clear role_access before re-creating from CSV.",
        )

    def handle(self, *args, **options):
        access_csv_path = options.get("access_csv_path")
        clear_existing = options.get("clear_existing", False)

        if not clear_existing:
            self.stdout.write(
                self.style.ERROR(
                    "Refusing to modify Role/RoleAccess without --clear-existing. "
                    "Re-run with --clear-existing when you're ready."
                )
            )
            return

        desired_role_ids = {r["role_id"] for r in self.DESIRED_ROLES}
        desired_role_names = {r["role_name"] for r in self.DESIRED_ROLES}

        access_rows = None
        if access_csv_path:
            if not os.path.exists(access_csv_path):
                self.stdout.write(self.style.ERROR(f"CSV not found: {access_csv_path}"))
                return
            access_rows = []
            with open(access_csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                expected = {"role_access_id", "type", "role_id"}
                if not reader.fieldnames or not expected.issubset({h.strip() for h in reader.fieldnames}):
                    self.stdout.write(
                        self.style.ERROR(
                            f"CSV headers must include {sorted(expected)}. Found: {reader.fieldnames}"
                        )
                    )
                    return
                for row in reader:
                    access_rows.append(row)
        else:
            # Use default rows embedded in this command.
            access_rows = list(self.DEFAULT_ROLE_ACCESSES)

        with transaction.atomic():
            # Delete any conflicting roles that block unique role_name for the desired roles.
            conflicting_roles = list(
                Role.objects.filter(role_name__in=desired_role_names).exclude(role_id__in=desired_role_ids)
            )
            if conflicting_roles:
                conflicting_role_ids = [r.role_id for r in conflicting_roles]
                User.objects.filter(roles_id__in=conflicting_role_ids).update(roles=None)
                RoleAccess.objects.filter(role_id__in=conflicting_role_ids).delete()
                Role.objects.filter(role_id__in=conflicting_role_ids).delete()

            # Remove users pointing at roles we will delete, to avoid CASCADE deleting users.
            roles_to_delete = list(Role.objects.exclude(role_id__in=desired_role_ids).values_list("role_id", flat=True))
            if roles_to_delete:
                User.objects.filter(roles_id__in=roles_to_delete).update(roles=None)

            # Clear role_access first (safe even if roles are kept).
            RoleAccess.objects.all().delete()

            # Delete extra roles (keep only 1/2/3).
            Role.objects.exclude(role_id__in=desired_role_ids).delete()

            # Ensure the 3 required roles exist with correct ids/names.
            for role_data in self.DESIRED_ROLES:
                Role.objects.update_or_create(
                    role_id=role_data["role_id"],
                    defaults={"role_name": role_data["role_name"]},
                )

            role_by_id = {r.role_id: r for r in Role.objects.filter(role_id__in=desired_role_ids)}

            created = 0
            skipped = 0
            seen_ids = set()

            for row in access_rows:
                try:
                    role_access_id = self._to_int(row.get("role_access_id"))
                    access_type = self._to_str(row.get("type"))
                    role_id = self._to_int(row.get("role_id"))
                except (ValueError, AttributeError, TypeError, KeyError):
                    skipped += 1
                    continue

                if not access_type or role_access_id <= 0:
                    skipped += 1
                    continue

                if role_access_id in seen_ids:
                    self.stdout.write(self.style.ERROR(f"Duplicate role_access_id in access rows: {role_access_id}"))
                    return
                seen_ids.add(role_access_id)

                role = role_by_id.get(role_id)
                if not role:
                    # Row refers to a role outside 1/2/3; skip to keep DB clean.
                    skipped += 1
                    continue

                RoleAccess.objects.create(
                    role_access_id=role_access_id,
                    type=access_type,
                    role=role,
                )
                created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Reset complete. Roles now: {', '.join(sorted(desired_role_names))}. "
                f"RoleAccess created: {created}, skipped: {skipped}."
            )
        )
