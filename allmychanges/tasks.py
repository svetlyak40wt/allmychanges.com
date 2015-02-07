# -*- coding: utf-8 -*-
import logging

from django.db import transaction
from django.utils import timezone
from django.conf import settings

from allmychanges.utils import (
    count,
    count_time)
from allmychanges.changelog_updater import update_preview_or_changelog
from allmychanges import chat

from twiggy_goodies.django_rq import job
from twiggy_goodies.threading import log


from functools import wraps
from django_rq.queues import get_queue
import inspect


if settings.DEBUG_JOBS == True:
    _task_log = []
    _orig_job = job
    def job(func, *args, **kwargs):
        """Dont be afraid, this magic is necessary only for unittests
        to be track all delayed tasks."""
        result = _orig_job(func, *args, **kwargs)

        if callable(func):
            def new_delay(*args, **kwargs):
                _task_log.append((func.__name__, args, kwargs))
                return result.delay(*args, **kwargs)
            result.delay = new_delay
            return result
        else:
            def decorator(func):
                new_func = result(func)
                orig_delay = new_func.delay

                def new_delay(*args, **kwargs):
                    _task_log.append((func.__name__, args, kwargs))
                    return orig_delay(*args, **kwargs)
                new_func.delay = new_delay
                return new_func
            return decorator


def get_func_name(func):
    """Helper to get same name for the job function as rq does.
    """
    if inspect.ismethod(func):
        return func.__name__
    elif inspect.isfunction(func) or inspect.isbuiltin(func):
        return '%s.%s' % (func.__module__, func.__name__)

    raise RuntimeError('unable to get func name')


def singletone(queue='default'):
    def decorator(func):
        """A decorator for rq's `delay` method which
        ensures there is no a job with same name
        and arguments already in the queue.
        """
        orig_delay = func.delay
        func_name = get_func_name(func)

        @wraps(func.delay)
        def wrapper(*args, **kwargs):
            queue_obj = get_queue(queue)
            already_in_the_queue = False

            # if queue is not async, then we don't need
            # to check if job already there
            if queue_obj._async:
                jobs = queue_obj.get_jobs()
                for j in jobs:
                    if j.func_name == func_name \
                       and j.args == args \
                       and j.kwargs == kwargs:
                        already_in_the_queue = True
                        break
            if not already_in_the_queue:
                return orig_delay(*args, **kwargs)

        func.delay = wrapper
        return func
    return decorator


def wait_chat_threads(func):
    """We need to wait all chat threads to exit
    to ensure that all messages were sent before
    task done.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            chat.wait_threads()

    return wrapper


@singletone()
@job
@transaction.atomic
@wait_chat_threads
def schedule_updates(reschedule=False, packages=[]):
    from .models import Changelog

    if packages:
        changelogs = Changelog.objects.filter(name__in=packages).distinct()
    else:
        changelogs = Changelog.objects.only_active()

    if not reschedule:
        changelogs = changelogs.filter(next_update_at__lte=timezone.now())

    num_changelogs = len(changelogs)
    count('task.schedule_updates.scheduling.count', num_changelogs)
    log.info('Rescheduling {0} changelogs update'.format(num_changelogs))

    for changelog in changelogs:
        changelog.schedule_update()


@singletone('preview')
@job('preview', timeout=600)
@transaction.atomic
@wait_chat_threads
def update_preview_task(preview_id):
    with log.fields(preview_id=preview_id):
        log.info('Starting task')
        try:
            from .models import Preview
            preview = Preview.objects.get(pk=preview_id)
            update_preview_or_changelog(preview)
        finally:
            log.info('Task done')


@singletone()
@job('default', timeout=600)
@transaction.atomic
@wait_chat_threads
def update_changelog_task(source):
    with log.fields(source=source):
        log.info('Starting task')
        processing_started_at = timezone.now()
        error = True
        error_description = None

        try:
            from .models import Changelog
            changelog = Changelog.objects.get(source=source)
            update_preview_or_changelog(changelog)

            changelog.last_update_took = (timezone.now() - processing_started_at).seconds
            changelog.next_update_at = changelog.calc_next_update()
            changelog.save()
            error = False
        except Exception as e:
            error_description = unicode(e)
            log.trace().error('Error during changelog update')
        finally:
            if error or changelog.status == 'error':
                changelog.paused_at = timezone.now()
                changelog.create_issue('auto-paused',
                                       comment=u'Paused because of error: "{0}"'.format(
                                           error_description or changelog.problem or 'unknown'))
                changelog.save()

            log.info('Task done')


@job
def raise_exception():
    1/0
