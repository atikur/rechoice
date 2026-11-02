from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('exchange', '0016_exchange_agents'),
    ]

    operations = [
        migrations.AddField(model_name='previewjob', name='print_ratio', field=models.FloatField(blank=True, null=True)),
        migrations.AddField(model_name='previewjob', name='outside_change_pct', field=models.FloatField(blank=True, null=True)),
        migrations.AddField(model_name='previewjob', name='gate_status', field=models.CharField(blank=True, max_length=12)),
        migrations.AddField(model_name='previewjob', name='gate_note', field=models.CharField(blank=True, max_length=220)),
    ]
