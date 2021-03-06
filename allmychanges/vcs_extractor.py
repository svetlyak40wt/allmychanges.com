# coding: utf-8

import anyjson
import envoy
import os
import os.path
import setuptools as orig_setuptools
import sys
import string
import operator
import datetime

from itertools import islice, dropwhile, izip_longest
from collections import defaultdict, deque
from orderedset import OrderedSet
from allmychanges.utils import cd, trace
from allmychanges.crawler import _extract_date, _extract_version, RE_BUMP_LINE
from django.utils.encoding import force_str


from twiggy_goodies.threading import log


def do(command):
#        print 'EXEC>', command
    r = envoy.run(force_str(command))
    assert r.status_code == 0, '"{0} returned code {1} and here is it\'s stderr:{2}'.format(
        command, r.status_code, r.std_err)
    return r


def process_vcs_message(text):
    lines = text.split(u'\n')
    lines = (line for line in lines
             if RE_BUMP_LINE.match(line) is None)
    lines = dropwhile(operator.not_, lines)
    return u'<br/>\n'.join(lines)


def find_tagged_versions():
    """Returns a map {hash -> version_number}
    """
    def tag_to_hash(tag):
        r = do('git rev-parse ' + tag + '^{}')
        return r.std_out.strip()

    r = do('git tag')
    tags = r.std_out.split('\n')
    tags = ((tag, _extract_version(tag)) for tag in tags)
    result = dict(
        (tag_to_hash(tag), version)
        for tag, version in tags
        if version is not None)
    return result




def git_history_extractor(path, limit=None):
    """Returns list of dicts starting from older commits to newer.
    Each dict should contain fields: `date`, `message`, `hash`, `checkout`,
    where `checkout` is a function which makes checkout of this version
    and returns path which will be passed to the version extractor.
    Optionally, dict could contain `version` field, taken from tag.
    """
    splitter = '-----======!!!!!!======-----'
    ins = '--!!==!!--'

    with cd(path):
        def gen_checkouter(hash_):
            def checkout():
                with cd(path):
                    result = do('git status --porcelain')
                    # if state is dirty, we just commit these changes
                    # into a orphaned commit
                    if result.std_out:
                        if '??' in result.std_out:
                            do('git clean -f')

                        try:
                            do('git add -u')
                            do('git commit -m "Trash"')
                        except:
                            pass

                    do('git checkout ' + hash_)
                return path
            return checkout

        if os.environ.get('IGNORE_TAGS'):
            tagged_versions = []
        else:
            tagged_versions = find_tagged_versions()

            with log.fields(num_tagged_versions=len(tagged_versions)):
                log.info('Found tagged versions')

        r = do('git log --pretty=format:"%H%n{ins}%n%ai%n{ins}%n%B%n{ins}%n%P%n{splitter}"'.format(ins=ins, splitter=splitter))

        # containse tuples (_hash, date, msg, parents)

        response = r.std_out.decode('utf-8', errors='replace')
        groups = (map(string.strip, group.strip().split(ins))
                  for group in response.split(splitter)[:-1])

        result = (dict(date=_extract_date(date),
                       message=process_vcs_message(msg),
                       checkout=gen_checkouter(_hash),
                       hash=_hash,
                       parents=parents.split())
                  for _hash, date, msg, parents in groups)
        result = list(result)

        if limit:
            result = islice(result, 0, limit)
        result = list(result)

        root = result[0]['hash']

        def add_tagged_version(item):
            if item['hash'] in tagged_versions:
                item['version'] = tagged_versions[item['hash']]
            return item

        result = dict((item['hash'], add_tagged_version(item))
                      for item in result)
        result['root'] = result[root]

        def show(hh):
            # Function to view result item by partial hash
            # used with set_trace ;)
            for key, value in result.items():
                if key.startswith(hh):
                    return value

        return result, tagged_versions


def choose_history_extractor(path):
    if isinstance(path, dict):
        # this is a special case for tests
        def test_history_extractor(path):
            def create_version(item):
                new_item = item.copy()
                new_item['checkout'] = lambda: item['version']
                del new_item['version']
                return new_item
            commits = dict((key, create_version(item))
                           for key, item in path.items())
            # make root to point to the tip of the
            # commit history instead of pointing to a separate
            # object
            commits['root'] = commits[commits['root']['hash']]
            tagged_versions = {}
            return commits, tagged_versions
        return test_history_extractor

    return git_history_extractor


def python_version_extractor(path, use_threads=True):
    from multiprocessing import Process, Queue
    from collections import deque

    if use_threads:
        queue = Queue()
        process = Process(target=python_version_extractor_worker,
                          args=(path, queue))
        process.start()
        process.join()
        return queue.get()
    else:
        class Queue(deque):
            def put(self, value):
                self.append(value)
            def get(self):
                return self.popleft()
        queue = Queue()
        python_version_extractor_worker(path, queue)
        return queue.get()


def python_version_extractor_worker(path, queue):
    with cd(path):
        if os.path.exists('setup.py'):
            envoy.run("find . -name '*.pyc' -delete")

            try:
                metadata = {}

                class FakeSetuptools(object):
                    def setup(self, *args, **kwargs):
                        metadata.update(kwargs)

                    def __getattr__(self, name):
                        return getattr(orig_setuptools, name)

                sys.modules['distutils.core'] = FakeSetuptools()
                sys.modules['setuptools'] = FakeSetuptools()
                sys.path.insert(0, os.getcwd())

                try:
                    from setup import setup
                except:
                    pass

                version = metadata.get('version')
                queue.put(version)
            finally:
                pass
    queue.put(None)


def npm_version_extractor(path):
    filename = os.path.join(path, 'package.json')

    if os.path.exists(filename):
        with open(filename) as f:
            try:
                data = anyjson.deserialize(f.read())
                return data.get('version')
            except Exception:
                pass


def choose_version_extractor(path):
    if isinstance(path, dict):
        # this is a special case for tests
        def test_version_extractor(path):
            return path
        return test_version_extractor

    if os.path.exists(os.path.join(path, 'setup.py')):
        return python_version_extractor

    if os.path.exists(os.path.join(path, 'package.json')):
        return npm_version_extractor


def stop_at_hash_if_needed(hash):
    if os.environ.get('STOP_AT_HASH'):
        hashes = os.environ['STOP_AT_HASH'].split(',')
        for hash_to_check in hashes:
            if hash.startswith(hash_to_check):
                import pdb; pdb.set_trace()


def _add_version_number(commit, extract_version):
    if 'version' not in commit:
        stop_at_hash_if_needed(commit['hash'])
        checkout_path = commit['checkout']()
        version = extract_version(checkout_path)
        commit['version'] = version
    return commit['version']


# TODO we don't need it anymore
def _normalize_version_numbers(commits):
    already_seen = defaultdict(int)
    previous_number = None

    # We need this step to normalize version order
    # because sometimes after the merge it is currupted
    stop_at_hash = os.environ.get('STOP_AT_HASH')

    for commit in commits:
        number = commit['version']
        if number is not None and previous_number is not None and number != previous_number and number in already_seen:
            # fixing commit merged after the version
            # was bumped
            if stop_at_hash and commit['hash'].startswith(stop_at_hash):
                import pdb; pdb.set_trace()

            commit['version'] = previous_number
        else:
            already_seen[number] += 1
            previous_number = number

def write_vcs_versions_bin(commits, extract_version):
    # first, we'll skip heading commits without version number
    stop_at_hash = os.environ.get('STOP_AT_HASH')
    idx = 0
    number = _add_version_number(commits[idx], extract_version)

    while number is None:
        idx += 1
        number = _add_version_number(commits[idx], extract_version)

    # and now we need to go through the history recursivly
    # dividing it twice on each step
    def rec(commits, extract_version):
        left_version = commits[0]['version']
        right_version = _add_version_number(commits[-1], extract_version)

        if len(commits) == 2:
            if right_version is None:
                commits[-1]['version'] = left_version
        else:
            if left_version == right_version:
                for commit in commits[1:-1]:
                    if stop_at_hash and commit['hash'].startswith(stop_at_hash):
                        import pdb; pdb.set_trace()
                    commit['version'] = left_version
            else:
                threshold = len(commits) / 2
                rec(commits[:threshold + 1], extract_version)
                rec(commits[threshold:], extract_version)

    rec(commits[idx:], extract_version)
    _normalize_version_numbers(commits)


def _normalize_version_numbers2(commits):
    # fills commits in gaps with 'version' attribute
    updated = set()
    to_update = set()
    to_check = deque()

    def show(hh):
        # Function to view result item by partial hash
        # used with set_trace ;)
        for key, value in commits.items():
            if key.startswith(hh):
                return value

    for commit in commits.values():
        if commit.get('version') is None:
            to_check.clear()
            to_update.clear()
            current = commit

            while current and current.get('version') is None and current['hash'] not in updated:
                to_update.add(current['hash'])
                for hash_ in current['parents']:
                    if hash_ not in to_update:
                        to_check.append(hash_)

                try:
                    hash_ = to_check.popleft()
                    current = commits.get(hash_)
                except Exception:
                    current = None

            for hash_ in to_update:
                commit = commits.get(hash_)
                commit['version'] = current['version'] if current else None
                updated.add(hash_)


def write_vcs_versions_slowly(commits, extract_version):

    for idx, commit in enumerate(commits.values()):
        _add_version_number(commit, extract_version)

    _normalize_version_numbers2(commits)


def write_vcs_versions_bin_helper(commits, extract_version):
    """Recursively writes versions to continuous chain of commits.
    Each commit should be decedant or ancestor of its nearest neiboughrs.
    """

    # first, we'll skip commits from head and tail without version numbers
    number = _add_version_number(commits[0], extract_version)
    while number is None:
        commits = commits[1:]
        if not commits:
            break
        number = _add_version_number(commits[0], extract_version)

    if not commits:
        return

    number = _add_version_number(commits[-1], extract_version)
    while number is None:
        commits = commits[:-1]
        if not commits:
            break
        number = _add_version_number(commits[-1], extract_version)

    if not commits:
        return

    # and now we need to go through the history recursivly
    # dividing it twice on each step
    def rec(commits, extract_version):
        left_version = commits[0]['version']
        right_version = _add_version_number(commits[-1], extract_version)

        if len(commits) > 2:
            if left_version and left_version == right_version:
                for commit in commits[1:-1]:
                    commit['version'] = left_version
            else:
                threshold = len(commits) / 2
                rec(commits[:threshold + 1], extract_version)
                rec(commits[threshold:], extract_version)

    rec(commits, extract_version)



def write_vcs_versions_fast(commits, extract_version):
    queue = deque()
    commit = commits['root']
    covered = set()

    def enque(hash):
        if hash not in covered:
            commit = commits.get(hash)
            if commit is not None:
                queue.append(commit)


    while commit:
        # fast forward
        commits_between = []
        # a hack to reuse version number from a previously calculated commit
        if 'child' in commit:
            commits_between.append(commit['child'])
        limit = 200
        forward = commit
        while forward['hash'] not in covered and limit > 0:
            covered.add(forward['hash'])
            commits_between.append(forward)

            num_parents = len(forward['parents'])
            if num_parents > 1:
                map(enque, forward['parents'][1:])
                for it_hash in forward['parents'][1:]:
                    it_commit = commits.get(it_hash)
                    if it_commit:
                        it_commit['child'] = forward
            if num_parents > 0:
                forward = commits[forward['parents'][0]]

            limit -= 1

        covered.add(forward['hash'])
        # we add this point nonetheless it could be already covered
        # because if it is covered, then we don't need to calculate
        # version number again
        commits_between.append(forward)

        write_vcs_versions_bin_helper(commits_between, extract_version)
        map(enque, forward['parents'])

        try:
            commit = queue.pop()
        except IndexError:
            commit = None

    _normalize_version_numbers2(commits)


def messages_to_html(messages):
    items = [u'<ul>']
    items.extend(map(u'<li>{0}</li>'.format, messages))
    items.append(u'</ul>')
    return u''.join(items)


def get_versions_from_vcs(env):
    path = env.dirname

    get_history = choose_history_extractor(path)
    commits, tagged_versions = get_history(path)

    # we only go through the history
    # if version extractor is available for this repository
    extract_version = choose_version_extractor(path)

    if extract_version is not None:
        write_vcs_versions_fast(commits, extract_version)
    else:
        # if we only use information from tags,
        # we need to fill gaps between tags
        _normalize_version_numbers2(commits)

    # now we'll check if some version information was
    # extracted
    has_versions = sum(1 for commit in commits.values()
                       if 'version' in commit)

    if has_versions:
        bumps = mark_version_bumps(commits, tagged_versions)
        grouped = group_versions(commits, bumps)
        for version in grouped:
            yield env.push(type='almost_version',
                            title=version['version'],
                            version=version['version'],
                            filename='VCS',
                            date=None if version.get('unreleased') else version['date'],
                            unreleased=version.get('unreleased', False),
                            content=messages_to_html(version['messages']))


def iterate_over_commits(tree, start_hash, upto=None):
    """Returns iterable over all hashes in the tree,
    starting from `start_hash` and up to `upto`.

    When merge commit encountered, algorithm first iterates
    the left branch until a fork point, then right branch
    until a fork point, and continues from the fork point.

    Notice! This algorithm does not work with tries where
    more than 2 branches are merged in 1 point.
    """

    current_hash = start_hash
    while current_hash and current_hash != upto:
        yield current_hash
        item = tree[current_hash]
        parents = item['parents']
        num_parents = len(parents)

        if num_parents > 1:
            fork_point = find_fork_point(tree, *parents)

            # yield both branches
            for parent in parents:
                for commit in iterate_over_commits(
                        tree,
                        parent,
                        upto=fork_point):
                    yield commit

            # then jump to fork point
            current_hash = fork_point
        elif num_parents == 1:
            current_hash = parents[0]
        else:
            current_hash = None


def find_fork_point(tree, left, right):
    """Returns a single parent commit common for two branches
    designated by `left` and `right` hashes.
    """

    left_ancestors = iterate_over_commits(tree, left)
    right_ancestors = iterate_over_commits(tree, right)
    zipped_branches = izip_longest(left_ancestors, right_ancestors)

    left_set = set()
    right_set = set()

    for left_hash, right_hash in zipped_branches:
        if left_hash is not None:
            left_set.add(left_hash)

        if right_hash is not None:
            right_set.add(right_hash)

        intersection = left_set.intersection(right_set)

        # if there is an intersection between these two
        # sets, then it should contain only one hash
        # and this hash is the fork point
        if intersection:
            assert len(intersection) == 1
            return intersection.pop()


def mark_version_bumps(tree, tagged_versions=None):
    """Returns hashes where version was incremented.

    If tagged_versions given, it should be a dict
    {hash -> version}. In this case, hashes for this
    dict will be considered as increments to versions
    from this dict.
    """
    bumps = []
    queue = deque()
    processed = set()
    tagged_versions = tagged_versions or {}

    def add_bump(hash, version, date):
#        print 'add', hash[:7], version
        found = None
        for idx, bump in enumerate(bumps):
            if bump[1] == version:
                found = idx
                break
        if found is not None:
            if bumps[found][2] > date:
                del bumps[idx]
                bumps.append((hash, version, date))
        else:
            bumps.append((hash, version, date))

    queue.extend(
        map(
            tree.get,
            iterate_over_commits(
                tree,
                tree['root']['hash'],
            )
        )
    )

    commit = queue.popleft()

    while commit:
        hash = commit['hash']

        if hash not in processed:
#            print hash[:7]
            stop_at_hash_if_needed(hash)

            version = commit['version']
            if version is not None:
                parents = map(tree.get, commit['parents'])
                # if history was limited, then some parents could be unavailable
                parents = filter(None, parents)

                # if all parents have different version, then
                # this version was incremented in this commit
                if hash in tagged_versions:
                    version = tagged_versions[hash]
                    # we use 1970-01-01 to make tagged version have
                    # advantage over versions guessed using other ways
                    dt = datetime.date(1970, 01, 01)
                    add_bump(hash, version, dt)

                elif not any(map(lambda parent: parent['version'] == version,
                   parents)):
                    add_bump(hash, version, commit['date'])

            processed.add(hash)
        try:
            commit = queue.popleft()
        except IndexError:
            commit = None
    return [bump[0] for bump in reversed(bumps)]


def mark_version_bumps_rec(tree):
    """Returns hashes of commits where
    version changes.
    """
    @trace
    def rec(commit, cache={}):
        hash = commit['hash']
        if hash in cache:
            return cache[hash]

        version = commit['version']
        parents = map(tree.get, commit['parents'])

        parent_bumps = reduce(operator.__or__,
                              (rec(parent, cache=cache) for parent in parents),
                              OrderedSet())

        if not any(map(lambda parent: parent['version'] == version,
                   parents)):
            if version:
                cache[hash] =  parent_bumps | OrderedSet([hash])
                return cache[hash]

        cache[hash] = parent_bumps
        return cache[hash]


    return rec(tree['root'])


def group_versions(tree, bumps):
    root_hash = tree['root']['hash']
    if root_hash not in bumps:
        bumps.append(root_hash)
        probably_has_unreleased_commits = True
    else:
        probably_has_unreleased_commits = False

    processed = set()
    def collect_messages(commit):
        messages = []
        queue = deque()

        while commit is not None:
            if commit['hash'] not in processed:
                # we ignore merge commits where parents > 1
                if len(commit['parents']) < 2 and commit['message']:
                    messages.append(commit['message'])

                processed.add(commit['hash'])
                queue.extend(filter(None, map(tree.get, commit['parents'])))

            try:
                commit = queue.popleft()
            except IndexError:
                commit = None

        return messages

    result = []

    for bump in bumps:
        commit = tree[bump].copy()
        messages = collect_messages(commit)
        if not messages:
            continue

        commit['messages'] = messages
        del commit['message']
        result.append(commit)

    if probably_has_unreleased_commits and result and result[-1]['hash'] == root_hash:
        result[-1]['version'] = 'x.x.x'
        result[-1]['unreleased'] = True
    return result
