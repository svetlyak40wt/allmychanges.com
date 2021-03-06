from django.core.management.base import BaseCommand
from twiggy_goodies.django import LogMixin
from allmychanges import chat
from plumbum.cmd import git


class Command(LogMixin, BaseCommand):
    help = u"""Send release beats to graphite."""

    def handle(self, *args, **options):
        chat.send('Deploying new release')
        current_hash = git['rev-parse', '--short', 'HEAD']()
        with open('.hash-before-release', 'w') as f:
            f.write(current_hash)
