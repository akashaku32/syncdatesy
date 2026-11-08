import csv
import os

from django.core.management.base import BaseCommand

from apps.elttool.models import Country


class Command(BaseCommand):
    help = "Load country_name and ISO3 code from countries_iso3.csv into Country table."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            dest="csv_path",
            default=None,
            help="Optional path to CSV file. Defaults to countries_iso3.csv next to this command.",
        )

    def handle(self, *args, **options):
        csv_path = options.get("csv_path")
        if not csv_path:
            csv_path = os.path.join(os.path.dirname(__file__), "countries_iso3.csv")

        if not os.path.exists(csv_path):
            self.stdout.write(self.style.ERROR(f"CSV not found: {csv_path}"))
            return

        created_count = 0
        updated_count = 0
        skipped_count = 0

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                self.stdout.write(self.style.ERROR("CSV has no headers."))
                return

            # Normalize headers
            header_map = {h.strip().lower(): h for h in reader.fieldnames}
            name_key = header_map.get("country name") or header_map.get("country_name") or header_map.get("name")
            code_key = header_map.get("iso3 code") or header_map.get("iso3") or header_map.get("code")

            if not name_key or not code_key:
                self.stdout.write(
                    self.style.ERROR(
                        f"CSV headers must include Country Name and ISO3 Code. Found: {reader.fieldnames}"
                    )
                )
                return

            for row in reader:
                country_name = (row.get(name_key) or "").strip()
                code = (row.get(code_key) or "").strip()

                if not country_name or not code:
                    skipped_count += 1
                    continue

                obj, created = Country.objects.update_or_create(
                    country_name=country_name,
                    defaults={"code": code},
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Countries loaded. Created: {created_count}, Updated: {updated_count}, Skipped: {skipped_count}"
            )
        )
