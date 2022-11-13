from collections import defaultdict
from typing import Dict, List, Union

from sphinx.domains import Domain, Index
from sphinx.roles import XRefRole
from sphinx.util.nodes import make_refnode

from .utils import command_anchor_id, command_pos_args


class CommandsIndex(Index):
    name = 'index'
    localname = 'Commands Index'

    def generate(self, docnames=None):
        content = defaultdict(list)

        commands = self.domain.get_objects()
        commands = sorted(commands, key=lambda command: command[0])

        for _cmd, dispname, typ, docname, anchor, _priority in commands:
            content[dispname[0].lower()].append((dispname, 0, docname, anchor, docname, '', typ))

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
            for _cmd, dispname, typ, docname, anchor, _priority in commands:
                content[group].append((dispname, 0, docname, anchor, docname, '', typ))

        content = sorted(content.items())
        return content, True


class SphinxArgParseDomain(Domain):
    name = 'commands'
    label = 'commands-label'
    roles = {'ref': XRefRole()}
    initial_data: Dict[str, Union[List, Dict]] = {
        'commands': [],
        'commands-by-group': defaultdict(list),
    }

    indices = {}  # type: ignore

    # Keep a list of the temporary index files that are created in the
    # source directory. The files are created if the command_xxx_in_toctree
    # option is set to True.
    temporary_index_files: List[str] = []

    def get_full_qualified_name(self, node):
        return f'{node.arguments[0]}'

    def get_objects(self):
        yield from self.data['commands']

    def resolve_xref(self, env, fromdocname, builder, typ, target, node, contnode):
        match = [(docname, anchor) for _cmd, sig, typ, docname, anchor, prio in self.get_objects() if sig == target]

        if len(match) > 0:
            todocname = match[0][0]
            targ = match[0][1]

            return make_refnode(builder, fromdocname, todocname, targ, contnode, targ)
        else:
            print(f'Error, no command target from {fromdocname}:{target}')
            return None

    def add_command(self, result: Dict, groups: List[str] = None):
        """Add an argparse command to the domain."""
        cmd = command_pos_args(result)
        anchor = command_anchor_id(result)
        desc = "No description."
        if 'description' in result:
            desc = result['description']

        #  (name, grouptype, page, anchor, extra, qualifier, description)
        idx_entry = (cmd, cmd, desc, self.env.docname, anchor, 0)
        self.data['commands'].append(idx_entry)

        # A likely duplicate list of index entries is kept for the grouping.
        # A separate list is kept to avoid the edge case that a command is used
        # once as part of a group (with idxgroups) and another time without the
        # option.
        for group in groups or []:
            self.data['commands-by-group'][group].append(idx_entry)
