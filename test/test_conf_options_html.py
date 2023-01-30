"""Test the HTML builder with sphinx-argparse conf options and check output against XPath."""

import pytest

from .conftest import check_xpath, flat_dict


@pytest.mark.parametrize(
    "fname,expect",
    flat_dict(
        {
            'index.html': [
                (".//h1", 'Sample'),
                (".//h2", 'Sub-commands'),
                (".//h3", 'sample-directive-opts A'),  # By default, just "A".
                (".//h3", 'sample-directive-opts B'),
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
    print(app.outdir / fname)
    check_xpath(cached_etree_parse(app.outdir / fname), fname, *expect)


@pytest.mark.parametrize(
    "fname,expect",
    flat_dict(
        {
            'index.html': [
                (".//h1", 'Sample'),
                (".//section[contains(@class, 'argparse-test')][@id='sample-directive-opts-positional-arguments']", ''),
                (".//section[contains(@class, 'argparse-test')][@id='sample-directive-opts-bar-options']", ''),
                (".//section[contains(@class, 'custom-class')][@id='sample-directive-opts-sub-commands']", ''),
                (".//section[contains(@class, 'argparse-test')][@id='sample-directive-opts-A']", ''),
                (".//section/div[contains(@class, 'argparse-test')]", ''),
                (".//section[contains(@class, 'custom-class')][@id='sample-directive-opts-B']", ''),
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
            "classes": ["argparse-test", "custom-class"],
        }
    },
)
def test_additional_classes_html(app, cached_etree_parse, fname, expect):
    app.build()
    print(app.outdir / fname)
    check_xpath(cached_etree_parse(app.outdir / fname), fname, *expect)
