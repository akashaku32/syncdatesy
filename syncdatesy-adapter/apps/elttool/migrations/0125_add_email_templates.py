from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0124_proxy_agent"),
    ]

    operations = [
        migrations.CreateModel(
            name="EmailTemplate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, null=True)),
                ("subject", models.CharField(max_length=500)),
                (
                    "header_subtitle",
                    models.CharField(default="AUTOMATED REPORT", max_length=255),
                ),
                ("header_bg_color", models.CharField(default="#6E2FD5", max_length=50)),
                ("primary_color", models.CharField(default="#6E2FD5", max_length=50)),
                ("greeting_name", models.CharField(default="Hi Team,", max_length=255)),
                ("body_content", models.TextField(blank=True, default="")),
                ("callout_content", models.TextField(blank=True, default="")),
                ("callout_styles", models.JSONField(blank=True, default=dict)),
                ("body_fields", models.JSONField(blank=True, default=list)),
                (
                    "cta_button_text",
                    models.CharField(default="View file on SharePoint", max_length=255),
                ),
                ("button_bg_color", models.CharField(default="#6E2FD5", max_length=50)),
                ("button_text_color", models.CharField(default="#ffffff", max_length=50)),
                (
                    "button_align",
                    models.CharField(
                        choices=[
                            ("left", "left"),
                            ("center", "center"),
                            ("right", "right"),
                        ],
                        default="center",
                        max_length=20,
                    ),
                ),
                (
                    "button_variant",
                    models.CharField(
                        choices=[
                            ("solid", "solid"),
                            ("outline", "outline"),
                            ("subtle", "subtle"),
                        ],
                        default="solid",
                        max_length=20,
                    ),
                ),
                ("team_name", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cmp",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="email_templates",
                        to="elttool.company",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="email_templates_created",
                        to="elttool.user",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="email_templates_updated",
                        to="elttool.user",
                    ),
                ),
            ],
            options={
                "db_table": "email_templates",
                "ordering": ["-updated_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="sourcetableselection",
            name="email_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="export_settings",
                to="elttool.emailtemplate",
            ),
        ),
        migrations.AddIndex(
            model_name="emailtemplate",
            index=models.Index(
                fields=["cmp", "updated_at"],
                name="email_templ_cmp_id_04a603_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="emailtemplate",
            constraint=models.UniqueConstraint(
                fields=("cmp", "name"),
                name="uniq_email_template_name_per_cmp",
            ),
        ),
    ]
