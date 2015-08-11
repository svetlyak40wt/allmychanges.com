import os
import shutil
import envoy
import tempfile

from django.conf import settings
from collections import defaultdict
from allmychanges.downloaders.utils import normalize_url
from allmychanges.utils import (
    cd, get_text_from_response, is_http_url,
    first_sentences,
    html_document_fromstring)


def guess(source, discovered={}):
    result = defaultdict(dict)
    source, username, repo = normalize_url(source)

    path = ''
    try:
        path = download(source)
        # if everything is OK, start populating result
        result['changelog']['source'] = source
        if username and repo:
            result['params'].update(dict(username=username, repo=repo))
    except:
        # ignore errors because most probably, they are from
        # hg command which won't be able to clone repository
        # from strange url
        pass
    finally:
        if os.path.exists(path):
            shutil.rmtree(path)

    return result


def download(source,
             search_list=[],
             ignore_list=[]):
    path = tempfile.mkdtemp(dir=settings.TEMP_DIR)
    url = source.replace('hg+', '')

    with cd(path):
        response = envoy.run('hg clone {url} {path}'.format(url=url,
                                                             path=path))
    if response.status_code != 0:
        if os.path.exists(path):
            shutil.rmtree(path)
        raise RuntimeError('Bad status_code from hg clone: {0}. '
                           'Mercurial\'s stderr: {1}'.format(
                               response.status_code, response.std_err))

    return path