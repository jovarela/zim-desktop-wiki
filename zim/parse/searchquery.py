# Copyright 2009-2025 Jaap Karssenberg <jaap.karssenberg@gmail.com>

'''This module contains logic to parse search queries

Search queries consist of keywords like `content: foo` to search content for `foo`
or `name: foo` to search names matching `foo`.

Search terms without a keyword are mapped to a default keyword.

Search terms can be combined using operators.

Supported operators:
	- "NOT", "not" and "-"
	- "AND", "and", and "+"
	- "OR" or "or"

Order of precedence: AND, OR, NOT
so "foo AND NOT bar OR baz" means AND(foo, OR(NOT(bar), baz))

Explicit groups can be made by "(" and ")"

This module does not contain logic for evaluating the queries, that is up
to the module implementing the search behavior.
'''

import re

from zim.errors import Error

from .encode import unescape_string


OPERATOR_OR = 'OR'
OPERATOR_AND = 'AND'
OPERATOR_NOT = 'NOT'
OPERATOR_GROUP_START = '('
OPERATOR_GROUP_END = ')'

_operator_tokens = (OPERATOR_OR, OPERATOR_AND, OPERATOR_NOT, OPERATOR_GROUP_START, OPERATOR_GROUP_END)

operators = {
	'or': OPERATOR_OR,
	'and': OPERATOR_AND,
	'+': OPERATOR_AND,
	'-': OPERATOR_NOT,
	'not': OPERATOR_NOT,
	'(': OPERATOR_GROUP_START,
	')': OPERATOR_GROUP_END,
}

search_tag_re = re.compile(r'^\@(\w+)$', re.U)

_word_re = re.compile(r'''
	(	'(\\'|[^'])*' |  # single quoted word
		"(\\"|[^"])*" |  # double quoted word
		[^\s'"]+         # word without spaces or quotes
	)''', re.X)


def split_quoted_strings(string: str) -> list[str]:
	'''Split a word list respecting quotes, does not remove the quotes

	Allow both double and single quotes

	This function always expect full words to be quoted, even if quotes
	appear in the middle of a word, they are considered word
	boundries.
	'''
	string = string.strip()
	words = []
	m = _word_re.match(string)
	while m:
		words.append(m.group(0))
		i = m.end()
		string = string[i:].lstrip()
		m = _word_re.match(string)

	if string:
		words += string.split() # unmatched quote ?

	return [w for w in words if w]


def unescape_quoted_string(string: str) -> str:
	'''Removes quotes from a string and unescapes embedded quotes'''
	if not string:
		return string
	elif string[0] in ('"', "'") and string[-1] == string[0]:
		string = string[1:-1]
	return unescape_string(string)


def _indent(string):
	return ''.join("\t"+l for l in string.splitlines(True))


class SearchQuery:
	'''Object to represent a search query'''

	def __init__(self, operator, terms=None):
		self.operator = operator
		self.negate = False
		self.terms = list(terms) if terms else []
		assert all(isinstance(t, (SearchQuery, SearchQueryTerm)) for t in self.terms)

	def __eq__(self, other):
		return isinstance(other, self.__class__) and \
			(self.operator, self.negate, self.terms) == (other.operator, other.negate, other.terms)

	def __repr__(self):
		return "<%s op=%r negate=%r [\n%s\n]>" % (
			self.__class__.__name__, self.operator, self.negate,
			'\n'.join(_indent(repr(t)) for t in self.terms)
		)

	def __len__(self):
		return len(self.terms)

	def __iter__(self):
		return iter(self.terms)

	def __getitem__(self, i):
		return self.terms[i]

	def add(self, term):
		self.terms.append(term)

	def remove(self, term):
		i = self.terms.index(term)
		self.terms.pop(i)


class SearchQueryTerm:
	'''Object to represent a single keyword term in a search query'''

	def __init__(self, keyword, value):
		self.keyword = keyword
		self.negate = False
		self.value = value

	def __eq__(self, other):
		# Compare resulting term, not original string information
		return isinstance(other, self.__class__) and \
			(self.keyword, self.negate, self.value) == (other.keyword, other.negate, other.value)

	def __repr__(self):
		return "<%s %r %r negate=%r>" % (self.__class__.__name__, self.keyword, self.value, self.negate)


class SearchQueryValidationError(Error):
	'''Error raised when search query parsing encounters invalid syntax'''
	pass


def parse_search_query(string: str, keywords: dict, default_keyword: str='any') -> SearchQuery:
	'''Parse a search query string into a L{SearchQuery} object
	@param string: the string to be parsed
	@param keywords: dict with supported keywords
	@param default_keyword: keyword for strings without keyword specified
	'''
	tokens = _tokenize_search_query(string, keywords, default_keyword)
	tokens = _collect_explicit_groups(tokens)
	query = _process_operators(tokens)
	return query


def _tokenize_search_query(string: str, keywords: dict, default_keyword: str='any') -> list:
	# Split string in words, each word is either a search term or an operator
	# terms without a keyword get the default keyword

	# Bootstrap regexes
	keyword_re = re.compile('(' + '|'.join(keywords) + '):(.*)', re.I|re.U)
	implicit_keywords = {}
	if isinstance(keywords, dict): # should always be a dict, but in testing we use sets
		for k in keywords:
			if 'regex' in keywords[k]:
				r = keywords[k]['regex']
				implicit_keywords[k] = re.compile(r, re.U) if isinstance(r, str) else r

	def match_implicit_keyword(string):
		for k, r in implicit_keywords.items():
			if r.match(string):
				return k
		else:
			return default_keyword


	# First do a raw tokenizer
	words = split_quoted_strings(string)
	tokens = []
	while words:
		w = words.pop(0)

		if w[0] in ('(', ')', '+', '-') and len(w) > 1:
			words.insert(0, w[1:])
			w = w[0]

		while w[-1] in ('(', ')') and len(w) > 1:
			words.insert(0, w[-1])
			w = w[:-1]

		m_key = keyword_re.match(w)
		if w.lower() in operators:
			tokens.append(operators[w.lower()])
		elif m_key:
			keyword = m_key.group(1).lower()
			if not (m_key.group(2) or words):
				# edge case - something ending in ":" but nothing following
				term = m_key.group(1)+":"
				keyword = match_implicit_keyword(term)
				tokens.append(SearchQueryTerm(keyword, term))
			else:
				string = m_key.group(2) or words.pop(0)
				term = unescape_quoted_string(string)
				tokens.append(SearchQueryTerm(keyword, term))
		else:
			keyword = match_implicit_keyword(w)
			term = unescape_quoted_string(w)
			tokens.append(SearchQueryTerm(keyword, term))
	return tokens


def _collect_explicit_groups(tokens: list) -> list:
	# Group matched "(" and ")" and raise on unmatched occurences
	stack = [[]]
	for i, t in enumerate(tokens):
		if t == OPERATOR_GROUP_START:
			subgroup = []
			stack[-1].append(subgroup)
			stack.append(subgroup)
		elif t == OPERATOR_GROUP_END:
			if len(stack) > 1:
				stack.pop()
			else:
				raise SearchQueryValidationError(_("Unmatched %s in search query") % '")"') # T: error for search query parsing, %s will be '(' or ')'
		else:
			stack[-1].append(t)

	if len(stack) > 1:
		raise SearchQueryValidationError(_("Unmatched %s in search query") % '"("') # T: error for search query parsing, %s will be '(' or ')'

	return stack[0]


def _process_operators(tokens: list) -> SearchQuery:
	# Validate out of place operators at start and end
	if not tokens or all(t in _operator_tokens for t in tokens):
		raise SearchQueryValidationError(_('Empty search query')) # T: error for search query parsing

	if tokens[0] == OPERATOR_AND:
		# Allow for one stray AND operator, to get over "+foo +bar"
		tokens.pop(0)

	if tokens[0] in (OPERATOR_AND, OPERATOR_OR):
		op = '"%s"' % tokens[0]
		raise SearchQueryValidationError(_("Out of place %s operator in search query") % op)
			# T: error for search query parsing, %s will be 'NOT', 'AND', or 'OR'
	elif tokens[-1] in (OPERATOR_AND, OPERATOR_OR, OPERATOR_NOT):
		op = '"%s"' % tokens[-1]
		raise SearchQueryValidationError(_("Out of place %s operator in search query") % op)
			# T: error for search query parsing, %s will be 'NOT', 'AND', or 'OR'

	# Turn sub groups into queries - depth first
	for i in range(0, len(tokens)):
		if isinstance(tokens[i], list):
			tokens[i] = _process_operators(tokens[i]) # recurs

	# Process NOT operators
	for i in range(0, len(tokens)-1):
		if tokens[i] == OPERATOR_NOT:
			if isinstance(tokens[i+1], (SearchQuery, SearchQueryTerm)):
				tokens[i+1].negate = True
			else:
				raise SearchQueryValidationError(_("Out of place %s operator in search query") % '"NOT"')
					# T: error for search query parsing, %s will be 'NOT', 'AND', or 'OR'
	tokens = [t for t in tokens if t != OPERATOR_NOT]

	# Validate no out of place operators inside list
	for i in range(0, len(tokens)-1):
		if tokens[i] in (OPERATOR_OR, OPERATOR_AND):
			if tokens[i+1] in (OPERATOR_OR, OPERATOR_AND):
				op = '"%s"' % tokens[-1]
				raise SearchQueryValidationError(_("Out of place %s operator in search query") % op)
					# T: error for search query parsing, %s will be 'NOT', 'AND', or 'OR'
		elif not isinstance(tokens[i], (SearchQuery, SearchQueryTerm)):
			# all other operators should be removed by now, just to be sure
			op = '"%s"' % tokens[-1]
			raise SearchQueryValidationError(_("Out of place %s operator in search query") % op)
				# T: error for search query parsing, %s will be 'NOT', 'AND', or 'OR'

	# Check for implicit sub-groups, and return top level group as query
	tokens = [t for t in tokens if t != OPERATOR_AND] # implicit deafult, so remove already
	while OPERATOR_OR in tokens:
		i = tokens.index(OPERATOR_OR) # position first OR
		j = i # position last OR
		while j < len(tokens) and tokens[j] == OPERATOR_OR:
			j += 2

		group = [t for t in tokens[i-1:j] if t != OPERATOR_OR]
		if i == 1 and j == len(tokens):
			# We consumed the whole token list
			return SearchQuery(OPERATOR_OR, group)
		else:
			# Splice subgroup in list
			tokens = tokens[:i-1] + [SearchQuery(OPERATOR_OR, group)] + tokens[j:]
	else:
		# Final group is implicit AND group
		return SearchQuery(OPERATOR_AND, tokens)

