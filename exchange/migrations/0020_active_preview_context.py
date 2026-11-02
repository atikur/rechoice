from django.db import migrations, models


def mark_existing_sessions(apps, schema_editor):
    # Old sessions did not record which photo was on screen. Do not guess.
    alias = schema_editor.connection.alias
    sessions_with_previews = apps.get_model("exchange", "PreviewJob").objects.using(alias).values("owner_session_id")
    apps.get_model("exchange", "ExchangeSession").objects.using(alias).filter(
        pk__in=sessions_with_previews).update(active_preview_ids=None)


class Migration(migrations.Migration):
    dependencies = [("exchange", "0019_preview_colour")]
    operations = [
        migrations.AddField(model_name="exchangesession", name="active_preview_ids",
                            field=models.JSONField(default=list, blank=True, null=True)),
        migrations.RunPython(mark_existing_sessions, migrations.RunPython.noop),
        migrations.AddField(model_name="quote", name="preview_concern_ack",
                            field=models.CharField(max_length=64, blank=True)),
    ]
