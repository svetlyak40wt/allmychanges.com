# coding: utf-8
import datetime
import times
import logging

from django.core.management.base import BaseCommand
from twiggy_goodies.django import LogMixin

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.urlresolvers import reverse

from allmychanges.models import UserHistoryLog
from allmychanges.views import get_digest_for
from allmychanges.utils import dt_in_window
from allmychanges.notifications.email import send_email


########################################################
# WARNING!
# This file is deprecated and shouldn't be used anymore!
########################################################


def send_digest_to(user, period='day'):
    now = timezone.now()
    period_ago = now - datetime.timedelta(1 if period == 'day' else 7)
    second_period_ago = now - datetime.timedelta(7 if period == 'day' else 30)
    second_period_name = 'week' if period == 'day' else 'month'

    today_changes = get_digest_for(user.changelogs,
                                   after_date=period_ago)

    if today_changes:
        print 'Sending digest to {0} {1}'.format(user.username, user.email)

        # if True, then this digest includes only our own changelog
        # and we don't need to send a copy to me
        only_allmychanges = (len(today_changes) == 1
                             and today_changes[0]['name'] == 'allmychanges')

        from operator import attrgetter
        from itertools import groupby

        changelog_ids = map(lambda ch: ch['changelog']['obj'].id, today_changes)
        tags = list(user.tags.filter(changelog_id__in=changelog_ids))
        get_changelog_id = attrgetter('changelog_id')
        tags.sort(key=get_changelog_id)
        tags = groupby(tags, key=get_changelog_id)
        # make a map to show all tags
        # for each changelog
        tags = {key: list(values)
                for key, values in tags}

        UserHistoryLog.write(user, '',
                             'digest-sent',
                             'We send user an email with digest')

        for project in today_changes:
            # inject user tags into the datastructure
            changelog_id = project['changelog']['obj'].id
            project['user_tags'] = tags.get(changelog_id, [])

            # output their names to make cron send email what it sent to users
            print '\t{namespace}/{name}'.format(**project)
            for version in project['versions']:
                print '\t\tversion={number}, date={date}, discovered_at={discovered_at}'.format(
                    **version)

        other_changes = get_digest_for(user.changelogs,
                                       before_date=period_ago,
                                       after_date=second_period_ago)

        for changelog in other_changes:
            changelog['url'] = reverse('project', kwargs=dict(
                namespace=changelog['namespace'],
                name=changelog['name']))

        def send_to(email):
            if user.username != 'svetlyak40wt' \
               and not email.startswith('svetlyak.40wt') \
               and not only_allmychanges:
                # все чужие дайжесты дублируем ко мне на email
                send_to('svetlyak.40wt+changes@gmail.com')

            subject = 'Changelogs digest on {0:%d %B %Y}'.format(now)
            # в копиях мне — указываем username
            if email.startswith('svetlyak.40wt'):
                actual_subject = subject + ' ({username})'.format(
                    username=user.username)
            else:
                actual_subject = subject

            send_email(email,
                       actual_subject,
                       'digest.html',
                       context=dict(current_user=user,
                                    today_changes=today_changes,
                                    second_period_name=second_period_name,
                                    other_changes=other_changes),
                       tags=['allmychanges', 'digest'])

        send_to(user.email)
# for testing emails
#        send_to('svetlyak.40wt@gmail.com')



class Command(LogMixin, BaseCommand):
    help = u"""Prepares and sends digests to all users."""
    period = 'day'

    def handle(self, *args, **options):
        # this will disable cssutil's logger
        cssutils_logger = logging.getLogger('CSSUTILS')
        cssutils_logger.level = logging.ERROR

        now = timezone.now()
        utc_now = times.to_universal(now)
        all_timezones = get_user_model().objects.all().values_list(
            'timezone', flat=True).distinct()
        send_for_timezones = [tz for tz in all_timezones
                              if tz and dt_in_window(tz, utc_now, 9)]

        users = get_user_model().objects.exclude(email='')

        if args:
            if args[0] != 'all':
                users = users.filter(username__in=args)
        else:
            users = users.filter(timezone__in=send_for_timezones)

        # selecting only users who should receive this king of digests
        users = users.filter(send_digest='daily'
                             if self.period == 'day'
                             else 'weekly')

        for user in users:
            send_digest_to(user, period=self.period)
