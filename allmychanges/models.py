# -*- coding: utf-8 -*-
import time
import math
import datetime
import md5

from django.db import models
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, UserManager as BaseUserManager
from django.core.cache import cache
from south.modelsinspector import add_introspection_rules

from twiggy_goodies.threading import log

from allmychanges.validators import URLValidator
from allmychanges.downloader import normalize_url
from allmychanges.utils import (
    split_filenames,
)
from allmychanges import chat
from allmychanges.downloader import (
    guess_downloader,
    get_downloader)

from allmychanges.tasks import update_preview_task, update_changelog_task


MARKUP_CHOICES = (
    ('markdown', 'markdown'),
    ('rest', 'rest'),
)
NAME_LENGTH = 80
NAMESPACE_LENGTH = 80


# based on http://www.caktusgroup.com/blog/2013/08/07/migrating-custom-user-model-django/

from pytz import common_timezones
TIMEZONE_CHOICES = [(tz, tz) for tz in common_timezones]


class URLField(models.URLField):
    default_validators = [URLValidator()]

add_introspection_rules([], ["^allmychanges\.models\.URLField"])



class UserManager(BaseUserManager):
    def _create_user(self, username, email=None, password=None,
                     **extra_fields):
        now = timezone.now()
        email = self.normalize_email(email)
        user = self.model(username=username,
                          email=email,
                          last_login=now,
                          date_joined=now,
                          **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create(self, *args, **kwargs):
        email = kwargs.get('email')
        if email and self.filter(email=email).count() > 0:
            raise ValueError('User with email "{0}" already exists'.format(email))

        chat.send('New user <http://allmychanges.com/u/{0}/history/|{0}> with email "{1}" (from create)'.format(kwargs.get('username'), email))
        return super(UserManager, self).create(*args, **kwargs)

    def create_user(self, username, email=None, password=None, **extra_fields):
        if email and self.filter(email=email).count() > 0:
            raise ValueError('User with email "{0}" already exists'.format(email))

        chat.send('New user <http://allmychanges.com/u/{0}/history/|{0}> with email "{1}" (from create_user)'.format(username, email))
        return self._create_user(username, email, password,
                                 **extra_fields)

    def active_users(self, interval):
        """Outputs only users who was active in last `interval` days.
        """
        after = timezone.now() - datetime.timedelta(interval)
        queryset = self.all()
        queryset = queryset.filter(history_log__action__in=ACTIVE_USER_ACTIONS,
                                   history_log__created_at__gte=after).distinct()
        return queryset


class User(AbstractBaseUser):
    """
    A fully featured User model with admin-compliant permissions that uses
    a full-length email field as the username.

    Email and password are required. Other fields are optional.
    """
    username = models.CharField('user name', max_length=254, unique=True)
    email = models.EmailField('email address', max_length=254, blank=True)
    email_is_valid = models.BooleanField(default=False)
    date_joined = models.DateTimeField('date joined', default=timezone.now)
    timezone = models.CharField(max_length=100,
                                choices=TIMEZONE_CHOICES,
                                default='UTC')
    changelogs = models.ManyToManyField('Changelog', through='ChangelogTrack',
                                        related_name='trackers')
    moderated_changelogs = models.ManyToManyField('Changelog', through='Moderator',
                                                  related_name='moderators')

    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['email']

    class Meta:
        verbose_name = 'user'
        verbose_name_plural = 'users'

    def does_track(self, changelog):
        """Check if this user tracks given changelog."""
        return self.changelogs.filter(pk=changelog.pk).count() == 1

    def track(self, changelog):
        if not self.does_track(changelog):
            if changelog.namespace == 'web' and changelog.name == 'allmychanges':
                action = 'track-allmychanges'
                action_description = 'User tracked our project\'s changelog.'
            else:
                action = 'track'
                action_description = 'User tracked changelog:{0}'.format(changelog.id)

            UserHistoryLog.write(self, '', action, action_description)

            ChangelogTrack.objects.create(
                user=self,
                changelog=changelog)

    def untrack(self, changelog):
        if self.does_track(changelog):
            if changelog.namespace == 'web' and changelog.name == 'allmychanges':
                action = 'untrack-allmychanges'
                action_description = 'User untracked our project\'s changelog.'
            else:
                action = 'untrack'
                action_description = 'User untracked changelog:{0}'.format(changelog.id)

            UserHistoryLog.write(self, '', action, action_description)
            ChangelogTrack.objects.filter(
                user=self,
                changelog=changelog).delete()


class Subscription(models.Model):
    email = models.EmailField()
    come_from = models.CharField(max_length=100)
    date_created = models.DateTimeField()

    def __unicode__(self):
        return self.email


class Downloadable(object):
    """Adds method download, which uses attribute `source`
    to update attribute `downloader` if needed and then to
    download repository into a temporary directory.
    """
    def download(self):
        """This method fetches repository into a temporary directory
        and returns path to this directory.
        """
        if self.downloader is None:
            self.downloader = guess_downloader(self.source)
            self.save(update_fields=('downloader',))

        download = get_downloader(self.downloader)
        return download(self.source,
                        search_list=self.get_search_list(),
                        ignore_list=self.get_ignore_list())


    # A mixin to get/set ignore and check lists on a model.
    def get_ignore_list(self):
        """Returns a list with all filenames and directories to ignore
        when searching a changelog."""
        return split_filenames(self.ignore_list)

    def set_ignore_list(self, items):
        self.ignore_list = u'\n'.join(items)

    def get_search_list(self):
        """Returns a list with all filenames and directories to check
        when searching a changelog."""
        def process(name):
            if ':' in name:
                return name.split(':', 1)
            else:
                return (name, None)

        filenames = split_filenames(self.search_list)
        return map(process, filenames)

    def set_search_list(self, items):
        def process(item):
            if isinstance(item, tuple) and item[1]:
                return u':'.join(item)
            else:
                return item
        self.search_list = u'\n'.join(map(process, items))


class ChangelogManager(models.Manager):
    def only_active(self):
        return self.all().filter(paused_at=None).exclude(name=None)


class Changelog(Downloadable, models.Model):
    objects = ChangelogManager()
    source = URLField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # TODO: remove
    processing_started_at = models.DateTimeField(blank=True, null=True)
    problem = models.CharField(max_length=1000,
                               help_text='Latest error message',
                               blank=True, null=True)
    filename = models.CharField(max_length=1000,
                               help_text=('If changelog was discovered, then '
                                          'field will store it\'s filename'),
                               blank=True, null=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    next_update_at = models.DateTimeField(default=timezone.now)
    paused_at = models.DateTimeField(blank=True, null=True)
    last_update_took = models.IntegerField(
        help_text=('Number of seconds required to '
                   'update this changelog last time'),
        default=0)
    ignore_list = models.CharField(max_length=1000,
                                   default='',
                                   help_text=('Comma-separated list of directories'
                                              ' and filenames to ignore searching'
                                              ' changelog.'),
                                   blank=True)
    check_list = models.CharField(max_length=1000,
                                   default='',
                                   help_text=('Comma-separated list of directories'
                                              ' and filenames to search'
                                              ' changelog.'),
                                   blank=True)
    search_list = models.CharField(max_length=1000,
                                  default='',
                                  help_text=('Comma-separated list of directories'
                                              ' and filenames to search'
                                              ' changelog.'),
                                  blank=True)
    namespace = models.CharField(max_length=NAMESPACE_LENGTH, blank=True, null=True)
    name = models.CharField(max_length=NAME_LENGTH, blank=True, null=True)
    downloader = models.CharField(max_length=10, blank=True, null=True)
    status = models.CharField(max_length=40, default='created')
    processing_status = models.CharField(max_length=40)

    class Meta:
        unique_together = ('namespace', 'name')

    def __unicode__(self):
        return u'Changelog from {0}'.format(self.source)

    def latest_version(self):
        versions = list(
            self.versions.exclude(unreleased=True) \
                         .order_by('-discovered_at', '-number')[:1])
        if versions:
            return versions[0]

    def get_absolute_url(self):
        from django.core.urlresolvers import reverse
        return reverse('package', kwargs=dict(
            namespace=self.namespace,
            name=self.name))

    def editable_by(self, user, light_user=None):
        light_moderators = set(self.light_moderators.values_list('light_user', flat=True))
        moderators = set(self.moderators.values_list('id', flat=True))

        if user.is_authenticated():
            # Any changelog could be edited by me
            if user.username == 'svetlyak40wt':
                return True

            if moderators or light_moderators:
                return user.id in moderators
        else:
            if moderators or light_moderators:
                return light_user in light_moderators
        return True

    def is_moderator(self, user, light_user=None):
        light_moderators = set(self.light_moderators.values_list('light_user', flat=True))
        moderators = set(self.moderators.values_list('id', flat=True))

        if user.is_authenticated():
            return user.id in moderators
        else:
            return light_user in light_moderators

    def add_to_moderators(self, user, light_user=None):
        """Adds user to moderators and returns 'normal' or 'light'
        if it really added him.
        In case if user already was a moderator, returns None."""

        if not self.is_moderator(user, light_user):
            if user.is_authenticated():
                Moderator.objects.create(changelog=self, user=user)
                return 'normal'
            else:
                if light_user is not None:
                    self.light_moderators.create(light_user=light_user)
                    return 'light'

    def create_issue(self, type, comment='', related_versions=[]):
        joined_versions = u', '.join(related_versions)

        # for some types, only one issue at a time is allowed
        if type == 'lesser-version-count':
            if self.issues.filter(type=type, resolved_at=None, related_versions=joined_versions).count() > 0:
                return

        issue = self.issues.create(type=type,
                                   comment=comment.format(related_versions=joined_versions),
                                   related_versions=joined_versions)
        chat.send(u'New issue of type "{issue.type}" with comment: "{issue.comment}" was created for <http://allmychanges.com/issues/?namespace={issue.changelog.namespace}&name={issue.changelog.name}|{issue.changelog.namespace}/{issue.changelog.name}>'.format(
            issue=issue))

    def resolve_issues(self, type):
        self.issues.filter(type=type, resolved_at=None).update(resolved_at=timezone.now())

    def create_preview(self, user, light_user):
        preview = self.previews.create(
            source=self.source,
            ignore_list=self.ignore_list,
            search_list=self.search_list,
            user=user,
            light_user=light_user)
        return preview

    def set_status(self, status, **kwargs):
        changed_fields = ['status', 'updated_at']
        if status == 'error':
            self.problem = kwargs.get('problem')
            changed_fields.append('problem')

        self.status = status
        self.updated_at = timezone.now()
        self.save(update_fields=changed_fields)

    def set_processing_status(self, status):
        self.processing_status = status
        self.updated_at = timezone.now()
        self.save(update_fields=('processing_status',
                                 'updated_at'))
        key = 'preview-processing-status:{0}'.format(self.id)
        cache.set(key, status, 10 * 60)

    def get_processing_status(self):
        key = 'preview-processing-status:{0}'.format(self.id)
        result = cache.get(key, self.processing_status)
        return result

    def calc_next_update(self):
        """Returns date and time when next update should be scheduled.
        """
        hour = 60 * 60
        min_update_interval = hour
        time_to_next_update = 4 * hour
        num_trackers = self.trackers.count()
        time_to_next_update = time_to_next_update / math.log(max(math.e,
                                                                 num_trackers))

        time_to_next_update = max(min_update_interval,
                                  time_to_next_update,
                                  2 * self.last_update_took)

        return timezone.now() + datetime.timedelta(0, time_to_next_update)

    def calc_next_update_if_error(self):
        # TODO: check and remove
        return timezone.now() + datetime.timedelta(0, 1 * 60 * 60)

    def schedule_update(self, async=True, full=False):
        with log.fields(changelog_name=self.name,
                        changelog_namespace=self.namespace,
                        async=async,
                        full=full):
            log.info('Scheduling changelog update')

            self.set_status('processing')
            self.set_processing_status('waiting-in-the-queue')

            self.problem = None
            self.save()

            if full:
                self.versions.all().delete()

            if async:
                update_changelog_task.delay(self.source)
            else:
                update_changelog_task(self.source)

    def resume(self):
        self.paused_at = None
        self.next_update_at = timezone.now()
        # we don't need to save here, because this will be done in schedule_update
        self.schedule_update()


    def clean(self):
        super(Changelog, self).clean()
        self.source, _, _ = normalize_url(self.source, for_checkout=False)


class ChangelogTrack(models.Model):
    user = models.ForeignKey(User)
    changelog = models.ForeignKey(Changelog)
    created_at = models.DateTimeField(default=timezone.now)


class Issue(models.Model):
    """Keeps track any issues, related to a changelog.
    """
    changelog = models.ForeignKey(Changelog,
                                  related_name='issues')
    user = models.ForeignKey(User, blank=True, null=True)
    light_user = models.CharField(max_length=40, blank=True, null=True)
    type = models.CharField(max_length=40)
    comment = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(blank=True, null=True)
    related_versions = models.TextField(default='', blank=True,
                                        help_text='Comma-separated list of versions, related to this issue')

    def __repr__(self):
        return """
Issue(changelog={self.changelog},
      type={self.type},
      comment={self.comment},
      created_at={self.created_at},
      resolved_at={self.resolved_at})""".format(self=self).strip()

    @staticmethod
    def merge(user, light_user):
        entries = Issue.objects.filter(user=None,
                                                light_user=light_user)
        if entries.count() > 0:
            with log.fields(username=user.username,
                            num_entries=entries.count(),
                            light_user=light_user):
                log.info('Merging issues')
                entries.update(user=user)

    def editable_by(self, user, light_user=None):
        return self.changelog.editable_by(user, light_user)

    def get_related_versions(self):
        response = [version.strip()
                    for version in self.related_versions.split(',')]
        return filter(None, response)

    def get_related_deployments(self):
        return DeploymentHistory.objects \
            .filter(deployed_at__lte=self.created_at) \
            .order_by('-id')[:3]


class IssueComment(models.Model):
    issue = models.ForeignKey(Issue, related_name='comments')
    user = models.ForeignKey(User, blank=True, null=True,
                             related_name='issue_comments')
    created_at = models.DateTimeField(default=timezone.now)
    message = models.TextField()


class DiscoveryHistory(models.Model):
    """Keeps track any issues, related to a changelog.
    """
    changelog = models.ForeignKey(Changelog,
                                  related_name='discovery_history')
    discovered_versions = models.TextField()
    new_versions = models.TextField()
    num_discovered_versions = models.IntegerField()
    num_new_versions = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


    def __repr__(self):
        return '<DiscoveryHistory versions={0.discovered_versions}'.format(
            self)


class LightModerator(models.Model):
    """These entries are created when anonymouse user
    adds another package into the system.
    When user signs up, these entries should be
    transformed into the Moderator entries.
    """
    changelog = models.ForeignKey(Changelog,
                                  related_name='light_moderators')
    light_user = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def merge(user, light_user):
        entries = LightModerator.objects.filter(light_user=light_user)
        for entry in entries:
            with log.fields(username=user.username,
                            light_user=light_user):
                log.info('Transforming light moderator into the permanent')
                Moderator.objects.create(
                    changelog=entry.changelog,
                    user=user,
                    from_light_user=light_user)
        entries.delete()

    @staticmethod
    def remove_stale_moderators():
        LightModerator.objects.filter(
            created_at__lte=timezone.now() - datetime.timedelta(1)).delete()


class Moderator(models.Model):
    changelog = models.ForeignKey(Changelog)
    user = models.ForeignKey(User)
    created_at = models.DateTimeField(auto_now_add=True)
    from_light_user = models.CharField(max_length=40, blank=True, null=True)


class Preview(Downloadable, models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             related_name='previews',
                             blank=True,
                             null=True)
    changelog = models.ForeignKey(Changelog,
                                  related_name='previews')
    light_user = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(blank=True, null=True)
    source = models.URLField()
    ignore_list = models.CharField(max_length=1000,
                                   default='',
                                   help_text=('Comma-separated list of directories'
                                              ' and filenames to ignore searching'
                                              ' changelog.'),
                                   blank=True)
    # TODO: remove this field after migration on production
    check_list = models.CharField(max_length=1000,
                                  default='',
                                  help_text=('Comma-separated list of directories'
                                              ' and filenames to search'
                                              ' changelog.'),
                                  blank=True)
    search_list = models.CharField(max_length=1000,
                                   default='',
                                   help_text=('Comma-separated list of directories'
                                              ' and filenames to search'
                                              ' changelog.'),
                                   blank=True)
    problem = models.CharField(max_length=1000,
                               help_text='Latest error message',
                               blank=True, null=True)
    downloader = models.CharField(max_length=10, blank=True, null=True)
    done = models.BooleanField(default=False)
    status = models.CharField(max_length=40, default='created')
    processing_status = models.CharField(max_length=40)

    @property
    def namespace(self):
        return self.changelog.namespace

    @property
    def name(self):
        return self.changelog.name

    def set_status(self, status, **kwargs):
        changed_fields = ['status', 'updated_at']
        if status == 'processing':
            self.versions.all().delete()
            self.updated_at = timezone.now()
            changed_fields.append('updated_at')

        elif status == 'error':
            self.problem = kwargs.get('problem')
            changed_fields.append('problem')

        self.status = status
        self.updated_at = timezone.now()
        self.save(update_fields=changed_fields)


    def set_processing_status(self, status):
        self.processing_status = status
        self.updated_at = timezone.now()
        self.save(update_fields=('processing_status',
                                 'updated_at'))
        key = 'preview-processing-status:{0}'.format(self.id)
        cache.set(key, status, 10 * 60)

    def get_processing_status(self):
        key = 'preview-processing-status:{0}'.format(self.id)
        result = cache.get(key, self.processing_status)
        return result

    def schedule_update(self):
        self.set_status('processing')
        self.set_processing_status('waiting-in-the-queue')
        self.versions.all().delete()
        update_preview_task.delay(self.pk)


class VersionManager(models.Manager):
    def get_query_set(self):
        # TODO: rename after migration to Django 1.6
        return super(VersionManager, self).get_query_set().order_by('-id')


CODE_VERSIONS = [
    ('v1', 'Old parser'),
    ('v2', 'New parser')]


class Version(models.Model):
    changelog = models.ForeignKey(Changelog,
                                  related_name='versions',
                                  blank=True,
                                  null=True,
                                  on_delete=models.SET_NULL)
    preview = models.ForeignKey(Preview,
                                related_name='versions',
                                blank=True,
                                null=True,
                                on_delete=models.SET_NULL)

    date = models.DateField(blank=True, null=True)
    number = models.CharField(max_length=255)
    unreleased = models.BooleanField(default=False)
    discovered_at = models.DateTimeField(blank=True, null=True)
    last_seen_at = models.DateTimeField(blank=True, null=True)
    code_version = models.CharField(max_length=255, choices=CODE_VERSIONS)
    filename = models.CharField(max_length=1000,
                                help_text=('Source file where this version was found'),
                                blank=True, null=True)
    raw_text = models.TextField(blank=True, null=True)
    processed_text = models.TextField(blank=True, null=True)
    order_idx = models.IntegerField(blank=True, null=True,
                                    help_text=('This field is used to reorder versions '
                                               'according their version numbers and to '
                                               'fetch them from database efficiently.'))

    objects = VersionManager()

    class Meta:
        get_latest_by = 'order_idx'

    def __unicode__(self):
        return self.number


ACTIVE_USER_ACTIONS = (
    u'landing-digest-view', u'landing-track', u'landing-ignore',
    u'login', u'package-view', u'profile-update', u'digest-view',
    u'edit-digest-view', u'index-view', u'track', u'untrack',
    u'untrack-allmychanges', u'create-issue')


class UserHistoryLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             related_name='history_log',
                             blank=True,
                             null=True)
    light_user = models.CharField(max_length=40)
    action = models.CharField(max_length=40)
    description = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)


    @staticmethod
    def merge(user, light_user):
        entries = UserHistoryLog.objects.filter(user=None,
                                                light_user=light_user)
        if entries.count() > 0:
            with log.fields(username=user.username,
                            num_entries=entries.count(),
                            light_user=light_user):
                log.info('Merging user history logs')
                entries.update(user=user)

    @staticmethod
    def write(user, light_user, action, description):
        user = user if user is not None and user.is_authenticated() else None
        UserHistoryLog.objects.create(user=user,
                                      light_user=light_user,
                                      action=action,
                                      description=description)


class DeploymentHistory(models.Model):
    hash = models.CharField(max_length=32, default='')
    description = models.TextField()
    deployed_at = models.DateTimeField(auto_now_add=True)

    def __repr__(self):
        response =[u'<DeploymentHistory deployed_at={0.deployed_at} hash={0.hash}'.format(self)]
        response.extend(
                   u'                  ' + line
            for line in self.description.split('\n'))
        response.append(u'>')
        return u'\n'.join(response).encode('utf-8')


class EmailVerificationCode(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
                                related_name='email_verification_code')
    hash = models.CharField(max_length=32, default='')
    deployed_at = models.DateTimeField(auto_now_add=True)


    @staticmethod
    def new_code_for(user):
        hash = md5.md5(str(time.time()) + settings.SECRET_KEY).hexdigest()

        try:
            code = user.email_verification_code
            code.hash = hash
            code.save()
        except EmailVerificationCode.DoesNotExist:
            code = EmailVerificationCode.objects.create(
                user=user,
                hash=hash)

        return code
