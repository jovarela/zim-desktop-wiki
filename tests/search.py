
# Copyright 2011-2025 Jaap Karssenberg <jaap.karssenberg@gmail.com>

import tests

from zim.parse.searchquery import *
from zim.search import *
from zim.notebook import Path
from zim.plugins import indexed_fts


# some aliases to define queries
_t = SearchQueryTerm
_any = lambda t: SearchQueryTerm('any', t)
_and = lambda *t: SearchQuery(OPERATOR_AND, t)
_or = lambda *t: SearchQuery(OPERATOR_OR, t)
_q = _and # toplevel is default an AND group


def _not(t):
	t.negate = True
	return t


class TestParseSearchQuery(tests.TestCase):

	def testKeywordParsing(self):
		keywords = {'links'}
		for string, wanted in (
			('links:Foo', _q(_t('links', 'Foo'))),
			('links: Foo', _q(_t('links', 'Foo'))),
			('Links:Foo', _q(_t('links', 'Foo'))),
			('Links: Foo', _q(_t('links', 'Foo'))),
			('Links:', _q(_any('Links:'))),
				# edge case, looking for literal occurrence, not a keyword, falls back to default
			('"Links:Foo"',	_q(_any('Links:Foo'))),
				# quoted string, not a keyword, falls back to default
			('links Foo', _and(_any('links'), _any('Foo'))),
		):
			query = parse_search_query(string, keywords)
			#print('====', string, '\n', query, '\n', wanted)
			self.assertEqual(query, wanted)

	def testImplicitKeywordMatch(self):
		keywords = {'tag': {'regex': search_tag_re}}
		for string, wanted in (
			('tag:Foo', _q(_t('tag', 'Foo'))),
			('@Foo', _q(_t('tag', '@Foo'))),
			('"@Foo"', _q(_any('@Foo'))),
		):
			query = parse_search_query(string, keywords)
			#print('====', string, '\n', query, '\n', wanted)
			self.assertEqual(query, wanted)

	def testOperatorPrecedence(self):
		# Example from docs:
		#	Order of precedence: AND, OR, NOT
		#	so "foo AND NOT bar OR baz" means AND(foo, OR(NOT(bar), baz))
		#
		#	'foo OR bar AND dus'
		#	gives all pages that contain "dus" plus either "foo" or "bar" or both.
		#
		keywords = {'links'}
		for string, wanted in (
			('foo AND NOT bar OR baz',
				_and(_any('foo'), _or(_not(_any('bar')), _any('baz')))),
			('foo OR bar AND dus',
				_and(_or(_any('foo'), _any('bar')), _any('dus'))),
			('foo OR bar OR test AND dus', # multiple OR at the start
				_and(_or(_any('foo'), _any('bar'), _any('test')), _any('dus'))),
			('dus AND foo OR bar OR test AND dus', # multiple OR in the middle
				_and(_any('dus'), _or(_any('foo'), _any('bar'), _any('test')), _any('dus'))),
			('dus AND foo OR bar OR test', # multiple OR at the end
				_and(_any('dus'), _or(_any('foo'), _any('bar'), _any('test')))),
			('foo OR bar OR test', # only OR -> skip top-level AND group
				_or(_any('foo'), _any('bar'), _any('test'))),
		):
			query = parse_search_query(string, keywords)
			#print('====', string, '\n', query, '\n', wanted)
			self.assertEqual(query, wanted)

	def testNotOperator(self):
		keywords = {'links'}
		for string, wanted in (
			('NOT links: Foo', _q(_not(_t('links', 'Foo')))),
			('NOTlinks: Foo', _and(_any('NOTlinks:'), _any('Foo'))),
				# not an operator, fallback to string with default keyword
			('-links:Foo', _q(_not(_t('links', 'Foo')))),
			('- links:Foo', _q(_not(_t('links', 'Foo')))),
			('-Links:', _q(_not(_any('Links:')))),
			('-"Links:Foo"', _q(_not(_any('Links:Foo')))),
			('-links Foo', _and(_not(_any('links')), _any('Foo'))),
			('-links +Foo', _and(_not(_any('links')), _any('Foo'))),
			('NOT links Foo', _and(_not(_any('links')), _any('Foo'))),
			('links -Foo', _and(_any('links'), _not(_any('Foo')))),
			('+links -Foo', _and(_any('links'), _not(_any('Foo')))),
			('links NOT Foo', _and(_any('links'), _not(_any('Foo')))),
			('NOT links -Foo', _and(_not(_any('links')), _not(_any('Foo')))),
		):
			query = parse_search_query(string, keywords)
			#print('====', string, '\n', query, '\n', wanted)
			self.assertEqual(query, wanted)

	def testQuotedStrings(self):
		# Examples from docs
		keywords = {'linksto'}
		for string, wanted in (
			('"foo bar" and "+1"', _and(_any('foo bar'), _any('+1'))),
			('NOT LinksTo: ":Done"', _q(_not(_t('linksto', ':Done')))),
		):
			query = parse_search_query(string, keywords)
			#print('====', string, '\n', query, '\n', wanted)
			self.assertEqual(query, wanted)

	def testExplicitGrouping(self):
		keywords = {'links'}
		for string, wanted in (
			('foo OR (bar baz)', # group at end
				_or(_any('foo'), _and(_any('bar'), _any('baz')))),
			('(bar baz) OR foo', # group at start
				_or(_and(_any('bar'), _any('baz')), _any('foo'))),
			('foo OR (bar baz) OR some', # group in middle
				_or(_any('foo'), _and(_any('bar'), _any('baz')), _any('some'))),
			('(bar baz)', # only group
				_and(_any('bar'), _any('baz'))),
			('foo OR (bar (test OR TEST))', # Nested group
				_or(_any('foo'), _and(_any('bar'), _or(_any('test'), _any('TEST'))))),
			('foo OR ((test OR TEST) bar)', # Nested group
				_or(_any('foo'), _and(_or(_any('test'), _any('TEST')), _any('bar')))),
			('foo OR (bar (test OR TEST) baz)', # Nested group
				_or(_any('foo'), _and(_any('bar'), _or(_any('test'), _any('TEST')), _any('baz')))),
			('foo AND NOT bar OR baz', # Apply NOT to term
				_and(_any('foo'), _or(_not(_any('bar')), _any('baz')))),
			('foo AND NOT (bar OR baz)', # Apply NOT to group
				_and(_any('foo'), _not(_or(_any('bar'), _any('baz'))))),
			('foo AND NOT ( bar OR baz )', # with spaces
				_and(_any('foo'), _not(_or(_any('bar'), _any('baz'))))),
			('links: (bar and baz -dus)', # keyword group - no operator support except "+", "-"
				_and(_t('links', 'bar'), _t('links', 'and'), _t('links', 'baz'), _not(_t('links', 'dus')))
			)
		):
			query = parse_search_query(string, keywords)
			self.assertEqual(query, wanted)

	def testFixingInvalidQueries(self):
		keywords = {'links'}
		for string, equivalent in (
			# groups cannot start with OR or multiple AND
			('AND +foo bar', 'foo bar'),
			('OR foo bar', 'foo bar'),
			('foo (OR bar)', 'foo (bar)'),
			# groups cannot end with AND, OR or NOT
			('foo OR', 'foo'),
			('foo AND', 'foo'),
			('foo NOT', 'foo'),
			('foo (bar NOT) baz', 'foo (bar) baz'),
			# AND and OR cannot follow another AND, OR or NOT operator
			('foo AND OR bar', 'foo OR bar'),
			('foo OR AND bar', 'foo AND bar'),
			('foo NOT AND bar', 'foo AND bar'),
			# groups cannot be empty - including toplevel - and "( )"
			('', ''),
			('    ', ''),
			('( )', ''),
			('AND', ''),
			('NOT', ''),
			# unmatched ( or )
			('(foo', 'foo'),
			('bar)', 'bar'),
			('foo (bar OR baz))', 'foo (bar OR baz)'),
			('foo (bar OR (baz)', 'foo (bar OR (baz))'),
			# more weird edge cases
			('(foo +)', 'foo'),
		):
			#print('====', string)
			with tests.LoggingFilter('zim.parsing') as warning:
				query = parse_search_query(string, keywords)
				wanted = parse_search_query(equivalent, keywords)
				self.assertEqual(query, wanted)
				self.assertTrue(warning.captured)


class TestSearchQueryToFindQuery(tests.TestCase):

	def runTest(self):
		from zim.gui.pageview.find import FindQuery, FIND_CASE_SENSITIVE, FIND_WHOLE_WORD, FIND_REGEX

		for string, wanted in (
			('Foo', FindQuery('Foo')), # TODO: should use FIND_WHOLE_WORD
			('*Foo*', FindQuery('Foo')),
			('Foo Bar', FindQuery('Foo|Bar', FIND_REGEX)),
			('Foo -Bar', FindQuery('Foo')),
			('Links: Foo', None), # no content match in this query
			('Tag: Foo', FindQuery('@Foo')),
			('@Foo', FindQuery('@Foo')),
			('@Foo Bar', FindQuery(re.escape('@Foo') + '|Bar', FIND_REGEX)),
				# re.escape() behavior changed in 3.7 older versions also escape the "@"
			('Foo... Bar', FindQuery('Foo\\.\\.\\.|Bar', FIND_REGEX)),
			('NOT foo', None),
		):
			squery = parse_page_search_query(string)
			fquery = find_query_from_search_query(squery)
			#print('====', string, '\n', squery, '\n', fquery, '\n', wanted)
			self.assertEqual(fquery, wanted)


class TestSearchRegex(tests.TestCase):
	'''Test regex compilation for search terms'''

	def testContentMatches(self):
		regex_func = SearchSelection(None)._content_regex

		for word, regex in (
			('foo', r'\bfoo\b'),
			('*foo', r'\b\S*foo\b'),
			('foo*', r'\bfoo\S*\b'),
			('*foo*', r'\b\S*foo\S*\b'),
			('foo$', r'\bfoo\$'),
			('foo bar', r'\bfoo\ bar\b'),
		):
			self.assertEqual(regex_func(word).pattern, re.compile(regex, re.I | re.U).pattern)

		self.assertIn(regex_func('汉字').pattern, ('汉字', r'\汉\字'))
			# re.escape add extra "\" prior to python3.7, but not later
			# goal of this check is to see no "\b" surrounding chines characters


		text = 'foo foobar FooBar Foooo Foo!'
		regex = regex_func('foo')
		new, n = regex.subn('', text)
		self.assertEqual(n, 2)
		self.assertEqual(new, ' foobar FooBar Foooo !')

		text = 'foo foobar FooBar Foooo Foo!'
		regex = regex_func('foo*')
		new, n = regex.subn('', text)
		self.assertEqual(n, 5)

	def testNameMatches(self):
		regex_func = SearchSelection(None)._name_regex

		for word, regex in (
			('foo', r'(^|.*:)foo(:|$)'),
			('*foo', r'.*foo(:|$)'),
			('foo*', r'(^|.*:)foo'),
			('*foo*', r'.*foo'),
			('foo$', r'(^|.*:)foo\$(:|$)'),
			('foo bar', r'(^|.*:)foo\ bar(:|$)'),
			('foo:bar', r'(^|.*:)foo:bar(:|$)'),
			(':foo', r'(^|.*:)foo(:|$)'), # same as "foo"
			('foo:', r'(^|.*:)foo(:|$)'),
			(':foo:', r'(^|.*:)foo(:|$)'),
			('foo:*', r'(^|.*:)foo:'), # match child pages only
			(':foo:*', r'(^|.*:)foo:'),
		):
			self.assertEqual(regex_func(word).pattern, re.compile(regex, re.I | re.U).pattern)


		for word, path, match in (
			('foo', 'foo', True),
			('foo', 'foo:bar', True),
			('foo', 'bar:foo', True),
			('foo', 'dus:foo:ja', True),
			('foo', 'foobar', False),
			('foo*', 'foobar', True),
			('foo*', 'dus:foobar', True),
			('foo*', 'dus:foobar:baz', True),
			('foo*', 'dusfoo', False),
			('*foo', 'dusfoo', True),
			('*foo', 'bar:dusfoo', True),
			('*foo', 'dusfoo:baz', True),
			('*foo', 'bar:dusfoo:baz', True),
			('*foo', 'dusfoobar', False),
			('*foo*', 'dusfoobar', True),
			('foo:bar', 'foo', False),
			('foo:bar', 'foo:bar', True),
			('foo:bar', 'foo:bar:baz', True),
			('foo:bar', 'dus:foo:bar', True),
			(':foo', 'foo', True),
			('foo:', 'foo', True),
			('foo:*', 'foo', False),
			('foo:*', 'foo:bar', True),
		):
			#print('==', word, path, match, regex_func(word).pattern)
			self.assertEqual(bool(regex_func(word).match(Path(path).name)), match)

	def testSectionMatches(self):
		regex_func = SearchSelection(None)._namespace_regex

		for word, regex in (
			('foo', r'^foo(:|$)'),
			('*foo', r'^\*foo(:|$)'), # not supported
			('foo*', r'^foo'),
			('*foo*', r'^\*foo'),
			('foo$', r'^foo\$(:|$)'),
			('foo bar', r'^foo\ bar(:|$)'),
			(':foo', r'^foo(:|$)'), # same as "foo"
			('foo:', r'^foo(:|$)'), # same as "foo"
			('foo:*', r'^foo:'), # only match child pages
			('foo:bar', r'^foo:bar(:|$)'),
		):
			self.assertEqual(regex_func(word).pattern, re.compile(regex, re.I | re.U).pattern)

		for word, path, match in (
			('foo', 'foo', True),
			('foo', 'foo:bar', True),
			('foo', 'bar:foo', False),
			('foo:', 'foo', True),
			('foo:', 'foo:bar', True),
			('foo:', 'bar:foo', False),
			('foo:*', 'foo', False),
			('foo:*', 'foo:bar', True),
			('foo:*', 'bar:foo', False),
		):
			#print('==', word, path, match, regex_func(word).pattern)
			self.assertEqual(bool(regex_func(word).match(Path(path).name)), match)


class TestPageSearch(tests.TestCase):

	@classmethod
	def setUpClass(cls):
		# Using a class setup speeds up considerably when testing with real files
		cls.notebook = cls.setUpClassNotebook(content=tests.FULL_NOTEBOOK)

	def callback_check(self, selection, path):
		self.assertIsInstance(selection, (SearchSelection, type(None)))
		self.assertIsInstance(path, (Path, type(None)))
		return True

	def testDefaultKeyword(self):
		results = SearchSelection(self.notebook)

		query = parse_page_search_query('foo bar')
		self.assertEqual(query, _and(
				_t('contentorname', 'foo'),
				_t('contentorname', 'bar')
			))
		results.search(query, callback=self.callback_check)
		self.assertTrue(len(results) > 0)
		self.assertFalse(Path('TaskList:foo') in results)
		self.assertTrue(Path('Test:foo') in results)
		self.assertTrue(Path('Test:foo:bar') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('+TODO -bar')
		self.assertEqual(query, _and(
				_t('contentorname', 'TODO'),
				_not(_t('contentorname', 'bar'))
			))
		results.search(query, callback=self.callback_check)
		self.assertTrue(len(results) > 0)
		self.assertTrue(Path('TaskList:foo') in results)
		self.assertFalse(Path('Test:foo') in results)
		self.assertFalse(Path('Test:foo:bar') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('TODO not bar')
		self.assertEqual(query, _and(
				_t('contentorname', 'TODO'),
				_not(_t('contentorname', 'bar'))
			))
		results.search(query, callback=self.callback_check)
		self.assertTrue(len(results) > 0)
		self.assertTrue(Path('TaskList:foo') in results)
		self.assertFalse(Path('Test:foo') in results)
		self.assertFalse(Path('Test:foo:bar') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('TODO or bar')
		self.assertEqual(query, _or(
				_t('contentorname', 'TODO'),
				_t('contentorname', 'bar')
			))
		results.search(query, callback=self.callback_check)
		self.assertTrue(len(results) > 0)
		self.assertTrue(Path('TaskList:foo') in results)
		self.assertTrue(Path('Test:foo') in results)
		self.assertTrue(Path('Test:foo:bar') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('ThisWordDoesNotExistingInTheTestNotebook')
		results.search(query, callback=self.callback_check)
		self.assertFalse(results)

	def testContentKeyword(self):
		results = SearchSelection(self.notebook)

		query = parse_page_search_query('Content: foo')
		self.assertEqual(query, _q(_t('content', 'foo')))
		results.search(query, callback=self.callback_check)
		self.assertTrue(len(results) > 0)

	def testNameKeyword(self):
		results = SearchSelection(self.notebook)

		query = parse_page_search_query('Name: foo')
		self.assertEqual(query, _q(_t('name', 'foo')))
		results.search(query, callback=self.callback_check)
		self.assertTrue(len(results) > 0)

	def testSectionKeyword(self):
		results = SearchSelection(self.notebook)

		query = parse_page_search_query('Namespace: "TaskList" fix')
		self.assertEqual(query, _and(_t('namespace', 'TaskList'), _t('contentorname', 'fix')))
		results.search(query, callback=self.callback_check)
		self.assertTrue(Path('TaskList:foo') in results)

		for text in (
			'Namespace: "Test:Foo Bar"',
			'Namespace:"Test:Foo Bar"'
			'Section: "Test:Foo Bar"'
			'Section:"Test:Foo Bar"'
		):
			# check if space in page name works - found bug for 2nd form
			query = parse_page_search_query(text)
			results.search(query, callback=self.callback_check)
			self.assertTrue(Path('Test:Foo Bar:Dus Ja Hmm') in results)

		query = parse_page_search_query('Namespace: "NonExistingNamespace"')
		results.search(query, callback=self.callback_check)
		self.assertFalse(results)

	def testTagKeyword(self):
		results = SearchSelection(self.notebook)

		query = parse_page_search_query('Tag: tags')
		self.assertEqual(query, _q(_t('tag', 'tags')))
		query = parse_page_search_query('@tags') # implicit keyword
		self.assertEqual(query, _q(_t('tag', '@tags')))
		results.search(query, callback=self.callback_check)
		#~ print results
		self.assertTrue(Path('Test:tags') in results and len(results) == 2)
			# Tasklist:all is the second match

		query = parse_page_search_query('Tag: NonExistingTag')
		results.search(query, callback=self.callback_check)
		self.assertFalse(results)

	def testLinksToKeyword(self):
		results = SearchSelection(self.notebook)

		query = parse_page_search_query('LinksTo: "Linking:Foo:Bar"')
		self.assertEqual(query, _and(_t('linksto', 'Linking:Foo:Bar')))
		results.search(query, callback=self.callback_check)
		self.assertTrue(Path('Linking:Dus:Ja') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('NOT LinksTo:"Linking:Foo:Bar"')
		self.assertEqual(query, _q(_not(_t('linksto', 'Linking:Foo:Bar'))))
		results.search(query, callback=self.callback_check)
		self.assertFalse(Path('Linking:Dus:Ja') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('LinksTo:"NonExistingNamespace:*"')
		results.search(query, callback=self.callback_check)
		self.assertFalse(results)

	def testLinksFromKeyword(self):
		results = SearchSelection(self.notebook)

		query = parse_page_search_query('LinksFrom: "Linking:Dus:Ja"')
		self.assertEqual(query, _q(_t('linksfrom', 'Linking:Dus:Ja')))
		results.search(query, callback=self.callback_check)
		self.assertTrue(Path('Linking:Foo:Bar') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('Links: "Linking:Dus:Ja"') # alias for LinksFrom
		self.assertEqual(query, _q(_t('links', 'Linking:Dus:Ja')))
		results.search(query, callback=self.callback_check)
		#~ print results
		self.assertTrue(Path('Linking:Foo:Bar') in results)
		self.assertTrue(set(results.scores.keys()) == results)
		self.assertTrue(all(results.scores.values()))

		query = parse_page_search_query('LinksFrom:"NonExistingNamespace:*"')
		results.search(query, callback=self.callback_check)
		self.assertFalse(results)


@tests.slowTest
class TestPageSearchFiles(TestPageSearch):

	@classmethod
	def setUpClass(cls):
		# Using a class setup speeds up considerably when testing with real files
		cls.notebook = cls.setUpClassNotebook(mock=tests.MOCK_ALWAYS_REAL, content=tests.FULL_NOTEBOOK)


@tests.skipIf(
	indexed_fts.IndexedFTSPlugin.check_dependencies()[0] == False,
	"Indexed FTS plugin not available"
)
class TestPageSearchIndexed(TestPageSearch):
	'''Test case for integration with the indexed_fts plugin'''

	@classmethod
	def setUpClass(cls):
		tests.TestCase.setUpClass() # setup plugin manager
		plugin = PluginManager.load_plugin('indexed_fts')
		TestPageSearch.setUpClass()


class TestUnicodeSearchTerms(tests.TestCase):

	def runTest(self):
		notebook = self.setUpNotebook(content={'Öffnungszeiten': 'Öffnungszeiten ... 123\n'})
		results = SearchSelection(notebook)
		path = Path('Öffnungszeiten')

		for string in (
			'*zeiten', # no unicode - just check test case
			'Öffnungszeiten',
			'öffnungszeiten', # case insensitive version
			'content:Öffnungszeiten',
			'content:öffnungszeiten',
			'name:Öffnungszeiten',
			'name:öffnungszeiten',
			'content:Öff*',
			'content:öff*',
			'name:Öff*',
			'name:öff*',
		):
			query = parse_page_search_query(string)
			results.search(query)
			self.assertIn(path, results, 'query did not match: "%s"' % string)


class TestQueryGrouping(tests.TestCase):

	PAGES = {
		'page1': 'term1',
		'page2': 'term2',
		'page3': 'term3',
		'page4': 'term4',
		'page12': 'term1 term2',
		'page13': 'term1 term3',
	}

	def runTest(self):
		notebook = self.setUpNotebook(content=self.PAGES)
		results = SearchSelection(notebook)

		for string, pages in (
			('term1', ('page1', 'page12', 'page13')), # simpel case to test test construct
			('term1 term2 OR term3', ('page12', 'page13')),
			('term1 (term2 OR term3)', ('page12', 'page13')),
			('(term1 term2) OR term3', ('page12', 'page3', 'page13')),
			('term1 NOT (term2 OR term3)', ('page1',)),
		):
			query = parse_page_search_query(string)
			results.search(query)
			#print('==', string, set(results), set(pages))
			self.assertEqual(set(results), set(Path(p) for p in pages))


class TestSearchQueryTermToRegex(tests.TestCase):

	def runTest(self):
		for value, regex in (
			('foo', '\\bfoo'),
			(' foo', '\\bfoo'),
			('*foo', 'foo'),
			('foo bar', '\\bfoo\\s+bar'),
			('foo*bar', '\\bfoo\\S*bar'),
			('foo*', '\\bfoo'),
			('foo ', '\\bfoo\\b'),
			(' foo ', '\\bfoo\\b'),
			('*foo*', 'foo'),
			(' foo bar ', '\\bfoo\\s+bar\\b'),
			('\u4e00foo', '\u4e00foo'), # chineses char changes behavior
			('*\u4e00foo', '\u4e00foo'),
			(' \u4e00foo', '\\b\u4e00foo'),
			('+foo', '\\+foo'), # no word boundery at non-word character
		):
			#print(value, regex, search_query_term_to_regex(value))
			self.assertEqual(search_query_term_to_regex(value), re.compile(regex, re.I))


class TestSearchQueryPageNameTermToRegex(tests.TestCase):

	def runTest(self):
		for value, regex in (
			('foo', 'foo'),
			(' foo', '\\s+foo'),
			('*foo', 'foo'),
			('foo bar', 'foo\\s+bar'),
			('foo*', 'foo'),
			('foo ', 'foo\\s+'),
			(' foo ', '\\s+foo\\s+'),
			('*foo*', 'foo'),
			(' foo bar ', '\\s+foo\\s+bar\\s+'),
			(':foo:', '(^:?|:)foo(:|:?$)'),
			('::foo::', '^:?foo:?$'),
		):
			#print(value, regex, search_query_pagename_term_to_regex(value))
			self.assertEqual(search_query_pagename_term_to_regex(value), re.compile(regex, re.I))


class TestCompileSearchQueryCheckFunction(tests.TestCase):

	tuple_keywords = {
		'name': {'check_func_constructor': check_func_constructor_any_keyword, 'include': ('firstname', 'lastname')},
		'firstname': {'key': 0},
		'lastname': {'key': 1}
	}
	dict_keywords = {
		'name': {'check_func_constructor': check_func_constructor_any_keyword, 'include': ('firstname', 'lastname')},
		'firstname': {'key': 'f_name'},
		'lastname': {'key': 'lastname'}
	}
	tuple_records = [
		('John', 'Doe'),
		('Johnny', 'Doe'),
		('John', 'Johnson'),
		('Janna', 'Doe'),
	]
	dict_records = [
		{'f_name': 'John', 'lastname': 'Doe'},
		{'f_name': 'Johnny', 'lastname': 'Doe'},
		{'f_name': 'John', 'lastname': 'Johnson'},
		{'f_name': 'Janna', 'lastname': 'Doe'},
	]
	queries = [
		('John', [True, True, True, False]),
		('name:John', [True, True, True, False]),
		('name: John', [True, True, True, False]),
		('name: "John"', [True, True, True, False]),
		('name: "John "', [True, False, True, False]),
		('firstname:John', [True, True, True, False]),
		('lastname:John', [False, False, True, False]),
		('firstname:John and lastname:Doe', [True, True, False, False]),
		('firstname:John or lastname:Doe', [True, True, True, True]),
		('not John', [False, False, False, True]),
	]

	def testWithTupleRecords(self):
		self._runTest(self.tuple_keywords, self.tuple_records)

	def testWithDictRecords(self):
		self._runTest(self.dict_keywords, self.dict_records)

	def _runTest(self, keywords, records):
		for query, wanted in self.queries:
			p_query = parse_search_query(query, keywords, default_keyword='name')
			#print('>>>', query, '\n', '===', p_query)
			check_func = compile_search_query_check_function(p_query, keywords)
			result = list(map(check_func, records))
			self.assertEqual(result, wanted, msg='Query: %s' % query)
