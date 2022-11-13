import pytest

from .conftest import check_xpath, flat_dict


@pytest.mark.parametrize(
    "fname,expect",
    flat_dict(
        {
            'subcommand-a.html': [
                (".//h1", 'Sample', False),
                (".//h1", 'Command A'),
            ],
            'subcommand-b.html': [
                (".//h1", 'Sample', False),
                (".//h1", 'Command B'),
            ],
            'commands-index.html': [
                (".//h1", 'Commands Index'),
                (".//tr/td[2]/a/code", 'sample-directive-opts'),
                (".//tr/td[3]/em", 'Support SphinxArgParse HTML testing'),
                (".//tr[td[2]/a/code/text()='sample-directive-opts']/td[3]/em", 'Support SphinxArgParse HTML testing'),
            ],
        }
    ),
)
@pytest.mark.sphinx('html', testroot='command-index')
def test_commands_index_html(app, cached_etree_parse, fname, expect):
    app.build()
    check_xpath(cached_etree_parse(app.outdir / fname), fname, *expect)
