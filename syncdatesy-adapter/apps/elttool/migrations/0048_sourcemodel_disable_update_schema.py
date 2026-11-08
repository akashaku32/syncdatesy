from django.db import migrations, models


def set_amazons3_disable_update_schema(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceModel.objects.filter(name="AmazonS3").update(disable_update_schema=True)


def unset_amazons3_disable_update_schema(apps, schema_editor):
    SourceModel = apps.get_model("elttool", "SourceModel")
    SourceModel.objects.filter(name="AmazonS3").update(disable_update_schema=False)


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0047_reorder_amazons3_include_subfolders"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcemodel",
            name="disable_update_schema",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            set_amazons3_disable_update_schema,
            reverse_code=unset_amazons3_disable_update_schema,
        ),
    ]

