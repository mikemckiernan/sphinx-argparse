import ast
import os
import shutil
import sys
from argparse import ArgumentParser
from collections import defaultdict
from typing import Dict, List, Optional, Union, cast

from docutils import nodes
from docutils.frontend import OptionParser
from docutils.nodes import Element
from docutils.parsers.rst import Parser
from docutils.parsers.rst.directives import flag, unchanged
from docutils.statemachine import StringList
from docutils.utils import new_document
from sphinx.addnodes import pending_xref
from sphinx.application import Sphinx
from sphinx.builders import Builder
from sphinx.domains import Domain, Index
from sphinx.environment import BuildEnvironment
from sphinx.errors import ExtensionError
from sphinx.roles import XRefRole
from sphinx.util import logging
from sphinx.util.docutils import SphinxDirective
from sphinx.util.nodes import make_id, make_refnode, nested_parse_with_titles

from sphinxarg.parser import parse_parser, parser_navigate
from sphinxarg.utils import command_pos_args, target_to_anchor_id

from . import __version__

logger = logging.getLogger(__name__)


def map_nested_definitions(nested_content):
    if nested_content is None:
        raise Exception('Nested content should be iterable, not null')
    # build definition dictionary
    definitions = {}
    for item in nested_content:
        if not isinstance(item, nodes.definition_list):
            continue
        for subitem in item:
            if not isinstance(subitem, nodes.definition_list_item):
                continue
            if not len(subitem.children) > 0:
                continue
            classifier = '@after'
            idx = subitem.first_child_matching_class(nodes.classifier)
            if idx is not None:
                ci = subitem[idx]
                if len(ci.children) > 0:
                    classifier = ci.children[0].astext()
            if classifier is not None and classifier not in (
                '@replace',
                '@before',
                '@after',
                '@skip',
            ):
                raise Exception(f'Unknown classifier: {classifier}')
            idx = subitem.first_child_matching_class(nodes.term)
            if idx is not None:
                term = subitem[idx]
                if len(term.children) > 0:
                    term = term.children[0].astext()
                    idx = subitem.first_child_matching_class(nodes.definition)
                    if idx is not None:
                        subcontent = []
                        for _ in subitem[idx]:
                            if isinstance(_, nodes.definition_list):
                                subcontent.append(_)
                        definitions[term] = (classifier, subitem[idx], subcontent)

    return definitions


def render_list(l, markdown_help, settings=None):
    """
    Given a list of reStructuredText or MarkDown sections, return a docutils node list
    """
    if len(l) == 0:
        return []
    if markdown_help:
        from sphinxarg.markdown import parse_markdown_block

        return parse_markdown_block('\n\n'.join(l) + '\n')
    else:
        all_children = []
        for element in l:
            if isinstance(element, str):
                if settings is None:
                    settings = OptionParser(components=(Parser,)).get_default_values()
                document = new_document(None, settings)
                Parser().parse(element + '\n', document)
                all_children += document.children
            elif isinstance(element, nodes.definition):
                all_children += element

        return all_children


def ensure_unique_ids(items):
    """
    If action groups are repeated, then links in the table of contents will
    just go to the first of the repeats. This may not be desirable, particularly
    in the case of subcommands where the option groups have different members.
    This function updates the title IDs by adding _repeatX, where X is a number
    so that the links are then unique.
    """
    s = set()
    for item in items:
        for n in item.traverse(descend=True, siblings=True, ascend=False):
            if isinstance(n, nodes.section):
                ids = n['ids']
                for idx, id in enumerate(ids):
                    if id not in s:
                        s.add(id)
                    else:
                        i = 1
                        while f"{id}_repeat{i}" in s:
                            i += 1
                        ids[idx] = f"{id}_repeat{i}"
                        s.add(ids[idx])
                n['ids'] = ids


class ArgParseDirective(SphinxDirective):
    has_content = True
    required_arguments = 0
    option_spec = dict(
        module=unchanged,
        func=unchanged,
        ref=unchanged,
        prog=unchanged,
        path=unchanged,
        nodefault=flag,
        nodefaultconst=flag,
        filename=unchanged,
        manpage=unchanged,
        nosubcommands=unchanged,
        passparser=flag,
        noepilog=unchanged,
        nodescription=unchanged,
        markdown=flag,
        markdownhelp=flag,
        idxgroups=unchanged,
    )
    domain: Optional[Domain] = None
    idxgroups: Optional[List[str]] = None

    def _construct_manpage_specific_structure(self, parser_info):
        """
        Construct a typical man page consisting of the following elements:
            NAME (automatically generated, out of our control)
            SYNOPSIS
            DESCRIPTION
            OPTIONS
            FILES
            SEE ALSO
            BUGS
        """
        items = []
        # SYNOPSIS section
        synopsis_section = nodes.section(
            '',
            nodes.title(text='Synopsis'),
            nodes.literal_block(text=parser_info["bare_usage"]),
            ids=['synopsis-section'],
        )
        items.append(synopsis_section)
        # DESCRIPTION section
        if 'nodescription' not in self.options:
            description_section = nodes.section(
                '',
                nodes.title(text='Description'),
                nodes.paragraph(
                    text=parser_info.get(
                        'description',
                        parser_info.get('help', "undocumented").capitalize(),
                    )
                ),
                ids=['description-section'],
            )
            nested_parse_with_titles(self.state, self.content, description_section)
            items.append(description_section)
        if parser_info.get('epilog') and 'noepilog' not in self.options:
            # TODO: do whatever sphinx does to understand ReST inside
            # docstrings magically imported from other places. The nested
            # parse method invoked above seem to be able to do this but
            # I haven't found a way to do it for arbitrary text
            if description_section:
                description_section += nodes.paragraph(text=parser_info['epilog'])
            else:
                description_section = nodes.paragraph(text=parser_info['epilog'])
                items.append(description_section)
        # OPTIONS section
        options_section = nodes.section('', nodes.title(text='Options'), ids=['options-section'])
        if 'args' in parser_info:
            options_section += nodes.paragraph()
            options_section += nodes.subtitle(text='Positional arguments:')
            options_section += self._format_positional_arguments(parser_info)
        for action_group in parser_info['action_groups']:
            if 'options' in parser_info:
                options_section += nodes.paragraph()
                options_section += nodes.subtitle(text=action_group['title'])
                options_section += self._format_optional_arguments(action_group)

        # NOTE: we cannot generate NAME ourselves. It is generated by
        # docutils.writers.manpage
        # TODO: items.append(files)
        # TODO: items.append(see also)
        # TODO: items.append(bugs)

        if len(options_section.children) > 1:
            items.append(options_section)
        if 'nosubcommands' not in self.options:
            # SUBCOMMANDS section (non-standard)
            subcommands_section = nodes.section('', nodes.title(text='Sub-Commands'), ids=['subcommands-section'])
            if 'children' in parser_info:
                subcommands_section += self._format_subcommands(parser_info)
            if len(subcommands_section) > 1:
                items.append(subcommands_section)
        if os.getenv("INCLUDE_DEBUG_SECTION"):
            import json

            # DEBUG section (non-standard)
            debug_section = nodes.section(
                '',
                nodes.title(text="Argparse + Sphinx Debugging"),
                nodes.literal_block(text=json.dumps(parser_info, indent='  ')),
                ids=['debug-section'],
            )
            items.append(debug_section)
        return items

    def _format_positional_arguments(self, parser_info):
        assert 'args' in parser_info
        items = []
        for arg in parser_info['args']:
            arg_items = []
            if arg['help']:
                arg_items.append(nodes.paragraph(text=arg['help']))
            elif 'choices' not in arg:
                arg_items.append(nodes.paragraph(text='Undocumented'))
            if 'choices' in arg:
                arg_items.append(nodes.paragraph(text='Possible choices: ' + ', '.join(arg['choices'])))
            items.append(
                nodes.option_list_item(
                    '',
                    nodes.option_group('', nodes.option('', nodes.option_string(text=arg['metavar']))),
                    nodes.description('', *arg_items),
                )
            )
        return nodes.option_list('', *items)

    def _format_optional_arguments(self, parser_info):
        assert 'options' in parser_info
        items = []
        for opt in parser_info['options']:
            names = []
            opt_items = []
            for name in opt['name']:
                option_declaration = [nodes.option_string(text=name)]
                if opt['default'] is not None and opt['default'] not in [
                    '"==SUPPRESS=="',
                    '==SUPPRESS==',
                ]:
                    option_declaration += nodes.option_argument('', text='=' + str(opt['default']))
                names.append(nodes.option('', *option_declaration))
            if opt['help']:
                opt_items.append(nodes.paragraph(text=opt['help']))
            elif 'choices' not in opt:
                opt_items.append(nodes.paragraph(text='Undocumented'))
            if 'choices' in opt:
                opt_items.append(nodes.paragraph(text='Possible choices: ' + ', '.join(opt['choices'])))
            items.append(
                nodes.option_list_item(
                    '',
                    nodes.option_group('', *names),
                    nodes.description('', *opt_items),
                )
            )
        return nodes.option_list('', *items)

    def _format_subcommands(self, parser_info):
        assert 'children' in parser_info
        items = []
        for subcmd in parser_info['children']:
            subcmd_items = []
            if subcmd['help']:
                subcmd_items.append(nodes.paragraph(text=subcmd['help']))
            else:
                subcmd_items.append(nodes.paragraph(text='Undocumented'))
            items.append(
                nodes.definition_list_item(
                    '',
                    nodes.term('', '', nodes.strong(text=subcmd['bare_usage'])),
                    nodes.definition('', *subcmd_items),
                )
            )
        return nodes.definition_list('', *items)

    def _nested_parse_paragraph(self, text):
        content = nodes.paragraph()
        self.state.nested_parse(StringList(text.split("\n")), 0, content)
        return content

    def _open_filename(self):
        # try open with given path
        try:
            return open(self.options['filename'])
        except OSError:
            pass
        # try open with abspath
        try:
            return open(os.path.abspath(self.options['filename']))
        except OSError:
            pass
        # try open with shutil which
        try:
            return open(shutil.which(self.options['filename']))
        except OSError:
            pass
        # raise exception
        raise FileNotFoundError(self.options['filename'])

    def _print_subcommands(self, data, nested_content, markdown_help=False, settings=None):
        """
        Each subcommand is a dictionary with the following keys:

        ['usage', 'action_groups', 'bare_usage', 'name', 'help']

        In essence, this is all tossed in a new section with the title 'name'.
        Apparently there can also be a 'description' entry.
        """

        definitions = map_nested_definitions(nested_content)
        items = []
        conf = self.env.config.sphinx_argparse_conf
        domain = cast(SphinxArgParseDomain, self.env.domains[SphinxArgParseDomain.name])

        if 'children' in data:
            full_command = command_pos_args(data)
            node_id = make_id(self.env, self.state.document, None, full_command + "-sub-commands")
            target = nodes.target('', '', ids=[node_id])
            self.set_source_info(target)
            self.state.document.note_explicit_target(target)

            subcommands = nodes.section(ids=[node_id, "Sub-commands"])
            subcommands += nodes.title('Sub-commands', 'Sub-commands')
            subcommands['classes'] += conf.get('classes') or []

            for child in data['children']:
                full_command = command_pos_args(child)
                node_id = make_id(self.env, self.state.document, None, full_command)
                target = nodes.target('', '', ids=[node_id])
                self.set_source_info(target)
                self.state.document.note_explicit_target(target)

                sec = nodes.section(ids=[node_id, child['name']])
                if ('full_subcommand_name', True) in conf.items():
                    title = nodes.title(full_command, full_command)
                else:
                    title = nodes.title(child['name'], child['name'])
                sec += title
                sec['classes'] += conf.get('classes') or []

                domain.add_command(child, node_id, self.idxgroups)

                if 'description' in child and child['description']:
                    desc = [child['description']]
                elif child['help']:
                    desc = [child['help']]
                else:
                    desc = ['Undocumented']

                # Handle nested content
                subcontent = []
                if child['name'] in definitions:
                    classifier, s, subcontent = definitions[child['name']]
                    if classifier == '@replace':
                        desc = [s]
                    elif classifier == '@after':
                        desc.append(s)
                    elif classifier == '@before':
                        desc.insert(0, s)

                for element in render_list(desc, markdown_help):
                    sec += element

                usage = nodes.literal_block(text=child['bare_usage'])
                usage['classes'] += conf.get('classes') or []
                sec += usage

                for x in self._print_action_groups(child, nested_content + subcontent, markdown_help, settings=settings):
                    sec += x

                for x in self._print_subcommands(child, nested_content + subcontent, markdown_help, settings=settings):
                    sec += x

                if 'epilog' in child and child['epilog']:
                    for element in render_list([child['epilog']], markdown_help):
                        sec += element

                subcommands += sec
            items.append(subcommands)

        return items

    def _print_action_groups(self, data, nested_content, markdown_help=False, settings=None):
        """
        Process all 'action groups', which are also include 'Options' and 'Required
        arguments'. A list of nodes is returned.
        """
        definitions = map_nested_definitions(nested_content)
        nodes_list = []
        conf = self.env.config.sphinx_argparse_conf

        if 'action_groups' in data:
            for action_group in data['action_groups']:
                # Every action group is comprised of a section, holding a title, the description, and the option group (members)
                full_command = command_pos_args(data)
                node_id = make_id(self.env, self.state.document, None, full_command + "-" + action_group['title'].replace(' ', '-').lower())
                target = nodes.target('', '', ids=[node_id])
                self.set_source_info(target)
                self.state.document.note_explicit_target(target)

                section = nodes.section(ids=[node_id, action_group['title'].replace(' ', '-').lower()])
                section += nodes.title(action_group['title'], action_group['title'])
                section['classes'] += conf.get('classes') or []

                desc = []
                if action_group['description']:
                    desc.append(action_group['description'])
                # Replace/append/prepend content to the description according to nested content
                subcontent = []
                if action_group['title'] in definitions:
                    classifier, s, subcontent = definitions[action_group['title']]
                    if classifier == '@replace':
                        desc = [s]
                    elif classifier == '@after':
                        desc.append(s)
                    elif classifier == '@before':
                        desc.insert(0, s)
                    elif classifier == '@skip':
                        continue
                    if len(subcontent) > 0:
                        for k, v in map_nested_definitions(subcontent).items():
                            definitions[k] = v
                # Render appropriately
                for element in render_list(desc, markdown_help):
                    section += element

                local_definitions = definitions
                if len(subcontent) > 0:
                    local_definitions = {k: v for k, v in definitions.items()}
                    for k, v in map_nested_definitions(subcontent).items():
                        local_definitions[k] = v

                items = []
                # Iterate over action group members
                for entry in action_group['options']:
                    # Members will include:
                    #    default	The default value. This may be ==SUPPRESS==
                    #    name	A list of option names (e.g., ['-h', '--help']
                    #    help	The help message string
                    # There may also be a 'choices' member.
                    # Build the help text
                    arg = []
                    if 'choices' in entry:
                        arg.append(f"Possible choices: {', '.join(str(c) for c in entry['choices'])}\n")
                    if 'help' in entry:
                        arg.append(entry['help'])
                    if entry['default'] is not None and entry['default'] not in [
                        '"==SUPPRESS=="',
                        '==SUPPRESS==',
                    ]:
                        if entry['default'] == '':
                            arg.append('Default: ""')
                        else:
                            arg.append(f"Default: {entry['default']}")

                    # Handle nested content, the term used in the dict has the comma removed for simplicity
                    desc = arg
                    term = ' '.join(entry['name'])
                    if term in local_definitions:
                        classifier, s, subcontent = local_definitions[term]
                        if classifier == '@replace':
                            desc = [s]
                        elif classifier == '@after':
                            desc.append(s)
                        elif classifier == '@before':
                            desc.insert(0, s)
                    term = ', '.join(entry['name'])

                    n = nodes.option_list_item(
                        '',
                        nodes.option_group('', nodes.option_string(text=term)),
                        nodes.description('', *render_list(desc, markdown_help, settings)),
                    )
                    items.append(n)

                section += nodes.option_list('', *items)
                nodes_list.append(section)

        return nodes_list

    def run(self):
        self.domain = cast(SphinxArgParseDomain, self.env.get_domain(SphinxArgParseDomain.name))
        conf = self.env.config.sphinx_argparse_conf

        if 'module' in self.options and 'func' in self.options:
            module_name = self.options['module']
            attr_name = self.options['func']
        elif 'ref' in self.options:
            _parts = self.options['ref'].split('.')
            module_name = '.'.join(_parts[0:-1])
            attr_name = _parts[-1]
        elif 'filename' in self.options and 'func' in self.options:
            mod = {}
            f = self._open_filename()
            code = compile(f.read(), self.options['filename'], 'exec')
            exec(code, mod)
            attr_name = self.options['func']
            func = mod[attr_name]
        else:
            raise self.error(':module: and :func: should be specified, or :ref:, or :filename: and :func:')

        # Skip this if we're dealing with a local file, since it obviously can't be imported
        if 'filename' not in self.options:
            try:
                mod = __import__(module_name, globals(), locals(), [attr_name])
            except ImportError:
                raise self.error(f'Failed to import "{attr_name}" from "{module_name}".\n{sys.exc_info()[1]}')

            if not hasattr(mod, attr_name):
                raise self.error(('Module "%s" has no attribute "%s"\nIncorrect argparse :module: or :func: values?') % (module_name, attr_name))
            func = getattr(mod, attr_name)

        if isinstance(func, ArgumentParser):
            parser = func
        elif 'passparser' in self.options:
            parser = ArgumentParser()
            func(parser)
        else:
            parser = func()
        if 'path' not in self.options:
            self.options['path'] = ''
        path = str(self.options['path'])
        if 'prog' in self.options:
            parser.prog = self.options['prog']
        result = parse_parser(
            parser,
            skip_default_values='nodefault' in self.options,
            skip_default_const_values='nodefaultconst' in self.options,
        )
        result = parser_navigate(result, path)
        if 'manpage' in self.options:
            return self._construct_manpage_specific_structure(result)

        # Handle nested content, where markdown needs to be preprocessed
        items = []
        nested_content = nodes.paragraph()
        if 'markdown' in self.options:
            from sphinxarg.markdown import parse_markdown_block

            items.extend(parse_markdown_block('\n'.join(self.content) + '\n'))
        else:
            self.state.nested_parse(self.content, self.content_offset, nested_content)
            nested_content = nested_content.children
        # add common content between
        for item in nested_content:
            if not isinstance(item, nodes.definition_list):
                items.append(item)

        markdown_help = False
        if 'markdownhelp' in self.options:
            markdown_help = True
        if 'description' in result and 'nodescription' not in self.options:
            if markdown_help:
                items.extend(render_list([result['description']], True))
            else:
                items.append(self._nested_parse_paragraph(result['description']))

        if 'idxgroups' in self.options:
            try:
                self.idxgroups = ast.literal_eval(self.options['idxgroups'])
            except (SyntaxError, ValueError):
                message = f"""Error in "{self.name}". In file "{self.env.doc2path(self.env.docname, False)}"
                failed to parse idxgroups as a list: "{self.state_machine.line.strip()}".
                """
                raise self.error(message)
            self.idxgroups = [x.strip() for x in self.idxgroups]
        else:
            self.idxgroups = []

        full_command = command_pos_args(result)
        node_id = make_id(self.env, self.state.document, None, full_command)
        target = nodes.target('', '', ids=[node_id])
        items.append(target)
        self.set_source_info(target)
        self.state.document.note_explicit_target(target)

        self.domain.add_command(result, node_id, self.idxgroups)

        usage = nodes.literal_block(text=result['usage'])
        usage['classes'] += conf.get('classes') or []
        items.append(usage)
        items.extend(
            self._print_action_groups(
                result,
                nested_content,
                markdown_help,
                settings=self.state.document.settings,
            )
        )
        if 'nosubcommands' not in self.options:
            items.extend(
                self._print_subcommands(
                    result,
                    nested_content,
                    markdown_help,
                    settings=self.state.document.settings,
                )
            )
        if 'epilog' in result and 'noepilog' not in self.options:
            items.append(self._nested_parse_paragraph(result['epilog']))

        # Traverse the returned nodes, modifying the title IDs as necessary to avoid repeats
        ensure_unique_ids(items)

        return items


class CommandsIndex(Index):
    name = 'index'
    localname = 'Commands Index'

    def generate(self, docnames=None):
        content = defaultdict(list)

        commands = self.domain.get_objects()
        commands = sorted(commands, key=lambda command: command[0])

        for cmd, dispname, _typ, docname, anchor, priority in commands:
            content[cmd[0].lower()].append((cmd, priority, docname, anchor, docname, '', dispname))

        content = sorted(content.items())
        return content, True


class CommandsByGroupIndex(Index):
    name = 'by-group'
    localname = 'Commands by Group'

    def generate(self, docnames=None):
        content = defaultdict(list)

        bygroups = self.domain.data['commands-by-group']

        for group in sorted(bygroups):
            commands = sorted(bygroups[group], key=lambda command: command[0])
            for cmd, dispname, _typ, docname, anchor, priority in commands:
                content[group].append((cmd, priority, docname, anchor, docname, '', dispname))

        content = sorted(content.items())
        return content, True


class SphinxArgParseDomain(Domain):
    name = 'commands'
    label = 'commands-label'

    roles = {'command': XRefRole()}
    indices = {}  # type: ignore
    initial_data: Dict[str, Union[List, Dict]] = {
        'commands': [],
        'commands-by-group': defaultdict(list),
    }

    # Keep a list of the temporary index files that are created in the
    # source directory. The files are created if the command_xxx_in_toctree
    # option is set to True.
    temporary_index_files: List[str] = []

    def get_full_qualified_name(self, node):
        return f'{node.arguments[0]}'

    def get_objects(self):
        yield from self.data['commands']

    def resolve_xref(self, env: BuildEnvironment, fromdocname: str, builder: Builder, typ: str, target: str, node: pending_xref, contnode: Element):
        anchor_id = target_to_anchor_id(target)
        match = [(docname, anchor) for _cmd, _sig, _type, docname, anchor, _prio in self.get_objects() if anchor_id == anchor]

        if len(match) > 0:
            todocname = match[0][0]
            targ = match[0][1]

            return make_refnode(builder, fromdocname, todocname, targ, contnode, targ)
        else:
            logger.warning(f'Error, no command xref target from {fromdocname}:{target}')
            return None

    def add_command(self, result: Dict, anchor: str, groups: List[str] = None):
        """Add an argparse command to the domain."""
        full_command = command_pos_args(result)
        desc = "No description."
        if 'description' in result:
            desc = result['description']
        idx_entry = (full_command, desc, 'command', self.env.docname, anchor, 0)
        self.data['commands'].append(idx_entry)

        # A likely duplicate list of index entries is kept for the grouping.
        # A separate list is kept to avoid the edge case that a command is used
        # once as part of a group (with idxgroups) and another time without the
        # option.
        for group in groups or []:
            self.data['commands-by-group'][group].append(idx_entry)


def delete_dummy_file(app: Sphinx, _) -> None:
    assert app.env is not None
    domain = cast(SphinxArgParseDomain, app.env.domains[SphinxArgParseDomain.name])
    for fpath in domain.temporary_index_files:
        if os.path.exists(fpath):
            os.unlink(fpath)


def create_temp_dummy_file(app: Sphinx, domain: Domain, docname: str, title: str) -> None:
    dummy_file = os.path.join(app.srcdir, docname)
    domain = cast(SphinxArgParseDomain, domain)
    if os.path.exists(dummy_file):
        raise ExtensionError(f'The Sphinx project cannot include a file named "{docname}" in the source directory.')
    with open(dummy_file, "w") as f:
        f.write(f"{title}\n")
        f.write(f"{len(title) * '='}\n")
        f.write("\n")
        f.write("Temporary file that is replaced with an index from the sphinxarg extension.\n")
        f.write(f"Creating this temporary file enables you to add {docname} to the toctree.\n")
    domain.temporary_index_files.append(dummy_file)


def configure_ext(app: Sphinx) -> None:
    conf = app.config.sphinx_argparse_conf
    assert app.env is not None
    domain = cast(SphinxArgParseDomain, app.env.domains[SphinxArgParseDomain.name])
    by_group_index = CommandsByGroupIndex
    build_index = False
    build_by_group_index = False
    if 'commands_by_group_index_file_suffix' in conf:
        build_by_group_index = True
        by_group_index.name = conf.get('commands_by_group_index_file_suffix')
    if 'commands_by_group_index_title' in conf:
        build_by_group_index = True
        by_group_index.localname = conf.get('commands_by_group_index_title')
    if ('commands_index_in_toctree', True) in conf.items():
        build_index = True
        docname = f"{SphinxArgParseDomain.name}-{CommandsIndex.name}.rst"
        create_temp_dummy_file(app, domain, docname, f"{CommandsIndex.localname}")
    if ('commands_by_group_index_in_toctree', True) in conf.items():
        build_by_group_index = True
        docname = f"{SphinxArgParseDomain.name}-{by_group_index.name}.rst"
        create_temp_dummy_file(app, domain, docname, f"{by_group_index.localname}")

    if build_index or ('build_commands_index', True) in conf.items():
        domain.indices.append(CommandsIndex)  # type: ignore
    if build_by_group_index or ('build_commands_by_group_index', True) in conf.items():
        domain.indices.append(by_group_index)  # type: ignore

    # Call setup so that :ref:`commands-...` are link targets.
    domain.setup()


def setup(app: Sphinx):
    app.add_domain(SphinxArgParseDomain)
    app.add_directive('argparse', ArgParseDirective)
    app.add_config_value('sphinx_argparse_conf', {}, 'html', Dict)
    app.connect('builder-inited', configure_ext)
    app.connect('build-finished', delete_dummy_file)
    return {'parallel_read_safe': True, 'version': __version__}
