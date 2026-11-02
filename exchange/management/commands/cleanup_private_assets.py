from django.core.management.base import BaseCommand

from exchange.cleanup import cleanup_expired


class Command(BaseCommand):
    help = "Delete expired private images, close expired sessions and prune expired browser sessions."

    def handle(self, *args, **options):
        result = cleanup_expired()
        self.stdout.write(f"Deleted {result['assets_removed']} expired private assets. "
                          f"Closed {result['sessions_closed']} expired sessions.")
        if result["assets_failed"]:
            self.stderr.write(f"{result['assets_failed']} images could not be deleted; they will be retried.")
