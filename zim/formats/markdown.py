
# Copyright 2012,2013 Jaap Karssenberg <jaap.karssenberg@gmail.com>
# Copyright 2026 - Markdown native format support

'''This module handles parsing and dumping markdown text with pandoc extensions.

It supports full round-trip as a native storage format: pages can be
stored as ``.md`` files with YAML front matter for metadata.
'''

import os.path
import re
import logging

logger = logging.getLogger('zim.formats.markdown')

from zim.parse import convert_space_to_tab, fix_unicode_whitespace
from zim.parse.encode import escape_string, url_encode, URL_ENCODE_DATA, \
	split_escaped_string, unescape_string
from zim.parse.regexparser import Rule, RegexParser
from zim.parse.links import is_url_link, match_url_link, url_link_re, old_url_link_re, link_type, is_path_re

from zim.formats import *
from zim.formats.plain import Dumper as TextDumper


MARKDOWN_FORMAT_VERSION = 'markdown 1.0'

info = {
	'name': 'markdown',
	'desc': 'Markdown Text (pandoc)',
	'mimetype': 'text/markdown',
	'extension': 'md',
	'native': True,
	'import': True,
	'export': True,
	'usebase': True,
}


# ---- YAML front matter helpers ----

_yaml_front_matter_re = re.compile(r'\A---[ \t]*\n(.*?\n)---[ \t]*\n\n?', re.DOTALL)
_yaml_kv_re = re.compile(r'^([\w-]+):\s+(.*?)$', re.M)


def parse_yaml_front_matter(text):
	'''Parse YAML front matter delimited by --- lines.

	@returns: tuple of (body_text, meta_dict)
	'''
	meta = {}
	m = _yaml_front_matter_re.match(text)
	if m:
		yaml_block = m.group(1)
		for kv in _yaml_kv_re.finditer(yaml_block):
			meta[kv.group(1)] = kv.group(2).strip().strip('"').strip("'")
		text = text[m.end():]
	return text, meta


def dump_yaml_front_matter(meta):
	'''Dump metadata as YAML front matter string.

	@param meta: dict of key-value pairs
	@returns: string with YAML front matter block, or empty string if no meta
	'''
	if not meta:
		return ''
	lines = ['---\n']
	for k, v in meta.items():
		v = str(v).strip()
		if ':' in v or '#' in v or "'" in v:
			v = '"%s"' % v.replace('"', '\\"')
		lines.append('%s: %s\n' % (k, v))
	lines.append('---\n')
	return ''.join(lines)


# ---- Markdown bullet patterns ----

# GFM task list: - [ ], - [x], - [X]
# Regular bullets: -, *, +
# Numbered: 1. 2. etc.
md_bullet_pattern = r'(?:[-*+]|\d+\.)[ \t]+'
md_checkbox_pattern = r'(?:[-*+])[ \t]+\[([ xX*><])\][ \t]+'
md_bullet_line_re = re.compile(
	r'^([ \t]*)((?:[-*+]|\d+\.)[ \t]+(?:\[[ xX*><]\][ \t]+)?)(.*$\n?)',
	re.M
)
md_number_bullet_re = re.compile(r'^(\d+)\.$')
md_empty_lines_re = re.compile(r'((?:^[ \t]*\n)+)', re.M | re.U)
md_unindented_line_re = re.compile(r'^\S', re.M)


# ---- Markdown Parser ----

class MarkdownParser(object):
	'''Parser for Markdown text using the same 3-level architecture
	as WikiParser: block -> list/indent -> inline.
	'''

	BULLETS = {
		'[ ]': UNCHECKED_BOX,
		'[x]': XCHECKED_BOX,
		'[X]': XCHECKED_BOX,
		'[*]': CHECKED_BOX,
		'[>]': MIGRATED_BOX,
		'[<]': TRANSMIGRATED_BOX,
	}

	def __init__(self):
		self.inline_parser = self._init_inline_parser()
		self.list_and_indent_parser = self._init_intermediate_parser()
		self.block_parser = self._init_block_parser()

	def __call__(self, builder, text):
		builder.start(FORMATTEDTEXT)
		if text:
			self.block_parser(builder, text)
		builder.end(FORMATTEDTEXT)

	def _init_inline_parser(self):
		descent = lambda *a: self.nested_inline_parser_below_link(*a)
		self.nested_inline_parser_below_link = (
			Rule(TAG, r'(?<!\S)@\w+', process=self.parse_tag)
			| Rule(EMPHASIS, r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', descent=descent)
			| Rule(STRONG, r'\*\*(?!\*)(.+?)\*\*', descent=descent)
			| Rule(MARK, r'__(?!_)(.+?)__', descent=descent)
			| Rule(SUBSCRIPT, r'(?<!~)~(?!~)(.+?)(?<!~)~(?!~)', descent=descent)
			| Rule(SUPERSCRIPT, r'\^(?!\^)(.+?)\^', descent=descent)
			| Rule(STRIKE, r'~~(?!~)(.+?)~~', descent=descent)
			| Rule(VERBATIM, r'(?<!`)``(?!`)(.+?)(?<!`)``(?!`)')
			| Rule(VERBATIM, r'(?<!`)`(?!`)(.+?)(?<!`)`(?!`)')
		)

		descent = lambda *a: self.inline_parser(*a)
		return (
			Rule(LINK, r'<([a-zA-Z][a-zA-Z0-9.+-]*:[^\s>]+)>', process=self.parse_autolink)
			| Rule(LINK, url_link_re, process=self.parse_url)
			| Rule(IMAGE, r'!\[([^\]]*)\]\(([^)]+)\)(\{[^}]*\})?', process=self.parse_image)
			| Rule(LINK, r'\[([^\]]+)\]\(([^)]+)\)', process=self.parse_link)
			| Rule(ANCHOR, r'\{\#(\w[\w-]*)\}', process=self.parse_anchor)
			| Rule(TAG, r'(?<!\S)@\w+', process=self.parse_tag)
			| Rule(EMPHASIS, r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', descent=descent)
			| Rule(STRONG, r'\*\*(?!\*)(.+?)\*\*', descent=descent)
			| Rule(MARK, r'__(?!_)(.+?)__', descent=descent)
			| Rule(SUBSCRIPT, r'(?<!~)~(?!~)(.+?)(?<!~)~(?!~)', descent=descent)
			| Rule(SUPERSCRIPT, r'\^(?!\^)(.+?)\^', descent=descent)
			| Rule(STRIKE, r'~~(?!~)(.+?)~~', descent=descent)
			| Rule(VERBATIM, r'(?<!`)``(?!`)(.+?)(?<!`)``(?!`)')
			| Rule(VERBATIM, r'(?<!`)`(?!`)(.+?)(?<!`)`(?!`)')
		)

	def _init_intermediate_parser(self):
		p = RegexParser(
			Rule(
				'X-Bullet-List',
				r'''(
					^[ \t]* (?:[-*+]|\d+\.) [ \t]+ (?:\[[ xX*><]\][ \t]+)? .* $\n?    # Line with bullet
					(?:
						^[ \t]* (?:[-*+]|\d+\.) [ \t]+ (?:\[[ xX*><]\][ \t]+)? .* $\n? # More items
					)*
				)''',
				process=self.parse_list
			),
			Rule(
				'X-Blockquote',
				r'''(
					^(?P<bq_indent>>[ \t]?) .* $\n?			# Line with > prefix
					(?:
						^>[ \t]? .* $\n?						# More quoted lines
					)*
				)''',
				process=self.parse_blockquote
			),
		)
		p.process_unmatched = self.inline_parser
		return p

	def _init_block_parser(self):
		p = RegexParser(
			# Zim object in fenced code block: ```{object_type: params}
			# Must come before generic fenced code block
			Rule(OBJECT, r'''
				^[ \t]* `{3,} [ \t]* \{ (\S+) : [ \t]* (.*?) \} [ \t]* \n		# ```{type: params}
				( (?:^.*\n)*? )													# body
				^[ \t]* `{3,} [ \t]* \n											# closing ```
			''',
				process=self.parse_object
			),
			# Backtick fenced code block (``` ... ```)
			Rule(VERBATIM_BLOCK, r'''
				^[ \t]* (`{3,}) [ \t]* (.*?) \n					# opening backtick fence with optional info
				( (?:^.*\n)*? )									# multi-line content
				^[ \t]* `{3,} [ \t]* \n							# closing backtick fence
			''',
				process=self.parse_fenced_code
			),
			# Tilde fenced code block (~~~ ... ~~~)
			Rule(VERBATIM_BLOCK, r'''
				^[ \t]* (~{3,}) [ \t]* (.*?) \n					# opening tilde fence with optional info
				( (?:^.*\n)*? )									# multi-line content
				^[ \t]* ~{3,} [ \t]* \n							# closing tilde fence
			''',
				process=self.parse_fenced_code
			),
			# ATX headings: # Heading
			Rule(HEADING,
				r'^(\#{1,6})[ \t]+(\S.*?)[ \t]*\#*[ \t]*$\n?',
				process=self.parse_heading
			),
			# GFM pipe table
			Rule(TABLE, r'''
				^(\|.+\|)[ \t]*\n								# header row
				^([ \t]*\|[ \t\-:|]+\|[ \t]*\n)				# separator row
				((?:^[ \t]*\|.+\|[ \t]*\n)+)					# body rows
			''',
				process=self.parse_table
			),
			# Horizontal rule
			Rule(LINE, r'^[ \t]*(?:[-*_][ \t]*){3,}$\n?', process=self.parse_line),
		)
		p.process_unmatched = self.parse_para
		return p

	# --- Block-level handlers ---

	def parse_heading(self, builder, hashes, text):
		level = min(len(hashes), 6)
		text = text.rstrip() + '\n'
		builder.start(HEADING, {'level': level})
		self.inline_parser(builder, text)
		builder.end(HEADING)

	def parse_fenced_code(self, builder, fence, info, text):
		attrib = None
		if info and info.strip():
			# Store language as an attribute for potential source view
			lang = info.strip().split()[0]
			if lang:
				attrib = {'lang': lang}
		builder.append(VERBATIM_BLOCK, attrib, text)

	def parse_object(self, builder, otype, param, body):
		otype = otype.strip().lower()
		attrib = {}

		from zim.formats.wiki import param_re
		for match in param_re.finditer(param):
			key = match.group(1).lower()
			value = match.group(2)
			if value.startswith('"') and len(value) > 1:
				value = value[1:-1].replace('""', '"')
			attrib[key] = value

		attrib['type'] = otype
		builder.append(OBJECT, attrib, body)

	def parse_table(self, builder, headerline, alignstyle, body):
		headerrow = split_escaped_string(headerline.strip().strip('|'), '|')
		rows = [
			split_escaped_string(line.strip().strip('|'), '|')
				for line in body.strip().split('\n') if line.strip()
		]

		n_cols = max(len(headerrow), max(len(r) for r in rows) if rows else 0)

		aligns = []
		for celltext in alignstyle.strip().strip('|').split('|'):
			celltext = celltext.strip()
			if celltext.startswith(':') and celltext.endswith(':'):
				alignment = 'center'
			elif celltext.startswith(':'):
				alignment = 'left'
			elif celltext.endswith(':'):
				alignment = 'right'
			else:
				alignment = 'normal'
			aligns.append(alignment)

		while len(aligns) < n_cols:
			aligns.append('normal')

		headers = []
		wraps = []
		for celltext in headerrow:
			headers.append(celltext.strip())
			wraps.append(0)

		while len(headers) < n_cols:
			headers.append('')
			wraps.append(0)

		attrib = {'aligns': ','.join(aligns), 'wraps': ','.join(map(str, wraps))}
		builder.start(TABLE, attrib)

		builder.start(HEADROW)
		for celltext in headers:
			celltext = unescape_string(celltext.strip()) or ' '
			builder.append(HEADDATA, {}, celltext)
		builder.end(HEADROW)

		for bodyrow in rows:
			while len(bodyrow) < n_cols:
				bodyrow.append('')
			builder.start(TABLEROW)
			for celltext in bodyrow:
				builder.start(TABLEDATA)
				celltext = unescape_string(celltext.strip()) or ' '
				self.inline_parser(builder, celltext)
				builder.end(TABLEDATA)
			builder.end(TABLEROW)

		builder.end(TABLE)

	def parse_para(self, builder, text):
		if text.isspace():
			builder.text(text)
		else:
			for block in md_empty_lines_re.split(text):
				if not block:
					pass
				elif block.isspace():
					builder.text(block)
				else:
					block = convert_space_to_tab(block)
					builder.start(PARAGRAPH)
					self.list_and_indent_parser(builder, block)
					builder.end(PARAGRAPH)

	def parse_blockquote(self, builder, text, indent=None):
		# Strip leading '> ' from each line
		lines = text.splitlines(True)
		stripped = []
		for line in lines:
			if line.startswith('> '):
				stripped.append(line[2:])
			elif line.startswith('>'):
				stripped.append(line[1:])
			else:
				stripped.append(line)
		inner = ''.join(stripped)
		builder.start(BLOCK, {'indent': 1})
		self.inline_parser(builder, inner)
		builder.end(BLOCK)

	def parse_list(self, builder, text, indent=None):
		lines = text.splitlines(True)
		self.parse_list_lines(builder, lines)

	def parse_list_lines(self, builder, lines):
		stack = [(None, -1)]  # (list_type, indent_level)

		def get_indent(line):
			count = 0
			for ch in line:
				if ch == '\t':
					count += 4
				elif ch == ' ':
					count += 1
				else:
					break
			return count

		def start_list(number_m, my_indent, list_indent=None):
			attrib = {'indent': list_indent} if (list_indent is not None and len(stack) == 1) else None
			if number_m:
				l = NUMBEREDLIST
				attrib = attrib or {}
				attrib['start'] = number_m.group(1)
			else:
				l = BULLETLIST
			builder.start(l, attrib)
			stack.append((l, my_indent))

		for line in lines:
			m = md_bullet_line_re.match(line)
			if not m:
				continue  # skip malformed lines

			prefix = m.group(1)
			bullet_full = m.group(2)
			text = m.group(3)
			my_indent = get_indent(prefix)

			# Parse bullet type and checkbox
			bullet_stripped = bullet_full.strip()
			checkbox_m = re.match(r'[-*+]\s+\[([ xX*><])\]', bullet_stripped)

			if checkbox_m:
				checkbox_char = checkbox_m.group(1)
				checkbox_map = {
					' ': UNCHECKED_BOX,
					'x': XCHECKED_BOX,
					'X': XCHECKED_BOX,
					'*': CHECKED_BOX,
					'>': MIGRATED_BOX,
					'<': TRANSMIGRATED_BOX,
				}
				bullet_type = checkbox_map.get(checkbox_char, UNCHECKED_BOX)
			else:
				# Check for numbered list
				num_m = re.match(r'(\d+)\.', bullet_stripped)
				if num_m:
					bullet_type = None  # numbered
				else:
					bullet_type = BULLET

			number_m = re.match(r'(\d+)\.', bullet_stripped) if bullet_type is None else None

			if my_indent > stack[-1][-1]:
				start_list(number_m, my_indent)
			elif len(stack) > 2 and my_indent <= stack[-2][-1]:
				while len(stack) > 2 and my_indent <= stack[-2][-1]:
					l, i = stack.pop()
					builder.end(l)
			elif (stack[-1][0] == NUMBEREDLIST and number_m is None) \
				or (stack[-1][0] == BULLETLIST and number_m is not None):
					l, x = stack.pop()
					builder.end(l)
					start_list(number_m, my_indent)

			if stack[-1][0] == NUMBEREDLIST:
				attrib = None
			else:
				attrib = {'bullet': bullet_type} if bullet_type else {'bullet': BULLET}

			builder.start(LISTITEM, attrib)
			if text:
				self.inline_parser(builder, text)
			builder.end(LISTITEM)

		while len(stack) > 1:
			l, x = stack.pop()
			builder.end(l)

	# --- Inline handlers ---

	def parse_link(self, builder, text, href):
		'''Parse [text](href) links'''
		href = href.strip()
		text = text.strip()

		if not href:
			return

		# Detect if this is an internal page link
		if not is_url_link(href) and not href.startswith('#'):
			_, ext = os.path.splitext(href.split('?')[0])
			if ext and ext.lower() != '.md':
				# Non-wiki file reference (e.g. document.pdf).
				# Ensure href looks like a path so link_type() returns 'file'
				# and the indexer does not create a placeholder page.
				if not is_path_re.match(href):
					href = './' + href
			else:
				# Internal page link: strip .md and convert separators to page names
				if href.lower().endswith('.md'):
					href = href[:-3]
				href = href.replace('/', ':').replace('%20', ' ')

		if text and text != href:
			builder.start(LINK, {'href': href})
			self.nested_inline_parser_below_link(builder, text)
			builder.end(LINK)
		else:
			builder.append(LINK, {'href': href}, text or href)

	@staticmethod
	def parse_image(builder, alt, src, props_str=None):
		'''Parse ![alt](src){props} images'''
		attrib = ParserClass.parse_image_url(src.strip())

		if alt:
			attrib['alt'] = alt

		# Parse Pandoc-style properties: { width=500px height=20px #id }
		if props_str:
			props_str = props_str.strip('{}').strip()
			for part in props_str.split():
				if part.startswith('#'):
					attrib['id'] = part[1:]
				elif '=' in part:
					k, v = part.split('=', 1)
					# Strip 'px' suffix for width/height
					if k in ('width', 'height') and v.endswith('px'):
						v = v[:-2]
					attrib[k] = v

		builder.append(IMAGE, attrib)

	def parse_url(self, builder, *a):
		text = a[0]
		url = match_url_link(text)
		if url is None:
			self.inline_parser.backup_parser_offset(len(text) - 1)
			builder.text(text[0])
		elif url != text:
			self.inline_parser.backup_parser_offset(len(text) - len(url))
			builder.append(LINK, {'href': url}, url)
		else:
			builder.append(LINK, {'href': url}, url)

	@staticmethod
	def parse_autolink(builder, href):
		'''Parse <url> autolinks'''
		builder.append(LINK, {'href': href}, href)

	@staticmethod
	def parse_tag(builder, text):
		builder.append(TAG, {'name': text[1:]}, text)

	@staticmethod
	def parse_anchor(builder, name):
		builder.append(ANCHOR, {'name': name})

	@staticmethod
	def parse_line(builder, text):
		builder.append(LINE)


markdownparser = MarkdownParser()  #: singleton instance


class Parser(ParserClass):
	'''Parser class for reading Markdown files.

	Handles both regular markdown text and file-level input with
	YAML front matter (when file_input=True).
	'''

	def parse(self, input, file_input=False):
		if not isinstance(input, str):
			input = ''.join(input)

		input = input.replace('\u2029', ' ')  # Unicode PARAGRAPH SEPARATOR
		input = fix_unicode_whitespace(input)

		meta = None
		if file_input:
			input, meta = parse_yaml_front_matter(input)

		builder = ParseTreeBuilder()
		markdownparser(builder, input)

		parsetree = builder.get_parsetree()
		if meta is not None:
			for k, v in list(meta.items()):
				if k not in ('Content-Type', 'Format'):
					parsetree.meta[k] = v
		return parsetree


class Dumper(TextDumper):
	'''Dumper class for writing Markdown files.

	Supports both export mode (with linker, for resolving links relative
	to export target) and native file output mode (without linker,
	using raw hrefs suitable for storage).
	'''

	BULLETS = {
		UNCHECKED_BOX: '-',  # will add [ ] in dump_li
		XCHECKED_BOX: '-',   # will add [x] in dump_li
		CHECKED_BOX: '-',    # will add [x] in dump_li
		MIGRATED_BOX: '-',   # will add [>] in dump_li
		TRANSMIGRATED_BOX: '-', # will add [<] in dump_li
		BULLET: '-',
	}

	CHECKBOX_MARKS = {
		UNCHECKED_BOX: '[ ]',
		XCHECKED_BOX: '[x]',
		CHECKED_BOX: '[x]',
		MIGRATED_BOX: '[>]',
		TRANSMIGRATED_BOX: '[<]',
	}

	TAGS = {
		EMPHASIS: ('*', '*'),
		STRONG: ('**', '**'),
		MARK: ('__', '__'),
		STRIKE: ('~~', '~~'),
		VERBATIM: ('`', '`'),
		TAG: ('', ''),  # @tag rendered as-is
		SUBSCRIPT: ('~', '~'),
		SUPERSCRIPT: ('^', '^'),
	}

	def dump(self, tree, file_output=False):
		if file_output:
			# Native mode: dump with YAML front matter, no linker needed
			header_meta = {}
			if hasattr(tree, 'meta') and tree.meta:
				header_meta.update(tree.meta)

			if 'Content-Type' not in header_meta:
				header_meta['Content-Type'] = 'text/markdown'
			if 'Format' not in header_meta:
				header_meta['Format'] = MARKDOWN_FORMAT_VERSION

			body = TextDumper.dump(self, tree)
			if body and not body[-1].endswith('\n'):
				body[-1] = body[-1] + '\n'
			return [dump_yaml_front_matter(header_meta), '\n'] + body
		elif self.linker:
			return TextDumper.dump(self, tree)
		else:
			# No linker and not file_output - just dump raw
			return TextDumper.dump(self, tree)

	def dump_indent(self, tag, attrib, strings):
		if attrib and 'indent' in attrib:
			prefix = '> ' * int(attrib['indent'])
			return self.prefix_lines(prefix, strings)
		else:
			return strings

	dump_p = dump_indent
	dump_div = dump_indent

	def dump_list(self, tag, attrib, strings):
		if 'indent' in attrib:
			# Use spaces for markdown list indentation
			indent_level = int(attrib['indent'])
			prefix = '    ' * indent_level
			del attrib['indent']
			strings = TextDumper.dump_list(self, tag, attrib, strings)
			return self.prefix_lines(prefix, strings)

		strings = TextDumper.dump_list(self, tag, attrib, strings)

		if self.context[-1].tag == LISTITEM:
			# sub-list - indent
			return self.prefix_lines('    ', strings)
		else:
			# top level list - blank line separation is handled by
			# inter-element whitespace in the tree, so no extra \n needed
			return strings

	dump_ul = dump_list
	dump_ol = dump_list

	def dump_li(self, tag, attrib, strings):
		# Handle numbered lists - convert letters to numbers
		if self.context[-1].tag in (BULLETLIST, NUMBEREDLIST):
			if self.context[-1].tag == NUMBEREDLIST \
				and not self.context[-1].attrib.get('_iter'):
					iter = self.context[-1].attrib.get('start', '1')
					self.context[-1].attrib['_iter'] = convert_list_iter_letter_to_number(iter)

		# Get the base list item from parent
		result = TextDumper.dump_li(self, tag, attrib, strings)

		# Add checkbox syntax for task list items
		bullet_type = attrib.get('bullet', BULLET) if attrib else BULLET
		if bullet_type in self.CHECKBOX_MARKS:
			checkbox = self.CHECKBOX_MARKS[bullet_type]
			# Insert checkbox after "- "
			result_list = list(result)
			for i, s in enumerate(result_list):
				if s == ' ' and i > 0:
					result_list.insert(i + 1, checkbox + ' ')
					break
			return tuple(result_list)

		return result

	def dump_pre(self, tag, attrib, strings):
		# Use fenced code blocks
		lang = ''
		if attrib and 'lang' in attrib:
			lang = attrib['lang']
		result = ['```%s\n' % lang]
		result.extend(strings)
		if result and not result[-1].endswith('\n'):
			result[-1] = result[-1] + '\n'
		result.append('```\n')

		if attrib and 'indent' in attrib:
			prefix = '> ' * int(attrib['indent'])
			return self.prefix_lines(prefix, result)

		return result

	def dump_h(self, tag, attrib, strings):
		level = int(attrib['level'])
		if level < 1:
			level = 1
		elif level > 6:
			level = 6
		prefix = '#' * level
		strings.insert(0, prefix + ' ')
		# Ensure heading ends with newline
		text = strings.pop()
		strings.append(text.rstrip() + '\n')
		return strings

	def dump_anchor(self, tag, attrib, strings=None):
		return ('{#%s}' % attrib['name'],)

	def dump_link(self, tag, attrib, strings=None):
		assert 'href' in attrib, \
			'BUG: link misses href: %s "%s"' % (attrib, strings)

		href = attrib['href']
		text = ''.join(strings) if strings else ''

		if self.linker:
			# Export mode: resolve links through linker
			href = self.linker.link(href)
			text = text or href
			if href == text and old_url_link_re.match(href):
				return ['<', href, '>']
			else:
				return ['[%s](%s)' % (text, href)]
		else:
			# Native / file mode: use raw href
			# For page links (not URLs), convert to relative .md path
			if is_url_link(href) or href.startswith('#'):
				# External URL or anchor reference
				text = text or href
				if href == text and old_url_link_re.match(href):
					return ['<', href, '>']
				return ['[%s](%s)' % (text, href)]
			else:
				# Internal page link - convert : to / and add .md
				# But if the href already has a non-.md file extension (e.g. "document.pdf"),
				# treat it as a file reference and leave it as-is.
				page_href = href.replace(':', '/').replace(' ', '%20')
				_, ext = os.path.splitext(page_href)
				if ext and ext.lower() != '.md':
					# file reference — strip ./ prefix added by parse_link
					if page_href.startswith('./'):
						page_href = page_href[2:]
				elif not page_href.lower().endswith('.md'):
					page_href = page_href + '.md'
				text = text or href
				return ['[%s](%s)' % (text, page_href)]

	def dump_img(self, tag, attrib, strings=None):
		if self.linker:
			src = self.linker.img(attrib['src'])
		else:
			src = attrib.get('src', '')

		text = attrib.get('alt', '')

		# Pandoc-style dimensions: ![alt](src){ width=500px height=20px }
		dimensions = filter(lambda i: i[0] in ['width', 'height'], attrib.items())
		properties = ["%s=%spx" % (k, v) for k, v in dimensions]

		if 'id' in attrib:
			properties.append('#' + attrib['id'])

		if len(properties) > 0:
			props = '{ %s }' % (' '.join(properties))
		else:
			props = ''

		if 'href' in attrib:
			href = attrib['href']
			if self.linker:
				href = self.linker.link(href)
			return ['[![%s](%s)%s](%s)' % (text, src, props, href)]

		return ['![%s](%s)%s' % (text, src, props)]

	def dump_object_fallback(self, tag, attrib, strings=None):
		assert "type" in attrib, "Undefined type of object"

		opts = []
		for key, value in sorted(list(attrib.items())):
			if key in ('type', 'indent') or value is None:
				continue
			opts.append(' %s="%s"' % (key, str(value).replace('"', '""')))

		if not strings:
			strings = []
		return ['```{', attrib['type'], ':'] + opts + ['}\n'] + strings + ['```\n']

	def dump_table(self, tag, attrib, strings):
		table = []
		rows = strings

		aligns, _wraps = TableParser.get_options(attrib)
		maxwidths = TableParser.width2dim(rows)
		headsep = TableParser.headsep(maxwidths, aligns, x='|', y='-')
		rowline = lambda row: TableParser.rowline(row, maxwidths, aligns)

		table += [rowline(rows[0])]
		table.append(headsep)
		table += [rowline(row) for row in rows[1:]]
		return [line + "\n" for line in table]

	def dump_td(self, tag, attrib, strings):
		text = ''.join(strings) if strings else ''
		return [escape_string(text.replace('\n', '<br>'), '|')]

	dump_th = dump_td

	def dump_line(self, tag, attrib, strings=None):
		return '---\n'
