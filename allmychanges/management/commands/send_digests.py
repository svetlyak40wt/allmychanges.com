# coding: utf-8
import datetime
import os
import times

from django.core.management.base import BaseCommand
from twiggy_goodies.django import LogMixin

from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.conf import settings

from allmychanges.views import get_digest_for
from allmychanges.utils import dt_in_window
from premailer import Premailer


class Command(LogMixin, BaseCommand):
    help = u"""Prepares and sends digests to all users."""

    def handle(self, *args, **options):
        now = timezone.now()
        day_ago = now - datetime.timedelta(1)
        week_ago = now - datetime.timedelta(7)

        utc_now = times.to_universal(now)
        all_timezones = get_user_model().objects.all().values_list(
            'timezone', flat=True).distinct()
        send_for_timezones = [tz for tz in all_timezones
                              if tz and dt_in_window(tz, utc_now, 9)]
        
        users = get_user_model().objects.exclude(email='')
        
        if args:
            users = users.filter(username__in=args)
        else:
            users = users.filter(timezone__in=send_for_timezones)
        
        for user in users:
            today_changes = get_digest_for(user, after_date=day_ago)
            
            if today_changes or args:
                print 'Sending digest to', user.username, user.email
            
                week_changes = get_digest_for(user,
                                              before_date=day_ago,
                                              after_date=week_ago)         
                body = render_to_string(
                    'emails/digest.html',
                    dict(current_user=user,
                         today_changes=today_changes,
                         week_changes_count=len(week_changes)))

                external_styles = [
                    os.path.join(settings.STATIC_ROOT,
                                 'allmychanges/css',
                                 name)
                    for name in ('email.css',)]
                premailer = Premailer(body,
                                      base_url='http://allmychanges.com/',
                                      external_styles=external_styles)

                body = premailer.transform()
                message = EmailMultiAlternatives('Changelogs digest on {0:%d %B %Y}'.format(now),
                          None,
                          'AllMyChanges.com <noreply@allmychanges.com>',
                          [user.email])
                message.attach_alternative(body.encode('utf-8'), 'text/html')
                message.send()
