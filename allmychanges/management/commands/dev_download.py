# coding: utf-8
from django.core.management.base import BaseCommand
from twiggy_goodies.django import LogMixin
from allmychanges.downloader import (
    guess_downloader,
    get_downloader)


class Command(LogMixin, BaseCommand):
    help = u"""Download package sources into a temporary directory."""

    def handle(self, *args, **options):
        source = args[0]
        if len(args) > 1:
            search_list = args[1].split(',')
        else:
            search_list = []
        downloader_name = guess_downloader(source)
        downloader = get_downloader(downloader_name)
        print downloader(source, search_list=search_list)
