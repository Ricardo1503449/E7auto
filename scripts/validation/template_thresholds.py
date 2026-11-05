"""Export every template use and effective recognition gate for explicit review."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

import cv2
import numpy as np
import yaml

from e7auto.configuration.loader import load_config
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.chaos.lineups import LINEUPS
from scripts.common.paths import PROJECT_ROOT, ensure_task_result_path, task_result_path

COLOR = '1 − TM_SQDIFF_NORMED'
STRUCTURE = 'TM_CCOEFF_NORMED'


def build_inventory(root: Path, before_root: Path) -> dict:
    def read(base, name):
        return yaml.safe_load((base / 'config' / (name+'.yaml')).read_text(encoding='utf-8'))
    current, old = read(root, 'internal'), read(before_root, 'internal')
    chaos, old_chaos = read(root, 'chaos'), read(before_root, 'chaos')
    catalog = {}
    for feature in ('common', 'shop', 'penguin', 'chaos'):
        manifest = root / 'assets/templates' / feature / 'manifest.json'
        for key, item in json.loads(manifest.read_text(encoding='utf-8'))['templates'].items():
            catalog[feature, key] = (manifest.parent / item['file']).relative_to(root).as_posix()
    base = load_config(root / 'config/internal.yaml', template_profile='chaos')
    active = {}
    for lineup in LINEUPS:
        for stage in range(1, 6):
            for scene in with_chaos_config(base, lineup_id=lineup.key, target_stage=stage).chaos.scenes:
                active.setdefault(scene, set()).add(lineup.label)
    old_correlation = 'if correlation:' in (before_root / 'src/e7auto/vision/baseline.py').read_text(encoding='utf-8')
    old_full_penguin = 'self.control(frame, "penguin_buy_5100")' in (
        before_root / 'src/e7auto/features/penguin/vision.py').read_text(encoding='utf-8')
    maskless = set()
    for file in set(catalog.values()):
        image = cv2.imdecode(np.frombuffer((root/file).read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError('Cannot read inventory template: '+file)
        if image.shape[2] == 3 or np.all(image[:, :, 3] == 255):
            maskless.add(file)
    rows = []

    def add(feature, use, resource_feature, key, before_color, color, before_structure, structure,
            score_source, structure_source='', *, route='match', status='启用', note='', formula=None):
        file = catalog[resource_feature, key]
        ordinary = formula is None
        before_formula = ('TM_CCOEFF_NORMED（旧主分数）' if old_correlation and file in maskless
                          and route == 'match' else COLOR) if ordinary else formula
        runtime_key = ('chaos:'+key if resource_feature == 'chaos' else
                       'penguin_'+key if resource_feature == 'penguin' else key)
        rows.append(dict(feature=feature, use=use, key=key, runtime_key=runtime_key, file=file, status=status,
                         formula_before=before_formula, formula_current=COLOR if ordinary else formula,
                         structure_formula=STRUCTURE if ordinary else '不适用',
                         metric='颜色差异相似度' if ordinary else ('字形相似度' if '数字字形' in (formula or '') else '不适用'),
                         score_before=before_color, score_current=color,
                         structure_before=before_structure, structure_current=structure,
                         score_source=score_source, structure_source=structure_source,
                         note=note, user_score=None, user_structure=None))

    def single(feature, use, resource_feature, key, parameter, *, note=''):
        def color_value(config):
            vision = config['vision']
            independent = key+'_confidence'
            if resource_feature == 'shop' and key in ('purchased_button', 'confirm_button') and independent in vision:
                return vision[independent], independent
            if resource_feature == 'shop' and parameter == 'default_confidence' and 'shop_default_confidence' in vision:
                return vision['shop_default_confidence'], 'shop_default_confidence'
            runtime_key = 'penguin_'+key
            if resource_feature == 'penguin' and runtime_key in vision.get('penguin_control_thresholds', {}):
                return vision['penguin_control_thresholds'][runtime_key], 'penguin_control_thresholds.'+runtime_key
            return vision[parameter], parameter

        before_color, _ = color_value(old)
        color, color_source = color_value(current)
        old_structure = old['vision'].get('template_structure_thresholds', {}).get(key)
        structure = current['vision'].get('template_structure_thresholds', {}).get(key)
        add(feature, use, resource_feature, key, before_color, color,
            old_structure, structure, 'config/internal.yaml:vision.'+color_source,
            'config/internal.yaml:vision.template_structure_thresholds.'+key,
            note=note or ('双门槛已启用；真实样本覆盖及数值确认见验证记录' if structure is not None
                          else '保留原数值；缺少完整真实正反例，结构门槛待样本'))

    for feature, key in (('shop', 'main_shop_icon'), ('penguin', 'sanctuary_entry')):
        a, b = old['vision']['entry_thresholds'][feature], current['vision']['entry_thresholds'][feature]
        add(feature, '主界面入口', feature, key, a['color'], b['color'], a['structure'], b['structure'],
            f'config/internal.yaml:vision.entry_thresholds.{feature}.color',
            f'config/internal.yaml:vision.entry_thresholds.{feature}.structure', note='沿用已有双门槛')
    for feature in ('shop', 'penguin', 'chaos'):
        for key, value in current['startup_navigation'].items():
            previous = old['startup_navigation'][key]
            add(feature, '启动导航/'+key, 'common', key, previous['threshold'], value['threshold'],
                previous['structure_threshold'], value['structure_threshold'],
                'config/internal.yaml:startup_navigation.'+key+'.threshold',
                'config/internal.yaml:startup_navigation.'+key+'.structure_threshold', note='三功能共享同一参数')
        if feature != 'chaos':
            for key in ('network_connection_abnormal', 'network_retry'):
                single(feature, '网络/'+key, 'common', key, 'default_confidence')
    for key in ('shop_refresh_button', 'shop_exit_icon', 'refresh_confirm_button', 'confirm_button',
                'insufficient_funds', 'purchased_button', 'sky_stone_icon'):
        structure_state = ('结构未启用' if current['vision'].get('template_structure_thresholds', {}).get(key) is None
                           else '结构已启用')
        note = ('原模板保留；颜色独立配置，真实正反例验证见记录；'+structure_state
                if key == 'confirm_button' and 'confirm_button_confidence' in current['vision'] else '')
        single('shop', '普通控件/'+key, 'shop', key, 'anchor_confidence', note=note)
    single('shop', '刷新确认提示', 'shop', 'refresh_confirm_prompt', 'default_confidence')
    for target in current['targets']:
        for field, purpose in (('template', '商品识别'), ('purchased_template', '商品已购识别'),
                               ('confirm_template', '购买弹窗身份')):
            single('shop', target['display_name']+'/'+purpose, 'shop', target[field], 'default_confidence')
        single('shop', target['display_name']+'/购买后同槽位确认', 'shop', target['purchased_template'], 'default_confidence')
        key = target['template']
        structure = current['vision'].get('template_structure_thresholds', {}).get(key)
        add('shop', target['display_name']+'/奖励浮层检测', 'shop', key,
            old['inventory_readiness']['reward_overlay_confidence'],
            current['inventory_readiness']['reward_overlay_confidence'],
            old['vision'].get('template_structure_thresholds', {}).get(key), structure,
            'config/internal.yaml:inventory_readiness.reward_overlay_confidence',
            'config/internal.yaml:vision.template_structure_thresholds.'+key,
            note='与商品扫描用途不同；同一PNG单列；样本覆盖及数值确认见验证记录')
    for feature, key in catalog:
        if feature == 'penguin' and key != 'sanctuary_entry':
            if key == 'penguin_buy_5100':
                runtime_key = 'penguin_'+key
                previous_color = old['vision'].get('penguin_control_thresholds', {}).get(
                    runtime_key, old['vision']['penguin_control_confidence']) if old_full_penguin else None
                previous_structure = old['vision'].get('template_structure_thresholds', {}).get(runtime_key) if old_full_penguin else None
                add('penguin', '控件/'+key, 'penguin', key, previous_color, None, previous_structure, None,
                    '', status='用途已移除', note='完整5100模板复核已移除；PNG仍供排除金额的按钮主体定位使用')
                rows[-1].update(formula_before=COLOR if old_full_penguin else '不再运行',
                                formula_current='不再运行', structure_formula='不适用', metric='不适用')
            else:
                single('penguin', '控件/'+key, 'penguin', key, 'penguin_control_confidence')
    single('penguin', '购买弹窗定位（排除可变金额）', 'penguin', 'penguin_buy_5100', 'penguin_control_confidence',
           note='派生运行遮罩排除金额；当前截图独立读取金额，完整5100模板不再复核')
    body_row = rows[-1]
    if 'penguin_purchase_body_confidence' in old['vision']:
        body_row['score_before'] = old['vision']['penguin_purchase_body_confidence']
    if 'penguin_purchase_body_confidence' in current['vision']:
        body_row['score_current'] = current['vision']['penguin_purchase_body_confidence']
        body_row['score_source'] = 'config/internal.yaml:vision.penguin_purchase_body_confidence'
    # Runtime penguin keys have a feature prefix; manifest names do not.
    for row in rows:
        if (row['feature'] == 'penguin' and row['file'].startswith('assets/templates/penguin/')
                and row['use'] != '主界面入口' and row['status'] != '用途已移除'):
            runtime_key = 'penguin_'+row['key']
            row['structure_before'] = old['vision'].get('template_structure_thresholds', {}).get(runtime_key)
            row['structure_current'] = current['vision'].get('template_structure_thresholds', {}).get(runtime_key)
            row['structure_source'] = 'config/internal.yaml:vision.template_structure_thresholds.'+runtime_key
            row['note'] = ('双门槛已启用；真实样本覆盖及数值确认见验证记录' if row['structure_current'] is not None
                           else '原数值保留；结构门槛待真实样本') + ('；运行遮罩排除金额，金额从当前截图单独读取' if '排除' in row['use'] else '')
    for key in sorted(k for feature, k in catalog if feature == 'common' and k.startswith('sky_stone_digit_')):
        for feature, confidence, margin in (('shop', 'sky_stone_digit_confidence', 'sky_stone_digit_margin'),
                                           ('penguin', 'penguin_price_digit_confidence', 'penguin_price_digit_margin')):
            add(feature, '数字字形/'+key, 'common', key, old['vision'][confidence], current['vision'][confidence],
                None, None, 'config/internal.yaml:vision.'+confidence, route='special', formula='数字字形专用相似度',
                note=f'另需候选差值 vision.{margin}={current["vision"][margin]}；不使用普通颜色/结构分数')
    for key, value in current['daily_reset']['templates'].items():
        previous = old['daily_reset']['templates'][key]
        add('common', '跨日恢复/'+key, 'common', key, previous['threshold'], value['threshold'],
            previous['structure_threshold'], value['structure_threshold'],
            'config/internal.yaml:daily_reset.templates.'+key+'.threshold',
            'config/internal.yaml:daily_reset.templates.'+key+'.structure_threshold', route='locate_all',
            status='卡厄思显式启用', note='保持现有整体/分部匹配、三帧及关闭守卫')
    loot = chaos['loot_recognition']
    for scene, value in chaos['templates'].items():
        previous = old_chaos['templates'][scene]
        resource = value['resource']
        status = '启用（'+','.join(sorted(active[scene]))+'）' if scene in active else '配置保留，当前阵容未启用'
        source = 'config/chaos.yaml:templates.'+scene
        if scene == 'auto_static':
            add('chaos', scene+'/自动战斗姿态字形', resource['feature'], resource['key'], None, None, None, None,
                'config/chaos.yaml:auto_motion', formula='中心/圆环/角度专用识别', status=status,
                note=f'模板场景声明{value["threshold"]}/{value["structure_threshold"]}未作为姿态通过门槛；实际参数见专用表')
        elif scene in loot['template_regions']:
            for part in ('icon', 'name'):
                add('chaos', scene+'/'+('图标' if part == 'icon' else '完整名称'), resource['feature'], resource['key'],
                    old_chaos['loot_recognition'][part+'_threshold'], loot[part+'_threshold'],
                    old_chaos['loot_recognition'][part+'_structure'], loot[part+'_structure'],
                    'config/chaos.yaml:loot_recognition.'+part+'_threshold',
                    'config/chaos.yaml:loot_recognition.'+part+'_structure', route='locate_all', status=status,
                    note='图标和全名共同通过；仅使用战利品分部门槛，无整图门槛；说明文字不参与身份')
        else:
            note = '使用场景独立配置的门槛；按所选阵容/阶段加载'
            if scene == 'text_continue':
                note += '；另有亮背景几何兜底，见专用表'
            add('chaos', scene, resource['feature'], resource['key'], previous['threshold'], value['threshold'],
                previous['structure_threshold'], value['structure_threshold'], source+'.threshold',
                source+'.structure_threshold', route='locate_all', status=status, note=note)
    used = {row['file'] for row in rows}
    for (feature, key), file in catalog.items():
        if file not in used:
            add(feature, '保留资源/'+key, feature, key, None, None, None, None, '', formula='无运行用途绑定',
                status='未启用/历史保留', note='无实际门槛；不把模板自匹配分数填作运行参数')
    parameters = []
    def walk(group, current_values, previous_values, source):
        for key, value in current_values.items():
            if key in ('templates', 'template_regions', 'layouts', 'lobby_scenes', 'component_matching'):
                continue
            previous = previous_values.get(key) if isinstance(previous_values, dict) else None
            if isinstance(value, dict):
                walk(group, value, previous or {}, source+'.'+str(key))
            elif type(value) in (int, float) and re.search('score|threshold|margin|tolerance|min|max|frames|support|contrast|angle|foreground|spread|extra', str(key)):
                parameters.append(dict(group=group, source=source+'.'+str(key), before=previous,
                                       current=value, user_value=None))
    for key in ('auto_motion', 'text_triangle', 'event_recognition', 'loot_recognition', 'stage_selection', 'geometry'):
        walk('卡厄思/'+key, chaos[key], old_chaos[key], 'config/chaos.yaml:'+key)
    for key in ('vision', 'inventory_readiness', 'scroll', 'timing'):
        values = {k:v for k,v in current[key].items() if not isinstance(v, dict)}
        walk('共享/商店/'+key, values, old[key], 'config/internal.yaml:'+key)
    for i, row in enumerate(rows, 1):
        row['id'] = f'T{i:03d}'
    actual_files = {p.relative_to(root).as_posix() for p in (root/'assets/templates').rglob('*.png')}
    missing = actual_files - {row['file'] for row in rows}
    if missing:
        raise ValueError('Uncatalogued PNGs require explicit inventory classification: '+repr(sorted(missing)))
    return dict(root=str(root.resolve()), rows=rows, specialized_parameters=parameters, png_count=len(actual_files),
                maskless_files=sorted(maskless), counts=dict(Counter(row['feature'] for row in rows)))


def markdown(report: dict) -> str:
    def text(value):
        if value is None:
            return '—'
        return str(value).replace('|', '\\|').replace('\n', ' ')
    lines = ['# 全量识别门槛逐项确认清单', '',
             '这是本轮实现后的配置快照，不自动应用用户填写值。空白确认栏表示尚未确认，不能视为同意当前值。',
             '数值为算法门槛，不是正确率。公式变更前的相关性主分数与变更后的色差分数不能直接换算。',
             '“—”表示该用途不适用或尚未启用该门槛，原因见备注；数字字形行的主门槛是专用字形分数。',
             '同一PNG的不同用途分别列出；共用配置位置的行会同时受参数调整影响。人工缩放样本不等于独立尺寸实机采集。',
             '可点击文件名查看模板；按编号提供主门槛/结构门槛即可。待样本项先保留当前值，不要求现在确认。',
             f'覆盖正式PNG {report["png_count"]} 张，逐用途条目 {len(report["rows"])} 条。', '']
    for feature, title in (('common', '共享与跨日'), ('shop', '商店'), ('penguin', '企鹅'), ('chaos', '卡厄思')):
        lines += ['## '+title, '',
                  '| 编号 | 用途 / 模板键 | 文件 | 状态 | 评分：改前 → 当前 | 主门槛：改前 → 当前 | 结构：改前 → 当前 | 配置位置（主 / 结构） | 验证与条件 | 用户确认主门槛 | 用户确认结构 |',
                  '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
        for row in report['rows']:
            if row['feature'] != feature:
                continue
            target = (Path(report['root'])/row['file']).as_posix()
            file_link = '['+row['file']+'](<'+target+'>)'
            key_label = row['key'] if row['key'] == row['runtime_key'] else row['key']+'（运行键 '+row['runtime_key']+'）'
            lines.append('| '+' | '.join(map(text, [row['id'], row['use']+' / '+key_label, file_link,
                row['status'], row['formula_before']+' → '+row['formula_current'],
                f'{text(row["score_before"])} → {text(row["score_current"])}',
                f'{text(row["structure_before"])} → {text(row["structure_current"])}',
                row['score_source']+' / '+row['structure_source'], row['note'], '', '']))+' |')
        lines.append('')
    lines += ['## 专用识别及其他判定门槛（保留原规则）', '',
              '本表包含候选差距、就绪、几何和稳定确认条件，不把这些数值伪装成颜色/结构分数。', '',
              '| 分组 | 配置位置 | 改造前 | 当前 | 用户确认新值 |', '| --- | --- | --- | --- | --- |']
    for row in report['specialized_parameters']:
        lines.append('| '+' | '.join(map(text, [row['group'], row['source'], row['before'], row['current'], '']))+' |')
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-root', type=Path, required=True, help='Read-only pre-change source/config snapshot')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = ensure_task_result_path(args.output) if args.output else task_result_path(
        'template-threshold-inventory', 'common', 'thresholds.md')
    if output.exists() or output.with_suffix('.json').exists():
        raise FileExistsError('Preserve existing review decisions; choose a new inventory output path')
    report = build_inventory(PROJECT_ROOT, args.before_root)
    output.write_text(markdown(report), encoding='utf-8')
    output.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows', 'specialized_parameters')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
