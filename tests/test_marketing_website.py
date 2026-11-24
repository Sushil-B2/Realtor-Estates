"""Imported marketing site is isolated from CRM and private configuration."""
from urllib.request import urlopen
from urllib.error import HTTPError
import pytest
from conftest import BASE


@pytest.mark.parametrize('path', ['/website/', '/website/projects/samruddhi-estate-phase-3', '/website/privacy-policy'])
def test_imported_website_routes(path):
    with urlopen(BASE + path, timeout=10) as res:
        assert res.status == 200
        html = res.read().decode()
        assert 'id="root"' in html
        assert '/website/assets/' in html
        assert "script-src 'self'" in res.headers['Content-Security-Policy']


def test_public_build_excludes_configuration_artifact():
    path = '/website/assets/aistudio/drizzle.config_cc445020.json'
    with pytest.raises(HTTPError) as error:
        urlopen(BASE + path, timeout=10)
    assert error.value.code == 404


def test_crm_root_is_preserved():
    with urlopen(BASE + '/', timeout=10) as res:
        html = res.read().decode()
        assert '/js/core.js' in html
        assert '/js/views-growth.js' in html
