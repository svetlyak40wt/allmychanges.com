import os
import datetime

from pprint import pprint
from django.core.management.base import BaseCommand
from twiggy_goodies.django import LogMixin
from django.conf import settings
from allmychanges.utils import graphite_send
from allmychanges.models import Package, Changelog, Version
from django.utils import timezone


def get_stats_from_file():
    """Gets stats dumped to file '.stats'.
    It has simple format like 'name value' on each line

    Usually .stats file used to store such data as number of
    unittests passed/failed, code metrics and parser quality
    metrics.
    """
    filename = os.path.join(settings.PROJECT_ROOT, '.stats')
    if os.path.exists(filename):
        with open(filename) as f:
            lines = f.readlines()
            stats = [line.split(None, 1) for line in lines]
            stats = {name: float(value) for name, value in stats}
            return stats
    return {}


def get_stats():
    stats = get_stats_from_file()

    from rq.scripts.rqinfo import (setup_default_arguments,
                                   parse_args,
                                   setup_redis, Queue)


    args = parse_args()
    setup_default_arguments(args, {})
    setup_redis(args)
    for queue in Queue.all():
        stats['queue.{0}.jobs'.format(queue.name)] = queue.count

    stats['db.packages'] = Package.objects.count()
    stats['db.changelogs'] = Changelog.objects.count()
    stats['db.versions.v1'] = Version.objects.filter(code_version='v1').count()
    stats['db.versions.v2'] = Version.objects.filter(code_version='v2').count()

    now = timezone.now()
    minute_ago = now - datetime.timedelta(0, 60)

    stats['crawler.discovered.v1.count'] = Version.objects.filter(
        code_version='v1',
        discovered_at__gte=minute_ago).count()
    stats['crawler.discovered.v2.count'] = Version.objects.filter(
        code_version='v2',
        discovered_at__gte=minute_ago).count()

    return stats


class Command(LogMixin, BaseCommand):
    help = u"""Send stats to graphite Graphite."""

    def handle(self, *args, **options):
        stats = get_stats()
        if args and args[0] == 'dry':
            pprint(stats)
        else:
            graphite_send(**stats)
