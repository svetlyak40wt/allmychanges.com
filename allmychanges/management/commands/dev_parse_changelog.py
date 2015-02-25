# coding: utf-8
from optparse import make_option
from django.core.management.base import BaseCommand
from twiggy_goodies.django import LogMixin
from allmychanges.parsing.pipeline import processing_pipe, vcs_processing_pipe
from allmychanges.utils import split_filenames




def print_version(version, full=False):
    print ''
    print u'{version} ({filename}, {date}, unreleased={unreleased})'.format(
        version=version.version,
        filename=version.filename,
        unreleased=getattr(version, 'unreleased', False),
        date=getattr(version, 'date', None)).encode('utf-8')

    if full:
        print version.content.encode('utf-8')


class Command(LogMixin, BaseCommand):
    help = u"""Search changelog like data in given path on disk."""
    option_list = BaseCommand.option_list + (
        make_option('--full',
                    action='store_true',
                    dest='full',
                    default=False,
                    help='Print also a version descriptions'),)



    def handle(self, path, *args, **options):
        search_list = ignore_list = []

        if len(args) >= 1:
            def parse_check(text):
                if ':' in text:
                    return text.split(':', 1)
                return text, None

            search_list = map(parse_check, split_filenames(args[0]))
        if len(args) >= 2:
            ignore_list = split_filenames(args[1])

        pipe = processing_pipe
        if search_list and search_list[0][0] == 'VCS':
            pipe = vcs_processing_pipe

        versions = pipe(path, ignore_list, search_list)
        for version in versions:
            print_version(version, options['full'])
