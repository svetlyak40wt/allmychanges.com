import types
import datetime

from nose.tools import eq_ as orig_eq_
from allmychanges.utils import first
from allmychanges.parsing.pipeline import (
    create_section,
    get_markup,
    extract_metadata,
    group_by_path,
    strip_outer_tag,
    prerender_items,
    highlight_keywords,
    parse_file)
from allmychanges.parsing.raw import RawChangelog
from allmychanges.env import Environment


def eq_(left, right):
    if isinstance(left, types.GeneratorType):
        left = list(left)

    if isinstance(right, types.GeneratorType):
        right = list(right)
    orig_eq_(left, right)


class TestRawChangelog(RawChangelog):
    def __init__(self, fixture):
        self.fixture = fixture

    def get_chunks(self):
        for item in self.fixture:
            yield self.create_chunk(title=item['filename'],
                                    content=item['content'])



def test_parsing_files():
    env = Environment()
    create_file = lambda filename, content: env.push(type='file_content',
                                                     filename=filename,
                                                     content=content)
    files = [
        create_file('release-notes/0.1.0.md',
"""
Initial release
===============

I wrote this library as a proof of the concept.
"""),
        create_file('release-notes/0.1.1.md',
"""
Minor changes
===============

This release has small importance.

* Test case was introduced

Final word.
""")]
    versions = list(parse_file(files[0])) + list(parse_file(files[1]))
    eq_(4, len(versions))
    v1, v2, v3, v4 = versions
    eq_('release-notes/0.1.0.md', v1.title)
    eq_('Initial release', v2.title)
    eq_('release-notes/0.1.1.md', v3.title)
    eq_('Minor changes', v4.title)

    sc = v3.content

    eq_("""<h1>Minor changes</h1>\n\n<p>This release has small importance.</p>

<ul><li>Test case was introduced</li>
</ul><p>Final word.</p>""",
        sc)


def test_markup_guesser_from_extension():
    eq_('markdown', get_markup('release-notes/0.1.1.md',
                               "Minor changes"))
    eq_('rst', get_markup('release-notes/0.1.1.rst',
                          "Minor changes"))
    eq_('plain', get_markup('release-notes/Changes',
                          "Minor changes"))


def test_markup_guesser_from_content():
    eq_('rst', get_markup('CHANGES',
                           "Some text with :func:`foo` mentioned."))

    # from https://github.com/celery/celery/blob/3.1/Changelog
    eq_('rst', get_markup('CHANGES',
                           "* [Security: `CELERYSA-0002`_] Insecure default umask."))

    eq_('rst', get_markup('CHANGES',
                           "- **Results**: ``result.get()`` was misbehaving."))

    eq_('markdown', get_markup('CHANGES',
                               """Some header
=========="""))

    eq_('markdown', get_markup('CHANGES',
                               """Some header
--------"""))

    eq_('markdown', get_markup('CHANGES',
                               "## Some 2 level header"))

    eq_('markdown', get_markup('CHANGES',
                               "Some [link](blah)"))

    eq_('markdown', get_markup('CHANGES',
                               "Some [link][blah]"))

    # but
    eq_('plain', get_markup('CHANGES',
                            "Some [thing] in brackets"))


    eq_('plain', get_markup('CHANGES',
"""
0.1:

 * Initial release

0.1.1

 * Added benchmarking script
 * Added support for more serializer modules"""))

    # part of the sbcl's html changelog
    eq_('html', get_markup('Changelog', """<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN" "http://www.w3.org/TR/html4/loose.dtd">
    <html
    ><head
     >"""))


def test_extract_metadata():
    env = Environment()
    env.type = 'almost_version'
    v = lambda **kwargs: env.push(**kwargs)

    input_data = v(title='1.0 (2014-06-24)',
                   content='Fixed issue')

    eq_([v(type='prerender_items',
           title='1.0 (2014-06-24)',
           content='Fixed issue',
           date=datetime.date(2014, 6, 24))],
        extract_metadata(input_data))


def test_prerender_inserts_labels_into_content_items():
    env = Environment()
    env.type = 'almost_version'
    v = lambda **kwargs: env.push(**kwargs)

    input_data = v(type='prerender_items',
                   title='1.0 (2014-06-24)',
                   content='<p>Some bug was <em>fixed</em> issue</p>',
                   date=datetime.date(2014, 6, 24))
    expected = '<p>Some <span class="changelog-highlight-fix">bug</span> was <em><span class="changelog-highlight-fix">fixed</span></em> issue</p>'
    eq_(expected, first(prerender_items(input_data)).processed_content)

    input_data = v(type='prerender_items',
                   title='1.0 (2014-06-24)',
                   content='Fixed issue',
                   date=datetime.date(2014, 6, 24))
    expected = '<span class="changelog-highlight-fix">Fixed</span> issue'
    eq_(expected, first(prerender_items(input_data)).processed_content)


def test_keywords_highlighting():
    eq_('Various <span class="changelog-highlight-fix">bugfixes</span>',
        highlight_keywords('Various bugfixes'))
    eq_('<span class="changelog-highlight-fix">Fixed a bug</span> where blah minor',
        highlight_keywords('Fixed a bug where blah minor'))
    eq_('<span class="changelog-highlight-fix">Bug Fixes</span>',
        highlight_keywords('Bug Fixes'))
    eq_('Some <span class="changelog-highlight-fix">bug</span> was <span class="changelog-highlight-fix">fixed</span>.',
        highlight_keywords('Some bug was fixed.'))
    eq_('<span class="changelog-highlight-fix">Fix</span> an issue.',
        highlight_keywords('Fix an issue.'))
    eq_('<span class="changelog-highlight-fix">Fixes</span> an issue.',
        highlight_keywords('Fixes an issue.'))
    eq_('This function is <span class="changelog-highlight-dep">deprecated</span>.',
        highlight_keywords('This function is deprecated.'))
    eq_('This is a <span class="changelog-highlight-fix">bugfix</span> release.',
        highlight_keywords('This is a bugfix release.'))

    # Backward
    eq_('This feature was <span class="changelog-highlight-inc">removed</span>.',
        highlight_keywords('This feature was removed.'))
    eq_('This change is <span class="changelog-highlight-inc">backward incompatible</span>.',
        highlight_keywords('This change is backward incompatible.'))

    # security
    eq_('Improved <span class="changelog-highlight-sec">XSS</span> filtering',
        highlight_keywords('Improved XSS filtering'))
    eq_('Improved <span class="changelog-highlight-sec">security</span> in SQL',
        highlight_keywords('Improved security in SQL'))

    # multiple
    eq_('attention to <span class="changelog-highlight-fix">bugfixes</span> and <span class="changelog-highlight-sec">security</span> issues',
        highlight_keywords('attention to bugfixes and security issues'))


def test_extract_metadata_is_able_to_detect_unreleased_version():
    env = Environment()
    env.type = 'almost_version'
    v = lambda **kwargs: env.push(**kwargs)

    eq_([v(type='prerender_items',
           title='1.0 (unreleased)',
           unreleased=True,
           content='')],
        extract_metadata(
            v(title='1.0 (unreleased)',
              content='')))

    eq_([v(type='prerender_items',
           title='1.45 (not yet released)',
           unreleased=True,
           content='')],
        extract_metadata(
            v(title='1.45 (not yet released)',
              content='')))

    eq_([v(type='prerender_items',
           title='1.45 (Under Development)',
           unreleased=True,
           content='')],
        extract_metadata(
            v(title='1.45 (Under Development)',
              content='')))

    eq_([v(type='prerender_items',
           title='1.45',
           unreleased=True,
           content='Under Development')],
        extract_metadata(
            v(title='1.45',
              content='Under Development')))


def test_extract_metadata_ignores_unreleased_keywords_if_date_was_found_ealier():
    env = Environment()
    env.type = 'almost_version'
    v = lambda **kwargs: env.push(**kwargs)

    eq_([v(type='prerender_items',
           title='1.0 (2015-02-06)',
           date=datetime.date(2015, 2, 6),
           content='unreleased')],
        extract_metadata(
            v(title='1.0 (2015-02-06)',
              content='unreleased')))



def test_extract_date_only_from_first_three_lines():
    env = Environment()
    env.type = 'almost_version'
    v = lambda **kwargs: env.push(**kwargs)

    eq_(datetime.date(2015, 12, 14),
        first(extract_metadata(
            v(title='1.0',
              content='one\ntwo\n2015-12-14'))).date)

    eq_(None,
        getattr(first(extract_metadata(
            v(title='1.0',
              content='one\ntwo\nthree\n2015-12-14'))), 'date', None))


def test_extract_unreleased_keywords_only_from_first_three_lines():
    env = Environment()
    env.type = 'almost_version'
    v = lambda **kwargs: env.push(**kwargs)

    eq_(True,
        first(extract_metadata(
            v(title='1.0',
              content='one\ntwo\nunreleased'))).unreleased)

    eq_(None,
        getattr(first(extract_metadata(
            v(title='1.0',
              content='one\ntwo\nthree\nunreleased'))), 'unreleased', None))


def test_grouping_by_path():
    env = Environment()
    env.type = 'version'
    v = lambda filename: env.push(filename=filename)

    eq_({'CHANGES': [v('CHANGES')],
         'docs/': [v('docs/notes/0.1.0.rst'),
                   v('docs/notes/0.2.0.rst'),
                   v('docs/README')],
         'docs/README': [v('docs/README')],
         'docs/notes/': [v('docs/notes/0.1.0.rst'),
                         v('docs/notes/0.2.0.rst')],
         'docs/notes/0.1.0.rst': [v('docs/notes/0.1.0.rst')],
         'docs/notes/0.2.0.rst': [v('docs/notes/0.2.0.rst')]},

        group_by_path([v('docs/notes/0.1.0.rst'),
                       v('docs/notes/0.2.0.rst'),
                       v('docs/README'),
                       v('CHANGES')]))


def test_strip_outer_tag():
    # simple case
    eq_('Added new feature.',
        strip_outer_tag('<li>Added new feature.</li>'))

    # a case with embedded html
    eq_('Added <b>new</b> feature.',
        strip_outer_tag('<li>Added <b>new</b> feature.</li>'))

    # a case with newline
    eq_('Added new\n feature.',
        strip_outer_tag('<li>Added new\n feature.</li>'))



    # and now multiline with embedded HTML
    eq_('Added new output <code>twiggy_goodies.logstash.LogstashOutput</code> which\nsends json encoded data via UDP to a logstash server.',
        strip_outer_tag('<li>Added new output <code>twiggy_goodies.logstash.LogstashOutput</code> which\nsends json encoded data via UDP to a logstash server.</li>'))

    # also, it should remove several nested tags too
    eq_('Some text',
        strip_outer_tag('<li><p>Some text</p></li>'))

    # and it shouldn't stuck at such strange things
    eq_('Blah',
        strip_outer_tag('<p>Blah'))

    # but should leave as is if there isn't any common root node
    eq_('<p>Blah</p><p>minor</p>',
        strip_outer_tag('<p>Blah</p><p>minor</p>'))

    # and shouldn't broke on comment lines
    eq_('Blah',
        strip_outer_tag('<!--Comment-->Blah'))



def test_parse_plain_text():
    env = Environment()
    create_file = lambda filename, content: env.push(type='file_content',
                                                     filename=filename,
                                                     content=content)
    file = create_file('Changes',
"""
0.1:

 * Initial release

0.1.1

 * Added benchmarking script
 * Added support for more
   serializer modules""")

    versions = list(parse_file(file))
    eq_(2, len(versions))
    v1, v2 = versions

    eq_('0.1:', v1.title)
    eq_('0.1.1', v2.title)

    eq_('<ul><li>Initial release</li></ul>',
        v1.content)

    eq_('<ul><li>Added benchmarking script</li>'
        '<li>Added support for more\nserializer modules</li></ul>',
        v2.content)


def test_parse_redispy_style_plain_text():
    env = Environment()
    create_file = lambda filename, content: env.push(type='file_content',
                                                     filename=filename,
                                                     content=content)
    file = create_file('Changes',
"""* 2.10.2
    * Added support for Hiredis's new bytearray support. Thanks
      https://github.com/tzickel
    * Fixed a bug when attempting to send large values to Redis in a Pipeline.
* 2.10.1
    * Fixed a bug where Sentinel connections to a server that's no longer a
      master and receives a READONLY error will disconnect and reconnect to
      the master.""")

    versions = list(parse_file(file))

    eq_(2, len(versions))
    v1, v2 = versions

    eq_('* 2.10.2', v1.title)
    eq_('* 2.10.1', v2.title)

    eq_('<ul><li>Added support for Hiredis\'s new bytearray support. Thanks\nhttps://github.com/tzickel</li>'
        '<li>Fixed a bug when attempting to send large values to Redis in a Pipeline.</li></ul>',
        v1.content)

    eq_('<ul><li>Fixed a bug where Sentinel connections to a server that\'s no longer a\nmaster and receives a READONLY error will disconnect and reconnect to\nthe master.</li></ul>',
        v2.content)
