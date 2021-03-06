from nose.tools import eq_
from allmychanges.crawler import _extract_version


def test_bad_lines():
    eq_(None, _extract_version('des-cbc           3624.96k     5258.21k     5530.91k     5624.30k     5628.26k'))
    eq_(None, _extract_version('16'))
    eq_(None, _extract_version('IPv6'))


def test_good_lines():
    eq_(u'3.0.1', _extract_version(u'rn3.0.1.php'))
    eq_(u'1.1beta2', _extract_version(u'rn1.1beta2.php'))
    eq_(u'17', _extract_version(u'view/Kodi_v17_(Krypton)_changelog.html'))
    eq_(u'17', _extract_version(u'view/Kodi-v17-(Krypton)-changelog.html'))
    eq_(u'16', _extract_version(u'v16'))
    eq_(u'16', _extract_version(u'Release v16'))
    eq_(u'16', _extract_version(u'v16-foo'))
    eq_(u'16', _extract_version(u'v16_bar'))
    eq_(u'2015', _extract_version(u'r2015'))
    eq_(u'16.0.1', _extract_version(u'v16.0.1'))
    eq_(u'1.2.2p1', _extract_version(u'1.2.2p1'))
    eq_(u'1.2.2p1', _extract_version(u'release 1.2.2p1'))
    eq_(u'1.2.2p1', _extract_version(u'release-1.2.2p1'))
    eq_(u'1.2.2p1', _extract_version(u'txt/release-1.2.2p1'))
    eq_('0.9.2b', _extract_version('Version 0.9.2b  [22 Mar 1999]'))
    eq_('1.0a3', _extract_version('Version 1.0a3'))
    eq_('1.0a', _extract_version('Version 1.0a'))
    eq_('1.0.1k', _extract_version('Version 1.0.1k'))
    eq_('2015.1.21', _extract_version('v2015.1.21 (released 2015-1-21)'))
    eq_('2015.1.21', _extract_version('r2015.1.21 (released 2015-1-21)'))
