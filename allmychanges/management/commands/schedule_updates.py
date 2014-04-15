from django.core.management.base import BaseCommand

from allmychanges.tasks import schedule_updates

class Command(BaseCommand):
    help = u"""Updates single project."""

    def handle(self, *args, **options):
        reschedule = True if args and 'reschedule' in args else False
        packages = [name for name in args if name != 'reschedule']
        schedule_updates.delay(reschedule=reschedule,
                               packages=packages)