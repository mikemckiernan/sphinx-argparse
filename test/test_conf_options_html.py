"""Test the HTML builder with sphinx-argparse conf options and check output against XPath."""

import pytest

from .conftest import check_xpath, flat_dict


@pytest.mark.parametrize(
    "fname,expect",
    flat_dict(
        {
            'index.html': [
                (".//h1", 'Sample'),
                (".//h2", 'Positional Arguments'),
                (".//h2", 'Named Arguments'),
                (".//h2", 'bar options'),
                (".//h2", 'bla options'),
                (".//h3", '^sample-directive-opts A'),  # By default, just "A".
                (".//h3", '^sample-directive-opts B'),
            ],
        }
    ),
)
@pytest.mark.sphinx(
    'html',
    testroot='conf-opts-html',
    freshenv=True,
    confoverrides={
        'sphinx_argparse_conf': {
            "full_subcommand_name": True,
        }
    },
)
def test_full_subcomand_name_html(app, cached_etree_parse, fname, expect):
    app.build()
    check_xpath(cached_etree_parse(app.outdir / fname), fname, *expect)


@pytest.mark.parametrize(
    "fname,expect",
    flat_dict(
        {
            'index.html': [
                (".//h1", 'Sample'),
                (".//h2", 'Positional Arguments', False),
                (".//h2", 'Named Arguments', False),
                (".//h2", 'bar options', False),
                (".//h2", 'bla options', False),
                (".//h2", 'Sub-commands'),
                (".//h3", '^A'),
                (".//h3", '^B'),
            ],
        }
    ),
)
@pytest.mark.sphinx(
    'html',
    testroot='conf-opts-html',
    freshenv=True,
    confoverrides={
        'sphinx_argparse_conf': {
            "subcommands_only": True,
        }
    },
)
def test_subcomands_only_html(app, cached_etree_parse, fname, expect):
    app.build()
    check_xpath(cached_etree_parse(app.outdir / fname), fname, *expect)
