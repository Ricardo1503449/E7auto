from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

import pytest

from scripts.cloudflare.shop_stats import snapshot,publish
from scripts.common.paths import create_artifact_run,managed_task_directory
from scripts.cloudflare.shop_stats.configure import configure,START,END


def aggregate():
    first = {'skystone_spent':330,'covenant_purchases':4,'mystic_purchases':1,
             'covenant_per_100':400/330,'mystic_per_100':100/330}
    return {'pools':{'transfer':first,'continuous':{'skystone_spent':0,'covenant_purchases':2,
            'mystic_purchases':0,'covenant_per_100':None,'mystic_per_100':None}},
            'updated_at':'2026-10-03T00:00:00.000Z'}


def test_snapshot_json_and_svg_use_one_aggregate_and_publish_only_expected_files(tmp_path):
    node = os.environ.get('SHOP_STATS_NODE') or shutil.which('node')
    if not node:
        pytest.skip('Node.js needed to render SVG')
    files = snapshot.build_files(aggregate(),'https://service.example',node=node,day='2026-10-03')
    assert '1.21' in files['latest.svg'] and '0.30' in files['latest.svg']
    assert json.loads(files['latest.json']) == aggregate()
    assert files['latest.svg'] == files['history/2026-10-03.svg']
    snapshot.write_files(tmp_path,files)
    assert publish.read_files(tmp_path) == files


@pytest.mark.parametrize('change',['missing_pool','negative','boolean','wrong_rate','zero_rate','extra_field','bad_timestamp'])
def test_invalid_aggregate_is_not_accepted(change):
    value = aggregate()
    if change == 'missing_pool': del value['pools']['continuous']
    elif change == 'negative': value['pools']['transfer']['skystone_spent'] = -1
    elif change == 'boolean': value['pools']['transfer']['covenant_purchases'] = True
    elif change == 'wrong_rate': value['pools']['transfer']['covenant_per_100'] = 2.16
    elif change == 'zero_rate': value['pools']['continuous']['covenant_per_100'] = 0
    elif change == 'extra_field': value['runs'] = []
    else: value['updated_at'] = 'bad'
    with pytest.raises(ValueError): snapshot.validate_stats(value)


def test_failed_fetch_leaves_last_snapshot_untouched(tmp_path,monkeypatch):
    output = tmp_path/'latest.json'; output.write_text('previous',encoding='utf-8')
    def failure(endpoint): raise OSError('unreachable')
    monkeypatch.setattr(snapshot,'fetch_stats',failure)
    monkeypatch.setattr('sys.argv',['snapshot','--endpoint','https://service.example','--output-dir',str(tmp_path)])
    with pytest.raises(OSError): snapshot.main()
    assert output.read_text(encoding='utf-8') == 'previous'


def test_publication_manifest_cannot_include_other_sources(tmp_path):
    (tmp_path/'snapshot-files.json').write_text(json.dumps(['../README.md']*5),encoding='utf-8')
    with pytest.raises(ValueError): publish.read_files(tmp_path)


@pytest.mark.parametrize('existing',[False,True])
def test_publication_uses_stats_ref_and_preserves_prior_history(existing):
    calls = []
    def request(method,path,body=None,**kwargs):
        calls.append((method,path,body))
        if method == 'GET' and path == '/git/ref/heads/stats':
            if len(calls) == 1: return {'object':{'sha':'old'}} if existing else None
            return {'object':{'sha':'new'}}
        if path == '/git/commits/old': return {'tree':{'sha':'old-tree'}}
        if path == '/git/trees': return {'sha':'new-tree'}
        if path == '/git/commits': return {'sha':'new'}
        return {}
    files = {'latest.json':'{}','latest.svg':'svg','README.md':'index',
             'history/2026-10-03.json':'{}','history/2026-10-03.svg':'svg'}
    assert publish.publish(request,files) == 'new'
    tree = next(body for method,path,body in calls if path == '/git/trees')
    assert tree.get('base_tree') == ('old-tree' if existing else None)
    assert {item['path'] for item in tree['tree']} == set(files)
    assert not any('main' in path or 'master' in path for _,path,_ in calls)
    update = next(body for method,path,body in calls if method == 'PATCH') if existing else None
    if existing: assert update['force'] is False


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
    readme.write_text(f'Original introduction\n{START}\nconfigure service\n{END}\nOriginal footer\n',encoding='utf-8')
    path = tmp_path/'scripts/cloudflare/shop_stats/wrangler.jsonc'
    path.parent.mkdir(parents=True)
    path.write_text('{"database_id": "", "name": "e7auto-shop-stats"}',encoding='utf-8')
    configure(database_id='a'*8+'-'+'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12,
              endpoint='https://service.example/',root=tmp_path)
    assert config.read_bytes() == original.replace(b'api_base_url: ""',b'api_base_url: "https://service.example"')
    value = readme.read_text(encoding='utf-8')
    assert value.startswith('Original introduction\n') and value.endswith('Original footer\n')
    assert 'https://service.example/stats.svg' in value and 'https://service.example/stats)' in value


def test_invalid_or_missing_readme_marker_does_not_partially_rewrite_configuration(tmp_path):
    (tmp_path/'config').mkdir()
    config = tmp_path/'config/internal.yaml'
    original = 'statistics:\n  api_base_url: ""\n'
    config.write_text(original,encoding='utf-8')
    (tmp_path/'README.md').write_text('No marker',encoding='utf-8')
    with pytest.raises(ValueError): configure(endpoint='https://service.example',root=tmp_path)
    assert config.read_text(encoding='utf-8') == original


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
    with pytest.raises(ValueError): snapshot.endpoint_base(endpoint)
