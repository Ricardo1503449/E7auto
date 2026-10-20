import pytest

from e7auto.core.reporting import ShopRunReport
from e7auto.configuration.reporting import load_reporting_endpoint


@pytest.mark.parametrize('values', [
    {'run_id':'short'},{'strategy':'unknown'},{'skystone_spent':True},
    {'covenant_purchases':-1},{'mystic_purchases':1.5},{'skystone_spent':2**31},
])
def test_invalid_report_fields_rejected(values):
    data = dict(run_id='a'*32,strategy='transfer')
    data.update(values)
    with pytest.raises(ValueError):
        ShopRunReport(**data)


@pytest.mark.parametrize('value', ['', 'http://server.example', 'https://name:password@server.example',
                                   'https://server.example/path', 'https://server.example?token=x',
                                   'https://server.example bad'])
def test_optional_endpoint_never_allows_insecure_or_embedded_credentials(tmp_path,value):
    path = tmp_path/'config.yaml'
    path.write_text(f'statistics:\n  api_base_url: "{value}"\n',encoding='utf-8')
    assert load_reporting_endpoint(path) == ''


def test_reporting_config_is_independent_of_game_configuration(tmp_path):
    path = tmp_path/'config.yaml'
    path.write_text('statistics:\n  api_base_url: https://real.example/\ngame: broken\n',encoding='utf-8')
    assert load_reporting_endpoint(path) == 'https://real.example'
    path.write_text('[invalid yaml',encoding='utf-8')
    assert load_reporting_endpoint(path) == ''
