
# Copyright 2009-2025 Jaap Karssenberg <jaap.karssenberg@gmail.com>

'''
This module contains the logic for searching in a notebook.

See L{zim.parse.searchquery} for generic parsing of the query language

Supported keywords:

  - C{Content}
  - C{Name}
  - C{Section}: alias for "Name XXX or Name: XXX:*"
  - C{Namespace}: alias for "Name XXX or Name: XXX:*" -- backward compatible
  - C{Links}: forward - alias for linksfrom
  - C{LinksFrom}: forward
  - C{LinksTo}: backward
  - C{ContentOrName}: the default, like Name: *X* or Content: X
  - C{Tag}: look for a single tag

For the Content field we need to request the actual page contents,
all other fields we get from the index and are more efficient to
query.

For link keywords only a '*' at the right side is allowed
For the name keyword a '*' is allowed on both sides
For content '*' can occur on both sides, but does not match whitespace
'''


import re
import logging

from typing import Optional

from zim.notebook import Path, \
	PageNotFoundError, IndexNotFoundError, \
	LINK_DIR_BACKWARD, LINK_DIR_FORWARD

from zim.plugins import PluginManager

from zim.parse.searchquery import *


logger = logging.getLogger('zim.search')



KEYWORDS = {
	'content': {},
	'name': {},
	'namespace': {},
	'section': {},
	'contentorname': {},
	'links': {},
	'linksfrom': {},
	'linksto': {},
	'tag': {'regex': search_tag_re}
}
DEFAULT_KEYWORD = 'contentorname'


def parse_page_search_query(string: str) -> SearchQuery:
	return parse_search_query(string, KEYWORDS, default_keyword=DEFAULT_KEYWORD)


def find_query_from_search_query(query: SearchQuery) -> Optional['FindQuery']:
	from zim.gui.pageview.find import FindQuery, FIND_CASE_SENSITIVE, FIND_WHOLE_WORD, FIND_REGEX

	strings = list(_walk_search_query(query))
	if len(strings) == 1:
		return FindQuery(strings[0])
	elif strings:
		return FindQuery('|'.join(re.escape(s) for s in strings if s), FIND_REGEX)
	else:
		return None

def _walk_search_query(query):
	for term in query:
		if isinstance(term, SearchQuery):
			if term.negate:
				continue # skip negated content
			else:
				for s in self._walk_text_content(term): # recurs
					yield s
		else: # SearchQueryTerm
			if term.negate: # OPERATOR_NOT
				continue
			elif term.keyword in ('content', 'contentorname'):
				yield term.value.strip('*') # strip "*" for partial matches
			elif term.keyword == 'tag':
				yield '@' + term.value.lstrip('@').strip('*')
			else:
				pass # other terms select pages, but no (easy) match in the page


class PageSelection(set):
	'''This class is just a container of path objects'''

	pass


class SearchSelection(PageSelection):
	'''This class wraps a set of Page or ResultPath objects which result
	from processing a search query. The attribute 'scores' gives a dict
	with an arbitrary integer for each path in this set to rank how well
	they match the query.
	'''

	def __init__(self, notebook):
		self.notebook = notebook
		self.cancelled = False
		self.query = None
		self.scores = {}

	def search(self, query, selection=None, callback=None):
		'''Populate this SearchSelection with results for a query.
		This method flushes any previous results in this set.

		@param query: a L{Query} object
		@param selection: a prior selection to search within, will result in a sub-set
		@param callback: a function to call in between steps in the search.
		It is called as::

			callback(selection, path)

		Where:
		  - C{selection} is a L{SearchSelection} with partial results (if any)
		  - C{path} is the C{Path} for the last searched path or C{None}

		If the callback returns C{False} the search is cancelled.
		'''
		# Clear state
		self.cancelled = False
		self.query = query
		self.clear()
		self.scores = {}

		# Actual search
		self.update(self._process_group(query, selection, callback))

		# Clean up results
		scored = set(self.scores.keys())
		for path in scored - self:
			self.scores.pop(path)

	def _process_group(self, group, scope=None, callback=None):
		# This method processes all search terms in a SearchQuery
		# it is recursive for nested SearchQuery objects and calls
		# _process_from_index and _process_content to handle
		# SearchQueryTerms in the group. It takes care of combining the
		# results from various terms and calling the callback
		# function when possible

		# Special case to optimize for simple OR query to give callback results
		if len(group) == 1 and isinstance(group[0], SearchQuery):
			group = group[0]

		# For optimization we sort the terms in the group based  on how
		# easy we can get them. Anything that needs content is last.
		indexterms = []
		subgroups = []
		contentterms = []
		for term in group:
			if isinstance(term, SearchQuery):
				subgroups.append(term)
			else:
				assert isinstance(term, SearchQueryTerm)
				if term.keyword in ('content', 'contentorname'):
					contentterms.append(term)
				else:
					indexterms.append(term)

		# Decide what operator to use
		if group.operator == OPERATOR_AND:
			op_func = self._and_operator
		else:
			op_func = self._or_operator

		# First process index terms - no callback in between - this is fast
		results = None
		for term in indexterms:
			results, scope = op_func(results, scope,
				self._process_from_index(term, scope))

		if callback:
			if group.operator == OPERATOR_AND:
				cont = callback(None, None) # do not transmit results yet
			else:
				cont = callback(results, None)

			if not cont:
				self.cancelled = True
				return results or set()

		# Next we process subgroups - recursing - callback after each group
		def callbackwrapper(results, path):
			# Don't update results from subgroup match, but do allow cancel
			if callback:
				return callback(None, path)
			else:
				return True

		for term in subgroups:
			newresults = self._process_group(term, scope, callbackwrapper)
			if term.negate:
				newresults = self._negate_op(scope, newresults)
			results, scope = op_func(results, scope, newresults)

			if callback:
				if group.operator == OPERATOR_AND:
					cont = callback(None, None) # do not transmit results yet
				else:
					cont = callback(results, None)

				if not cont:
					self.cancelled = True
					return results or set()

		# Optimization of the contentorname items to quickly show results for name
		for term in contentterms:
			if scope and id(scope) == id(results):
				scope = scope.copy()
			myscope = scope # local copy here, need to pass full scope to _process_content
			if term.keyword == 'contentorname':
				results, myscope = op_func(results, myscope,
					self._process_from_index(term, myscope, scoring=10))

		if callback and (
			group.operator == OPERATOR_OR or
			all(term.keyword == 'contentorname' for term in contentterms)
		):
			cont = callback(results, None)
			if not cont:
				self.cancelled = True
				return results or set()

		# If enabled, use the indexed_fts plugin for fast content search
		if "indexed_fts" in PluginManager:
			logger.debug("Searching using Indexed FTS plugin")
			process_index_fts = PluginManager["indexed_fts"].process_index_fts

			# For AND sets, scope will contain the results so far, and
			# results only contains stuff from the contentorname query
			# (which we don't need here)
			# For OR sets, results is whatever was found so far, and should
			# be extended with matches inside scope.
			for term in contentterms:
				if group.operator == OPERATOR_AND:
					results, scope = self._and_operator(scope, scope,
						process_index_fts(self, term, scope))
				else:
					results, scope = self._or_operator(results, scope,
						process_index_fts(self, term, scope))

		# Now do the content terms all at once per page - slow or very slow
		elif contentterms:
			results = self._process_content(
				contentterms, results, scope, group.operator, callback)

		# And return our results as summed by the operator
		return results or set()


	@staticmethod
	def _and_operator(results, scope, newresults):
		# Returns new results and new scope
		# For AND, the scope is always latest results
		if results is None:
			results = newresults
		else:
			results &= newresults
		return results, results

	@staticmethod
	def _or_operator(results, scope, newresults):
		# Returns new results and new scope
		# For OR we always keep the original scope
		if results is None:
			results = newresults
		else:
			results |= newresults
		return results, scope

	def _negate_op(self, scope, newresults):
		if not scope:
			# initialize scope with whole notebook :S
			scope = set()
			for p in self.notebook.pages.walk():
				scope.add(p)
		return scope - newresults

	def _count_score(self, path, score):
		self.scores[path] = self.scores.get(path, 0) + score

	def _process_from_index(self, term, scope, scoring=1):
		# Process keywords we can get from the index, just one term at
		# a time - leave it up to _process_group to combine them
		myresults = SearchSelection(None)
		myresults.scores = self.scores # HACK for callback function
		scoped = False

		if term.keyword in ('name', 'namespace', 'section', 'contentorname'):
			scoped = True # for these keywords we use scope immediatly
			if scope:
				generator = iter(scope)
			else:
				generator = self.notebook.pages.walk()

			if term.keyword in ('namespace', 'section'):
				regex = self._namespace_regex(term.value)
			elif term.keyword == 'contentorname':
				# More lax matching for default case
				regex = self._name_regex('*' + term.value.strip('*') + '*')
				term.name_regex = regex # needed in _process_content
			else:
				regex = self._name_regex(term.value)

			#~ print('!! REGEX: ' + regex.pattern)
			for path in generator:
				if regex.match(path.name):
					myresults.add(path)

		elif term.keyword in ('links', 'linksfrom', 'linksto'):
			if term.keyword in ('links', 'linksfrom'):
				dir = LINK_DIR_FORWARD
			else:
				dir = LINK_DIR_BACKWARD

			if term.value.endswith('*'):
				recurs = True
				string = term.value.rstrip('*')
			else:
				recurs = False
				string = term.value

			try:
				path = self.notebook.pages.lookup_from_user_input(string)
			except ValueError:
				pass
			else:

				try:
					if recurs:
						links = self.notebook.links.list_links_section(path, dir)
					else:
						links = self.notebook.links.list_links(path, dir)
				except IndexNotFoundError:
					pass
				else:

					if dir == LINK_DIR_FORWARD:
						for link in links:
							myresults.add(link.target)
					else:
						for link in links:
							myresults.add(link.source)

		elif term.keyword == 'tag':
			tag = term.value
			try:
				for path in self.notebook.tags.list_pages(tag):
					myresults.add(path)
			except IndexNotFoundError:
				pass
		else:
			assert False, 'BUG: unknown keyword: %s' % term.keyword

		# apply scope:
		if scope and not scoped:
			myresults &= scope # only keep results that in scope

		# negate selection
		if term.negate:
			negate = self._negate_op(scope, myresults)
			myresults.clear()
			myresults.update(negate)

		for path in myresults:
			self._count_score(path, scoring)

		return myresults

	def _process_content(self, terms, results, scope, operator, callback=None):
		# Process terms for content, process many at once in order to
		# only open the page once and allow for a linear behavior of the
		# callback function. (We could also have relied on page objects
		# caching the parsetree, but then there is no way to support a
		# useful callback method.)
		# Note that this rationale is for flat searches, once sub-groups
		# are involved things get less optimized.
		#
		# For AND 'scope' will be the results of previous steps, we make a subset
		# of this. In 'results' will only be any final results already obtained from
		# contentorname optimization
		# For OR 'results' is whatever was found so far while 'scope' can be larger
		# we extend the results with any matches from scope
		for term in terms:
			term.content_regex = self._content_regex(term.value)
			# term.name_regex already defined in _process_from_index

		def page_generator(paths):
			for path in paths:
				try:
					yield self.notebook.get_page(path)
				except:
					logger.exception('Exception opening: %s', path)
					continue

		if scope:
			generator = page_generator(scope)
		else:
			generator = page_generator(self.notebook.pages.walk())

		if results is None:
			results = SearchSelection(None)

		for page in generator:
			#~ print('!! Search content', page)
			try:
				tree = page.get_parsetree()
			except:
				logger.exception('Exception reading: %s', page)
				continue

			if tree is None:
				continue # Assume need to have content even for negative query

			path = Path(page.name)
			if operator == OPERATOR_AND:
				score = 0
				for term in terms:
					#~ print('!! Count AND %s' % term)
					myscore = tree.countre(term.content_regex)
					if term.keyword == 'contentorname' \
					and term.name_regex.match(path.name):
						myscore += 1 # effective score going to 11

					if bool(myscore) != term.negate: # implicit XOR
						score += myscore or 1
					else:
						score = 0
						break

				if score:
					results.add(path)
					self._count_score(path, score)
			else: # OPERATOR_OR
				for term in terms:
					#~ print('!! Count OR %s' % term)
					score = tree.countre(term.content_regex)
					if term.keyword == 'contentorname' \
					and term.name_regex.match(path.name):
						score += 1 # effective score going to 11

					if bool(score) != term.negate: # implicit XOR
						results.add(path)
						self._count_score(path, score or 1)

			if callback:
				# Since we are always last in the processing of the
				# (top-level) group, we can call the callback with all results
				cont = callback(results, path)
				if not cont:
					self.cancelled = True
					break

		return results

	def _name_regex(self, string, case=False):
		# Build a regex for matching a glob against a page name
		# consider the ":" separator as the word boundary, even if name contains spaces
		if string.startswith('*'):
			prefix = r'.*'
			string = string.lstrip('*')
		else:
			prefix = r'(^|.*:)'
			string = string.lstrip(':')

		if string.endswith('*'):
			# ":*" ending implicit here
			postfix = r''
			string = string.rstrip('*')
		else:
			postfix = r'(:|$)'
			string = string.rstrip(':')

		regex = prefix + re.escape(string) + postfix
		if case:
			return re.compile(regex, re.U)
		else:
			return re.compile(regex, re.U | re.I)

	def _namespace_regex(self, string, case=False):
		# like _name_regex but adds recursive descent below the page
		string = string.lstrip(':')
		if string.endswith('*'):
			# ":*" ending implicit here
			postfix = r''
			string = string.rstrip('*')
		else:
			postfix = r'(:|$)'
			string = string.rstrip(':')

		regex = '^' + re.escape(string) + postfix
		if case:
			return re.compile(regex)
		else:
			return re.compile(regex, re.I)

	def _content_regex(self, string, case=False):
		# Build a regex for a content search term, expands wildcards
		# and sets case sensitivity. Tries to guess if we look for
		# whole word or not.

		# Build regex - first expand wildcards
		parts = string.split('*')
		regex = r'\S*'.join(map(re.escape, parts))

		# Next add word delimiters
		# Avoid adding them next to non-word characters or next to chinese
		# charaters. Chinese is treated special because it does not use
		# whitespace as word delimiter.
		if re.search(r'^[*\w]', string, re.U) \
		and not '\u4e00' <= string[0] <= '\u9fff':
			regex = r'\b' + regex

		if re.search(r'[*\w]$', string, re.U) \
		and not '\u4e00' <= string[-1] <= '\u9fff':
			regex = regex + r'\b'

		#~ print('SEARCH REGEX: >>%s<<' % regex)
		if case:
			return re.compile(regex, re.U)
		else:
			return re.compile(regex, re.U | re.I)
