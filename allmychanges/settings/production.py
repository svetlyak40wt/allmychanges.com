import os
from .default import *  # nopep8


DEBUG = False
TEMPLATE_DEBUG = DEBUG

REST_FRAMEWORK.update({
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    )
})


DATABASES['default'].update({
    'NAME': 'allmychanges',
})

GRAPHITE_PREFIX = 'allmychanges'

ALLOWED_HOSTS = ['allmychanges.com', 'localhost']

METRIKA_ID = '22434466'
ANALYTICS_ID = 'UA-49927178-1'

SLACK_URL = 'https://hooks.slack.com/services/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'
KATO_URL = 'https://api.kato.im/rooms/fc6183a8a19599ff6a148a35870878414fcc5a573515c043d07b6529281dd630/simple'

LOG_FILENAME = '/var/log/allmychanges/django-root.log'
try:
    init_logging(LOG_FILENAME, logstash=True)
except OSError:
    pass


if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)
