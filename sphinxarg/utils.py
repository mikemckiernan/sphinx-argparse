def command_anchor_id(result: dict) -> str:
    """Returns a string that is suitable for an HTML anchor ID.

    If the supplied result from argparse includes a parent
    command, the parent command `name` or `prog` value is
    prefixed to the ID.

    >>> x, y, z = {}, {}, {}
    >>> x['prog']='simple-command'
    >>> command_anchor_id(x)
    'simple-command'

    >>> y['name']='sub-command-of-simple'
    >>> y['parent']=x
    >>> command_anchor_id(y)
    'simple-command-sub-command-of-simple'

    >>> z['name']='deeply-nested'
    >>> z['parent']=y
    >>> command_anchor_id(z)
    'simple-command-sub-command-of-simple-deeply-nested'

    >>> command_anchor_id("blah")
    ''
    """

    return common_command_traverse(result, sep='-')


def command_pos_args(result: dict) -> str:
    """Returns the command up to the positional arg a string
    that is suitable for the text in the command index.

    >>> x, y, z = {}, {}, {}
    >>> x['prog']='simple-command'
    >>> command_pos_args(x)
    'simple-command'

    >>> y['name']='A'
    >>> y['parent']=x
    >>> command_pos_args(y)
    'simple-command A'

    >>> z['name']='zz'
    >>> z['parent']=y
    >>> command_pos_args(z)
    'simple-command A zz'

    >>> command_pos_args("blah")
    ''
    """

    return common_command_traverse(result)


def common_command_traverse(result: dict, sep=' ') -> str:
    id = ""

    if 'name' in result and result['name'] != '':
        id += f"{result['name']}"
    elif 'prog' in result and result['prog'] != '':
        id += f"{result['prog']}"

    if 'parent' in result:
        id = common_command_traverse(result['parent'], sep) + sep + id

    return id


if __name__ == "__main__":
    import doctest

    doctest.testmod()
