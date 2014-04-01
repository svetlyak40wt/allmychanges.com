# -*- coding: utf-8 -*-
from django.utils import timezone

from rest_framework import viewsets, mixins
from rest_framework.exceptions import ParseError
from rest_framework_extensions.mixins import DetailSerializerMixin
from rest_framework_extensions.decorators import action
from rest_framework.response import Response

from allmychanges.models import Repo, Subscription, Package
from allmychanges.api.serializers import (
    RepoSerializer,
    RepoDetailSerializer,
    CreateChangelogSerializer,
    SubscriptionSerializer,
    PackageSerializer,
)
from allmychanges.utils import count


class HandleExceptionMixin(object):
    def handle_exception(self, exc):
        count('api.exception')
        if isinstance(exc, ParseError):
            return Response(data={u'error_messages': exc.detail},
                            status=400,
                            exception=exc)
        return super(HandleExceptionMixin, self).handle_exception(exc=exc)


class RepoViewSet(HandleExceptionMixin,
                  DetailSerializerMixin,
                  viewsets.ReadOnlyModelViewSet):
    queryset = Repo.objects.all()
    serializer_class = RepoSerializer
    serializer_detail_class = RepoDetailSerializer
    queryset_detail = queryset.prefetch_related('versions__items__changes')

    @action(is_for_list=True, endpoint='create-changelog')
    def create_changelog(self, request, *args, **kwargs):

        serializer = CreateChangelogSerializer(data=request.DATA)
        if serializer.is_valid():
            count('api.create.changelog')
            repo = Repo.start_changelog_processing_for_url(
                url=serializer.data['url'])

            return Response(data={'id': repo.id})
        else:
            raise ParseError(detail=serializer.errors)


class SubscriptionViewSet(HandleExceptionMixin,
                          mixins.CreateModelMixin,
                          viewsets.GenericViewSet):
    model = Subscription
    serializer_class = SubscriptionSerializer

    def create(self, request, *args, **kwargs):
        request.DATA['date_created'] = timezone.now()
        count('api.create.subscription')
        return super(SubscriptionViewSet, self) \
            .create(request, *args, **kwargs)


class PackageViewSet(HandleExceptionMixin,
                     DetailSerializerMixin,
                     viewsets.ModelViewSet):
    serializer_class = PackageSerializer
    serializer_detail_class = PackageSerializer


    def get_queryset(self, *args, **kwargs):
        return self.request.user.packages.all()

    def pre_save(self, obj):
        obj.user = self.request.user
        obj.next_update_at = obj.created_at = timezone.now()
        return super(PackageViewSet, self).pre_save(obj)
