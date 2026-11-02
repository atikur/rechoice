from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('exchange', '0018_widen_check_statuses'),
    ]

    operations = [
        migrations.AddField(
            model_name='previewjob',
            name='preview_colour',
            field=models.CharField(blank=True, max_length=7),
        ),
    ]
