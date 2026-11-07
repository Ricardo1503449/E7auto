import json
from pathlib import Path
import pytest
from scripts.common.paths import create_artifact_run,managed_task_directory
from scripts.cloudflare.shop_stats.configure import configure,endpoint_base

def test_task_staging_cannot_use_unmanaged_or_other_feature_output(tmp_path):
    task = create_artifact_run('tasks','cloudflare-tests',feature='shop',root=tmp_path)
    assert managed_task_directory(task,feature='shop',root=tmp_path) == task
    with pytest.raises(ValueError): managed_task_directory(task,feature='penguin',root=tmp_path)
    with pytest.raises(ValueError): managed_task_directory(tmp_path,feature='shop',root=tmp_path)


def test_deployment_inputs_change_only_their_owned_blocks_and_preserve_game_bytes(tmp_path):
    (tmp_path/'config').mkdir()
    config = tmp_path/'config/internal.yaml'
    original = b'game:\r\n  baseline: original\r\nstatistics:\r\n  # note\r\n  api_base_url: ""\r\ngeometry:\r\n  value: untouched\r\n'
    config.write_bytes(original)
    readme = tmp_path/'README.md'
    readme.write_text('Original introduction\nOriginal footer\n',encoding='utf-8')
    path = tmp_path/'scripts/cloudflare/shop_stats/wrangler.jsonc'
    path.parent.mkdir(parents=True)
    path.write_text('{"database_id": "", "name": "e7auto-shop-stats"}',encoding='utf-8')
    configure(database_id='a'*8+'-'+'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12,
              endpoint='https://service.example/',root=tmp_path)
    assert config.read_bytes() == original.replace(b'api_base_url: ""',b'api_base_url: "https://service.example"')
    value = readme.read_text(encoding='utf-8')
    assert value.startswith('Original introduction\n') and value.endswith('Original footer\n')
    assert value == 'Original introduction\nOriginal footer\n'


def test_ingest_configuration_does_not_require_or_recreate_public_readme_links(tmp_path):
    (tmp_path/'config').mkdir()
    config=tmp_path/'config/internal.yaml'
    config.write_text('statistics:\n  api_base_url: ""\n',encoding='utf-8')
    configure(endpoint='https://service.example',root=tmp_path)
    assert 'https://service.example' in config.read_text(encoding='utf-8')
    assert not (tmp_path/'README.md').exists()


def test_node_test_extension_is_limited_to_registered_cloudflare_category(tmp_path):
    from scripts.project.check_layout import check_layout
    root = Path(__file__).resolve().parents[3]
    policy = json.loads((root/'scripts/project/layout.json').read_text(encoding='utf-8'))
    source = tmp_path/'scripts/project/layout.json'; source.parent.mkdir(parents=True)
    source.write_text(json.dumps(policy),encoding='utf-8')
    allowed = tmp_path/'tests/scripts/cloudflare/test_worker.mjs'; allowed.parent.mkdir(parents=True)
    allowed.write_text('// worker test',encoding='utf-8')
    wrong = tmp_path/'tests/ui/test_worker.mjs'; wrong.parent.mkdir(parents=True)
    wrong.write_text('// misplaced worker test',encoding='utf-8')
    problems = check_layout(tmp_path)
    assert not any('tests/scripts/cloudflare/test_worker.mjs' in error for error in problems)
    assert any('tests/ui/test_worker.mjs' in error for error in problems)


@pytest.mark.parametrize('endpoint',['https://service.example"bad','https://service.example\n','https://service.example\\bad'])
def test_endpoint_rejects_characters_that_could_break_yaml(endpoint):
    with pytest.raises(ValueError): endpoint_base(endpoint)
