# coding: utf-8
import re
import codecs
import copy
import itertools
import os
import tempfile
import shutil
import envoy

from operator import itemgetter
from collections import defaultdict

from allmychanges.crawler import _extract_version, _extract_date
from allmychanges.utils import get_commit_type
from allmychanges.env import Environment
from django.conf import settings


def compare_versions(left, right):
    return left.date < right.date \
        and left.version < right.version


def parse_changelog(raw_changelog):
    # TODO: похоже этот код нигде не используется
    chunks = raw_changelog.get_chunks()
    versions = itertools.chain(*[chunk.get_versions()
                                 for chunk in chunks])
    versions = sorted(versions, compare_versions)
    return versions


##############################
## NEW
##############################


def get_files(env):
    ignore_list = env.ignore_list

    def in_ignore_list(filename):
        for ignore_prefix in ignore_list:
            if filename.startswith(ignore_prefix):
                return True
        return False

    for root, dirs, files in os.walk(env.dirname):
        for filename in files:
            full_filename = os.path.join(root, filename)
            rel_filename = os.path.relpath(full_filename, env.dirname)
            if not in_ignore_list(rel_filename):
                yield env.push(
                    type='filename',
                    filename=full_filename)


# TODO: remove
def read_files(root, filenames):
    for filename in filenames:
        with codecs.open(filename, 'r', 'utf-8') as f:
            try:
                content = create_file(
                    os.path.relpath(filename, root),
                    f.read())
            except Exception:
                continue
            
            yield content


def read_file(obj):
    with codecs.open(obj.filename, 'r', 'utf-8') as f:
        try:
            yield obj.push(
                type='file_content',
                filename=os.path.relpath(obj.filename, obj.dirname),
                content=f.read())
        except Exception:
            pass


# TODO: remove
def parse_files(file_objects):
    """Outputs separate sections (header + notes/items)
    from every file.
    """
    for obj in file_objects:
        markup = get_markup(obj)
        if markup is not None:
            versions = globals().get('parse_{0}_file'.format(markup))(obj)
            for version in versions:
                # this information will be required late
                version['filename'] = get_file_name(obj)
                yield version

def parse_file(env):
    """Outputs separate sections (header + notes/items)
    from every file.
    """
    markup = get_markup(env.filename, env.content)
    if markup is not None:
        versions = globals().get('parse_{0}_file'.format(markup))(env)
        for version in versions:
            yield version



def parse_markdown_file(obj):
    import markdown2
    html = markdown2.markdown(obj.content)
    return parse_html_file(obj.push(content=html))


def search_conf_py(root_dir, doc_filename):
    parts = doc_filename.split('/')
    while parts:
        parts = parts[:-1]
        filename = os.path.join(
            root_dir, '/'.join(parts), 'conf.py')
        if os.path.exists(filename):
            return filename


def parse_rst_file(obj):
    path = obj.cache.get('rst_builder_temp_path')
    if path is None:
        path = tempfile.mkdtemp(dir=settings.TEMP_DIR)
        obj.cache['rst_builder_temp_path'] = path

    conf_py = obj.cache.get('rst_conf_py')
    if conf_py is None:
        conf_py = search_conf_py(obj.dirname, obj.filename)
        if conf_py:
            obj.cache['rst_conf_py'] = conf_py

            # if there is a config for sphinx
            # then use it!
            shutil.copy(conf_py, os.path.join(path, 'conf.py'))

            # also, use all directories starting from underscore
            # for django it is _ext and _themes, for other
            # projects it maky work or not
            # if it does not work for some important projects, then
            # we'll need a more wise algorithm which will try
            # to figure out conf.py dependencies

            conf_py_dir = os.path.dirname(conf_py)
            for name in os.listdir(conf_py_dir):
                fullname = os.path.join(conf_py_dir, name)
                if name.startswith('_') and os.path.isdir(fullname):
                    destination = os.path.join(path, name)
                    shutil.copytree(fullname, destination)
        else:
            obj.cache['rst_conf_py'] = 'was created'

        with codecs.open(os.path.join(path, 'conf.py'), 'a', 'utf-8') as f:
            f.write("master_doc = 'index'\n")
            f.write("source_suffix = '.rst'\n")

    with codecs.open(os.path.join(path, 'index.rst'), 'w', 'utf-8') as f:
        f.write(obj.content)

    envoy.run('rm -fr {0}/output/index.html'.format(path))
    envoy.run('sphinx-build -b html {0} {0}/output'.format(path))

    with codecs.open(os.path.join(path, 'output', 'index.html'), 'r', 'utf-8') as f:
        html = f.read()

    return parse_html_file(obj.push(content=html))


# TODO: remove
def create_section(title, content=[], version=None, date=None):
    """Each section has a title and a list of content objects.
    Each content object is either a text or a list object.
    """
    result = dict(title=title, content=content)
    if version:
        result['version'] = version
    result['date'] = date
    return result


get_section_title = itemgetter('title')
get_section_content = itemgetter('content')


def parse_html_file(obj):
    import lxml.html
    parsed = lxml.html.document_fromstring(obj.content)
    headers = [tag for tag in parsed.iter()
               if tag.tag in ('h1', 'h2', 'h3', 'h4')]


    def create_list(el):
        def inner_html(el):
            text = lxml.html.tostring(el).strip()
            text = re.sub(ur'^<{tag}>(.*)</{tag}>$'.format(tag=el.tag),
                          ur'\1', text)
            return text
    
        return map(inner_html, el.getchildren())

    def create_notes(children):
        current_text = ''
        for el in children:
            if el.tag == 'ul':
                if current_text:
                    yield current_text.strip()
                    current_text = ''
                yield create_list(el)
            else:
                current_text += lxml.html.tostring(el)

        if current_text:
            yield current_text.strip()

    headers = [(header.tag, header.text, header.itersiblings())
               for header in headers]

    # use whole document and filename as a section
    headers.insert(0, ('h0',
                       obj.filename,
                       parsed.find('body').iterchildren()))
    
    for tag, text, all_children in headers:
        children = itertools.takewhile(
            lambda ch: not ch.tag.startswith('h') or ch.tag > tag,
            all_children)
        sections = list(create_notes(children))
        yield obj.push(type='file_section',
                       title=text,
                       content=sections)


def create_file(name, content):
    return dict(name=name, content=content)

get_file_name = itemgetter('name')
get_file_content = itemgetter('content')


def get_markup(filename, content):
    filename = filename.lower()

    if ':func:`' in content:
        return 'rst'

    if 'change' in filename \
       or filename.endswith('.md') \
       or filename.endswith('.rst'):
        # for now only markdown is supported
        return 'markdown'


def filter_versions(sections):
    """Searches parts of the files which looks like
    changelog pieces.
    """
    for section in sections:
        version = _extract_version(get_section_title(section))
        if version:
            new_section = copy.deepcopy(section)
            new_section['version'] = version
            yield new_section


def filter_version(section):
    """Searches parts of the files which looks like
    changelog pieces.
    """
    version = _extract_version(section.title)
    if version:
        yield section.push(type='almost_version',
                           version=version)


def extract_metadata(version):
    """Tries to extract date and list items' type.
    """
    def _all_dates(iterable):
        for item in iterable:
            date = _extract_date(item)
            if date:
                yield date

    def _all_list_items():
        for content_part in version.content:
            if isinstance(content_part, list):
                for item in content_part:
                    yield item
            elif isinstance(content_part, basestring):
                yield content_part

    def mention_unreleased(text):
        keywords = ('unreleased', 'under development',
                    'release date to be decided')
        lowered = text.lower()
        for keyword in keywords:
            if keyword in lowered:
                return True
        return False

    all_dates = _all_dates(itertools.chain([version.title],
                                           _all_list_items()))

    new_version = version.push(type='version')
    try:
        new_version.date = all_dates.next()
    except StopIteration:
        pass

    if mention_unreleased(version.title):
        new_version.unreleased = True

    def process_content(content_part):
        if isinstance(content_part, basestring):
            if mention_unreleased(content_part):
                new_version.unreleased = True
                
            return content_part
        else:
            return [{'type': get_commit_type(item),
                     'text': item}
                    for item in content_part]
    new_version.content = map(process_content, version.content)
    yield new_version


def group_by_path(versions):
    """ Returns a dictionary, where keys are a strings
    with directories, starting from '.' and values
    are lists with all versions found inside this directory.
    """
    result = defaultdict(list)
    for version in versions:
        path = version.filename.split(u'/')
        path = [name + u'/'
                for name in path[:-1]] + path[-1:]

        while path:
            result[''.join(path)].append(version)
            path = path[:-1]
    return result


def processing_pipe(root, ignore_list=[]):
    root_env = Environment()
    root_env.type = 'directory'
    root_env.dirname = root
    root_env.ignore_list = ignore_list
    # a dictionary to keep data between different processor's invocations
    root_env.cache = {}

    processors = dict(directory=get_files,
                      filename=read_file,
                      file_content=parse_file,
                      file_section=filter_version,
                      almost_version=extract_metadata)

    def run_pipeline(obj, get_processor=lambda obj: None):
        processor = get_processor(obj)
        if processor is None:
            yield obj
        else:
            for new_obj in processor(obj):
                for obj in run_pipeline(new_obj,
                                        get_processor=get_processor):
                    yield obj

    versions = list(
        run_pipeline(root_env,
                     get_processor=lambda obj: processors.get(obj.type)))

    if not versions:
        return []

    def compare_versions(left, right):
        if left.version < right.version:
            return -1
        if left.version > right.version:
            return 1

        left_keys_count = len(left.keys())
        right_keys_count = len(right.keys())
        
        if left_keys_count < right_keys_count:
            return -1
        if left_keys_count > right_keys_count:
            return 1

        return 0

    # now we'll select a source with maximum number of versions
    # but not the root directory
    grouped = group_by_path(versions).items()
    grouped.sort(key=lambda item: len(item[1]), reverse=True)
    versions = grouped[0][1]

    # using customized comparison we make versions which
    # have less metadata go first
    versions.sort(cmp=compare_versions)
    # and grouping them by version number
    # we leave only unique versions with maximum number of metadata

    versions = dict((version.version, version)
                    for version in versions)

    # and finally we'll prepare a list, sorted by version number
    versions = versions.values()
    versions.sort(key=lambda item: item.version)

    for key, value in root_env.cache.items():
        if key.endswith('_temp_path'):
            shutil.rmtree(value)

    return versions



# file|parse -> sections|filter_versions -> create_raw_versions|sort
