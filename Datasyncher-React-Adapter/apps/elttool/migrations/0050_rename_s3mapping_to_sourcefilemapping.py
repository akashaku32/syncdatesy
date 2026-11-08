from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("elttool", "0049_rename_s3_mapping_and_add_file_mapping_metadata"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="S3Mapping",
            new_name="SourceFileMapping",
        ),
        migrations.AlterField(
            model_name="sourcefilemapping",
            name="connection",
            field=models.OneToOneField(
                on_delete=models.CASCADE,
                primary_key=True,
                related_name="source_file_mapping",
                serialize=False,
                to="elttool.connection",
            ),
        ),
    ]
