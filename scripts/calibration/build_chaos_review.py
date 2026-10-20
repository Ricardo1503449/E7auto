"""Build an offline, review-only candidate pack; never modifies runtime resources."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import hashlib
import html
import json
from pathlib import Path

import cv2
import numpy as np
import yaml
from e7auto.core.types import Rect, Size
from e7auto.vision.frames import CoordinateTransform, adapt_frame
from e7auto.vision.matching import TemplateMatcher
from e7auto.resources.templates import TemplateData
from types import SimpleNamespace

from scripts.common.candidates import write_candidate_png
from scripts.common.image_io import read_color_rgba_png, write_png
from scripts.common.paths import PROJECT_ROOT, calibration_output_dirs


def crop_checked(image: np.ndarray, rect: list[int]) -> np.ndarray:
    x, y, w, h = rect
    if min(x, y) < 0 or min(w, h) <= 0 or x+w > image.shape[1] or y+h > image.shape[0]:
        raise ValueError(f"Invalid crop {rect} for {image.shape}")
    return image[y:y+h, x:x+w].copy()


def ink_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Includes pale violet text. A narrow dark outline preserves stroke contrast.
    ink = ((hsv[:, :, 2] > 130) & (hsv[:, :, 1] < 105)).astype(np.uint8)*255
    return cv2.dilate(ink, np.ones((3, 3), np.uint8))


def white_mask(bgr: np.ndarray) -> np.ndarray:
    pixels=bgr.astype(np.int16)
    ink=((pixels.min(2)>175)&(np.ptp(pixels,axis=2)<45)).astype(np.uint8)*255
    return cv2.dilate(ink,np.ones((3,3),np.uint8))


def icon_mask(bgr: np.ndarray) -> np.ndarray:
    """Suppress card tint using same-row border colors, retaining object contrast."""
    width=bgr.shape[1];edge=max(2,round(width*.12))
    border=np.concatenate([bgr[:,:edge],bgr[:,-edge:]],axis=1)
    background=np.median(border.astype(np.float32),axis=1)[:,None,:]
    distance=np.linalg.norm(bgr.astype(np.float32)-background,axis=2)
    mask=(distance>38).astype(np.uint8)*255
    return cv2.dilate(mask,np.ones((3,3),np.uint8))


def glyph_mask(bgr: np.ndarray) -> np.ndarray:
    hsv=cv2.cvtColor(bgr,cv2.COLOR_BGR2HSV)
    light=cv2.cvtColor(bgr,cv2.COLOR_BGR2GRAY)
    contrast=cv2.morphologyEx(light,cv2.MORPH_TOPHAT,np.ones((9,9),np.uint8))
    strokes=((contrast>14)&(hsv[:,:,1]<115)&(hsv[:,:,2]>95)).astype(np.uint8)
    count,labels,stats,_=cv2.connectedComponentsWithStats(strokes)
    keep=np.zeros(count,np.uint8)
    for label,(_,_,width,height,area) in enumerate(stats[1:],1):
        if area>=5 and height>=3 and max(width,height)>=4:keep[label]=255
    return cv2.dilate(keep[labels],np.ones((3,3),np.uint8))


def make_candidate(original: np.ndarray, mode: str, patches: list | None) -> np.ndarray:
    candidate = original.copy()
    h, w = original.shape[:2]
    if mode == "full":
        mask = np.full((h, w), 255, np.uint8)
    elif mode == "ink":
        mask = ink_mask(original[:, :, :3])
    elif mode == "white":
        mask = white_mask(original[:, :, :3])
    elif mode == "regions":
        mask = np.zeros((h, w), np.uint8)
        for x0, y0, x1, y1, kind in patches or []:
            a, b, c, d = round(x0*w), round(y0*h), round(x1*w), round(y1*h)
            if not (0 <= a < c <= w and 0 <= b < d <= h):
                raise ValueError("Mask patch outside template")
            patch=original[b:d,a:c,:3]
            mask[b:d,a:c] = ink_mask(patch) if kind=='ink' else white_mask(patch) if kind=='white' else icon_mask(patch) if kind=='icon' else glyph_mask(patch) if kind=='glyph' else 255
    else:
        raise ValueError(f"Unknown mask mode: {mode}")
    candidate[:, :, 3] = np.minimum(original[:, :, 3], mask)
    if np.count_nonzero(candidate[:, :, 3]) < 20:
        raise ValueError("Candidate mask has too little evidence")
    return candidate


def checker(candidate: np.ndarray) -> np.ndarray:
    h, w = candidate.shape[:2]
    y, x = np.indices((h, w)); board = np.where((x//12+y//12)%2, 65, 100).astype(np.uint8)
    background = np.repeat(board[:, :, None], 3, axis=2)
    a = candidate[:, :, 3:4].astype(np.float32)/255
    return (candidate[:, :, :3]*a+background*(1-a)).astype(np.uint8)


def apply_review_mask(candidate: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Apply an explicitly reviewed silhouette without changing source colors."""
    if mask.ndim != 2 or mask.dtype != np.uint8 or mask.shape != candidate.shape[:2]:
        raise ValueError('Review mask must be uint8 grayscale with exact crop dimensions')
    result = candidate.copy()
    result[:, :, 3] = np.minimum(result[:, :, 3], mask)
    if np.count_nonzero(result[:, :, 3]) < 20:
        raise ValueError('Review mask has too little evidence')
    return result


def match_candidate(frame: np.ndarray, template: np.ndarray, roi: list[int]) -> dict:
    """Use the production native matcher for diagnostic candidate scoring."""
    data=TemplateData(np.ascontiguousarray(template[:,:,:3]),np.ascontiguousarray(template[:,:,3]))
    matcher=TemplateMatcher(SimpleNamespace(get=lambda _:data))
    hits=matcher.locate_all(frame,'candidate',Rect(*roi),0.,.8,limit=1)
    if not hits:
        return dict(score=0.,structure=0.,location=None)
    hit=hits[0];r=hit.bounds
    return dict(score=round(hit.confidence,6),structure=round(hit.structure_confidence,6),
                location=[r.x,r.y,r.width,r.height])


def draw_preview(frame, roi, match, title, action=None, relative=None):
    out=frame[:,:,:3].copy()
    x,y,w,h=roi;cv2.rectangle(out,(x,y),(x+w-1,y+h-1),(80,210,80),4)
    cv2.putText(out,title,(25,130),cv2.FONT_HERSHEY_SIMPLEX,.8,(30,240,240),2)
    if match:
        x,y,w,h=match;cv2.rectangle(out,(x,y),(x+w-1,y+h-1),(240,220,20),3)
        if action:
            px=round(x+action[0]*w);py=round(y+action[1]*h)
            cv2.drawMarker(out,(px,py),(40,40,250),cv2.MARKER_CROSS,26,3)
    if relative:
        x,y,w,h=relative;cv2.rectangle(out,(x,y),(x+w,y+h),(220,80,220),3)
    return cv2.resize(out,(1161,653),interpolation=cv2.INTER_AREA)


@lru_cache(maxsize=24)
def read(path: str) -> np.ndarray:
    return read_color_rgba_png(Path(path))


def build(run_dir: Path, spec_path: Path, candidate_dir: Path | None = None, *, validate=True) -> dict:
    if not (run_dir/'task.json').is_file(): raise ValueError('Expected managed task directory')
    spec=json.loads(spec_path.read_text(encoding='utf-8'))
    sources={s['id']:s for s in json.loads((run_dir/'inputs/source_index.json').read_text(encoding='utf-8'))}
    links=json.loads((run_dir/'inputs/crop_links.json').read_text(encoding='utf-8'))
    candidate_dir,_=calibration_output_dirs(candidate_dir)
    results_dir=run_dir/'results';previews=run_dir/'previews'
    for name in ['templates','masks','regions','raw']:(previews/name).mkdir(exist_ok=True)
    previous_path=results_dir/'review_catalog.json'
    previous={i['key']:i for i in json.loads(previous_path.read_text(encoding='utf-8'))['items']} if previous_path.is_file() else {}
    items=[]
    for s in spec['templates']:
        info=sources[s['source']];original=read(info.get('client',info['local']))
        rect=s['rect'] or [0,0,original.shape[1],original.shape[0]]
        original=crop_checked(original,rect);candidate=make_candidate(original,s['mask'],s.get('patches'))
        if s.get('mask_file'):
            mask_path=run_dir/s['mask_file']
            mask=cv2.imdecode(np.fromfile(mask_path,np.uint8),cv2.IMREAD_UNCHANGED)
            candidate=apply_review_mask(candidate,mask)
        out=Path(info['original']) if s.get('reuse_existing') else candidate_dir/'chaos'/f"{s['key']}.png"
        if not s.get('reuse_existing'):write_candidate_png(out,candidate)
        source_match=None;context=None;notes=list(s['notes'])
        if info['kind']=='full':context=s['source'];source_match=rect
        elif links.get(s['source']) and links[s['source']][0]['score']>=.96:
            link=links[s['source']][0];context=link['source'];source_match=[link['rect'][0]+rect[0],link['rect'][1]+rect[1],rect[2],rect[3]]
        else: notes.append('局部素材缺少可确认的完整原图对应；区域图为搜索范围示意，不代表真实匹配成功。')
        preview_context=context or s.get('preview_source') or (s['positives'][0] if s['positives'] else {'战利品':'loot_set_a','修整与换人':'rank_result','英雄招募与筛选':'all_thief','地图与节点':'map_start'}.get(s['group'],'map_start'))
        roi=spec['rois'][s['roi']]
        item={**s,'candidate':str(out),'size':[candidate.shape[1],candidate.shape[0]],'crop':rect,
              'source_sha256':info['sha256'],'mask_pixels':int(np.count_nonzero(candidate[:,:,3])),
              'context':context,'source_match':source_match,'preview_context':preview_context,'search_roi':roi,'notes':notes,
              'evidence':[],'approval':'pending','registration':'not_prepared'}
        fingerprint={'algorithm':'masked-proposals-v2-adapted-frame','pixels':hashlib.sha256(candidate.tobytes()).hexdigest(),
                     'roi':roi,'structure':item['structure_threshold'],'cases':[(n,sources[n]['sha256']) for n in [context,*s['positives'],*s['negatives']] if n in sources]}
        item['validation_fingerprint']=hashlib.sha256(json.dumps(fingerprint,sort_keys=True).encode()).hexdigest()
        write_png(previews/'raw'/f"{s['key']}.png",original)
        write_png(previews/'templates'/f"{s['key']}.png",checker(candidate))
        write_png(previews/'masks'/f"{s['key']}.png",candidate[:,:,3])
        items.append(item)

    def evaluate(item):
        old=previous.get(item['key'])
        if old and old.get('validation_fingerprint')==item['validation_fingerprint']:
            for field in ['evidence','threshold','status']:item[field]=old[field]
            item['notes']=list(dict.fromkeys(item['notes']+old['notes']))
            item['validation_reused']=True
            return item
        t=read(str(item['candidate']));cases=[]
        src=sources[item['source']]
        if item['context']:
            cases.append((item['context'],'source_self'))
        for name in item['positives']:
            if name!=item['context']:cases.append((name,'real_positive'))
        for name in item['negatives']:cases.append((name,'real_negative'))
        for name,kind in cases:
            if name not in sources or 'client' not in sources[name]:
                item['notes'].append('缺少完整客户区样本：'+name);continue
            result=match_candidate(read(sources[name]['client']),t,item['search_roi'])
            item['evidence'].append({'source':name,'kind':kind,**result})
        # Synthetic whole-client downscale; matching keeps its native pixels.
        if item['context']:
            f=read(sources[item['context']]['client'])
            x,y,w,h=item['search_roi'];actual=cv2.resize(f[:,:,:3],(1536,864),interpolation=cv2.INTER_AREA)
            projected=adapt_frame(actual,CoordinateTransform(Size(2322,1306),Size(1536,864)))
            result=match_candidate(projected,t,item['search_roi'])
            item['evidence'].append({'source':item['context'],'kind':'synthetic_1536x864',**result})
        positives=[e['score'] for e in item['evidence'] if e['kind']!='real_negative' and e['structure']>=.80]
        negatives=[e['score'] for e in item['evidence'] if e['kind']=='real_negative' and e['structure']>=.80]
        if positives and min(positives)>max(negatives or [0])+.03:
            item['threshold']=round(max(.85,min(.97,min(positives)-.015)),3)
        for e in item['evidence']:
            detected=e['score']>=item['threshold'] and e['structure']>=item['structure_threshold']
            e['detected']=detected;e['passed']=not detected if e['kind']=='real_negative' else detected
        item['status']='needs_calibration' if any(not e['passed'] for e in item['evidence']) else 'offline_pass' if item['context'] or any(e['kind']=='real_positive' for e in item['evidence']) else 'crop_only'
        if not any(e['kind']=='real_positive' for e in item['evidence']):item['notes'].append('尚无独立完整画面正例；自匹配不能代替跨场景验证。')
        return item

    if validate:
        cv2.setNumThreads(1)
        with ThreadPoolExecutor(max_workers=3) as pool:
            for i,item in enumerate(pool.map(evaluate,items)):
                print(f"[{i+1}/{len(items)}] {item['key']} {item['status']}",flush=True)
    else:
        for item in items:item['status']='not_validated'
    for item in items:
        if item.get('revision5') and not item['context'] and any(e['kind']=='real_positive' and e.get('passed') for e in item['evidence']):
            item['notes']=[n for n in item['notes'] if n!='局部素材缺少可确认的完整原图对应；区域图为搜索范围示意，不代表真实匹配成功。']
            item['notes'].append('原始局部裁剪未确认与完整截图属于同一帧；已在新增完整截图中验证，区域预览中的匹配框为实际识别结果。')
        source=sources[item['preview_context']];match=item['source_match']
        if not match:
            accepted=[e for e in item['evidence'] if e['source']==item['preview_context'] and e['kind']!='real_negative' and e.get('detected')]
            if accepted:match=accepted[0]['location']
        relative=None
        if item.get('relative_roi'):
            anchor=next(v for v in items if v['key']==item['relative_roi']['anchor'])
            hit=match_candidate(read(source['client']),read(anchor['candidate']),anchor['search_roi'])
            if hit['location'] and hit['score']>=anchor['threshold'] and hit['structure']>=anchor['structure_threshold']:
                ax,ay,aw,ah=hit['location'];dx,dy,rw,rh=item['relative_roi']['offset']
                relative=[round(ax+aw/2+dx),round(ay+ah/2+dy),rw,rh]
        item['preview_relative_roi']=relative
        title=item['key']+(' | source/observed match' if match else ' | ROI ONLY - no full-scene match')
        write_png(previews/'regions'/f"{item['key']}.png",draw_preview(read(source['client']),item['search_roi'],match,title,item.get('action'),relative))
        record={'schema_version':1,'feature':'chaos','key':item['key'],'original':sources[item['source']]['original'],
                'original_sha256':item['source_sha256'],'client_rect':sources[item['source']].get('client_rect'),
                'crop':item['crop'],'editing_basis':'Original BGR pixels preserved; alpha mask only; no raster redraw or template resize.',
                'mask':item['mask'],'patches':item.get('patches'),'validation':item['evidence'],'review_status':'pending',
                'mask_file':item.get('mask_file'),'mask_sha256':hashlib.sha256((run_dir/item['mask_file']).read_bytes()).hexdigest() if item.get('mask_file') else None}
        if not item.get('reuse_existing'):(candidate_dir/f"{item['key']}.source.json").write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    report={'feature':'chaos','baseline':spec['baseline'],'candidate_dir':str(candidate_dir),'items':items,
            'status_counts':{v:sum(i['status']==v for i in items) for v in sorted({i['status'] for i in items})},
            'limitations':['No live game operation or validation.','No formal template registration.','Thresholds are provisional pending user review and more samples.','Coarse proposals are refined at original resolution.']}
    (results_dir/'review_catalog.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    draft={'feature':'chaos','baseline':spec['baseline'],'approved':False,'runtime_enabled':False,'rois':spec['rois'],'templates':{i['key']:{'resource':{'feature':'common' if i.get('reuse_existing') else 'chaos','key':i.get('resource_key',i['key'])},'search_roi':i['roi'],'threshold':i['threshold'],'structure_threshold':i['structure_threshold'],'action':i['action'],'priority':i.get('priority'),'deferred':i.get('deferred',False),'relative_roi':i.get('relative_roi')} for i in items}}
    (results_dir/'recognition_draft.yaml').write_text(yaml.safe_dump(draft,allow_unicode=True,sort_keys=False),encoding='utf-8')
    render_html(run_dir,report,sources)
    print(json.dumps(report['status_counts']),flush=True)
    return report


def render_html(run_dir: Path, report: dict, sources: dict):
    data=json.dumps(report,ensure_ascii=False).replace('</','<\\/')
    source_data=json.dumps(sources,ensure_ascii=False).replace('</','<\\/')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>自动卡厄思 · 候选模板审核</title><style>
*{box-sizing:border-box}body{margin:0;background:#10151e;color:#dde7f5;font:15px/1.65 "Microsoft YaHei",sans-serif}header{padding:28px 36px;background:#182131;border-bottom:1px solid #354357}h1{font-size:26px;margin:0 0 10px}p{margin:8px 0}.muted{color:#9bacc3}main{max-width:1560px;margin:auto;padding:24px}nav{position:sticky;top:0;background:#182131;z-index:3;padding:12px 24px;display:flex;gap:12px;flex-wrap:wrap}select,input,button{background:#253249;color:#e8f1ff;border:1px solid #51637c;padding:9px 12px;border-radius:6px;font:inherit}input{min-width:260px}.bad{color:#ffc180}.ok{color:#83dec2}article{background:#182131;border:1px solid #354357;border-radius:12px;margin:0 0 24px;padding:22px}h2{font-size:20px;margin:0 0 5px}.tag{font-size:12px;display:inline-block;background:#2c3c52;border-radius:4px;padding:2px 8px;margin-right:8px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:16px 0}.box{background:#0b1018;padding:12px;overflow:auto}.box img{display:block;max-width:100%;max-height:290px;margin:auto;object-fit:contain}.region{width:100%;height:auto;display:block;border:1px solid #45546b}a{color:#92c3ff}code{overflow-wrap:anywhere}details{margin-top:14px}table{width:100%;border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid #354357;padding:7px;text-align:left}summary{cursor:pointer;color:#a9cef9}.legend{display:flex;gap:20px;flex-wrap:wrap}.note{background:#263149;padding:10px 14px;border-left:3px solid #e5b260}#count{align-self:center}@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:12px}header{padding:20px}.box img{max-height:220px}}
</style><header><h1>自动卡厄思 · 候选模板审核包</h1><p>仅离线制作 · 尚未批准 · 未登记正式资源 · 未实现自动化流程</p><p class="muted">原始截图先裁出 2322 × 1306 游戏客户区。候选保留原始像素，透明遮罩排除非识别内容。区域坐标以客户区左上角为原点。</p><div class="legend"><span style="color:#70d070">绿色：运行搜索区域</span><span style="color:#40dbef">青色：来源裁剪／已匹配框</span><span style="color:#ff6666">红色：点击锚点</span><span style="color:#d779d7">紫色：相对锚点搜索示例</span></div><p class="note">ROI示意不代表识别已通过。只有局部素材的项目、离线未通过项、缺独立正例项均明确标注。人工缩放验证不等于真实分辨率实测。未修改正式PNG或运行配置。</p><p><a href="../results/review_catalog.json">完整识别结果 JSON</a> · <a href="../results/recognition_draft.yaml">搜索区域与阈值草案 YAML</a> · <a href="../inputs/source_index.json">162份素材索引</a> · <a href="motion.html">自动战斗录屏分析</a> · <a href="sources.html">全部素材及客户区裁剪</a></p></header>
<nav><input id="q" placeholder="按名称、编号、用途搜索"><select id="group"><option value="">全部分组</option></select><select id="status"><option value="">全部状态</option><option value="needs_calibration">需要校准</option><option value="crop_only">仅局部样本</option><option value="offline_pass">现有离线用例通过</option></select><span id="count"></span></nav><main id="items"></main>
<script>const report=__DATA__, sources=__SOURCES__;const labels={offline_pass:'现有离线用例通过（待审核）',needs_calibration:'需校准／补样本',crop_only:'仅局部素材，缺完整对应',not_validated:'尚未验证'};const kinds={source_self:'来源自匹配',real_positive:'其他真实正例',real_negative:'真实反例',synthetic_1536x864:'人工缩放1536×864'};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const groups=[...new Set(report.items.map(i=>i.group))];for(const g of groups)document.querySelector('#group').insertAdjacentHTML('beforeend',`<option>${esc(g)}</option>`);
function render(){const q=document.querySelector('#q').value.toLowerCase(),g=document.querySelector('#group').value,st=document.querySelector('#status').value;const arr=report.items.filter(i=>(!g||g===i.group)&&(!st||st===i.status)&&(!q||JSON.stringify([i.name,i.key,i.group]).toLowerCase().includes(q)));document.querySelector('#count').textContent=`显示 ${arr.length} / ${report.items.length} 项`;document.querySelector('#items').innerHTML=arr.map(i=>`<article><span class="tag">${esc(i.group)}</span>${i.priority!==undefined?`<span class="tag">T${i.priority}</span>`:''}${i.deferred?'<span class="tag">阶段切换暂不启用</span>':''}${i.role==='negative_reference'?'<span class="tag">灰态反例参考</span>':''}<h2>${esc(i.name)}</h2><code>${esc(i.key)}</code><p class="${i.status==='offline_pass'?'ok':'bad'}">${labels[i.status]}</p><p class="muted">尺寸 ${i.size.join(' × ')} · 有效遮罩 ${i.mask_pixels} px · 候选阈值 ${i.threshold} · 结构门槛 ${i.structure_threshold} · 搜索范围 [${i.search_roi.join(', ')}]</p>${i.choice?`<p>选择：${esc(i.choice)}</p>`:''}<div class="grid"><div class="box">原始裁剪<img loading="lazy" src="raw/${i.key}.png"></div><div class="box">候选／透明区域预览<img loading="lazy" src="templates/${i.key}.png"></div><div class="box">遮罩：白色参与匹配<img loading="lazy" src="masks/${i.key}.png"></div></div><p>搜索区域预览：${esc(i.preview_context)}${!i.context?'（原始局部图没有可靠完整画面对应）':''}</p><a href="regions/${i.key}.png"><img class="region" loading="lazy" src="regions/${i.key}.png"></a><p>来源：${esc(i.source)} · <a href="../inputs/${i.source}.png">查看原素材</a>${sources[i.source].kind==='full'?` · <a href="clients/${i.source}.png">原图客户区边界</a>`:''}</p>${i.notes.map(n=>`<p class="note">${esc(n)}</p>`).join('')}<details><summary>查看验证明细（${i.evidence.length}项）</summary><table><tr><th>类别</th><th>素材</th><th>颜色分数</th><th>结构分数</th><th>结果</th></tr>${i.evidence.map(e=>`<tr><td>${kinds[e.kind]||e.kind}</td><td>${esc(e.source)}</td><td>${e.score}</td><td>${e.structure}</td><td class="${e.passed?'ok':'bad'}">${e.passed?'通过':'未通过'}</td></tr>`).join('')}</table></details></article>`).join('')};for(const el of document.querySelectorAll('nav input,nav select'))el.addEventListener('input',render);render();</script></html>'''
    counts=report['status_counts']
    summary=f'<p><strong>{len(report["items"])}项审核条目：</strong>{counts.get("offline_pass",0)}项现有完整画面用例通过；{counts.get("crop_only",0)}项仅局部／缺完整正例；{counts.get("needs_calibration",0)}项需校准。通过也不代表实机验收。</p><p><a href="cross_validation.html">11种战利品交叉验证</a> · <a href="summary.html">覆盖范围与待审核事项</a></p>'
    page=page.replace('</header>',summary+'</header>')
    page=page.replace('162份素材索引',f'{sum(not s.get("id", "").startswith("network_") for s in sources.values())}份素材索引')
    page=page.replace('${labels[i.status]}',"${i.approval==='approved'?'用户已审核通过':labels[i.status]}")
    approved_count=sum(i.get('approval')=='approved' for i in report['items'])
    if approved_count:
        page=page.replace('仅离线制作 · 尚未批准',f'仅离线制作 · 全包已审核通过 {approved_count} / {len(report["items"])} 项')
    if any(i.get('resource_key',i['key'])!=i['key'] for i in report['items']):
        page=page.replace('</header>','<p><a href="button_reuse.html">三组按钮复用及场景映射</a></p></header>')
        page=page.replace('<code>${esc(i.key)}</code>','<code>${esc(i.key)}</code>${i.resource_key&&i.resource_key!==i.key?`<p class="note">该场景已复用资源 ${esc(i.resource_key)}；下方保留原候选审核历史，实际资源及匹配预览见<a href="button_reuse.html">按钮复用页面</a>。</p>`:""}')
    if report.get('registration_summary',{}).get('status')=='complete':
        page=page.replace('未登记正式资源','已正式登记130份专用资源，复用2份公共网络资源')
        page=page.replace('未修改正式PNG或运行配置。','登记仅落实已批准资源与来源记录，运行流程尚未启用。')
        page=page.replace('</header>','<p><a href="registration.html">逐项登记结果、计划与事务ID</a></p></header>')
    # Explicit version keys keep locally cached previews from showing earlier alpha masks.
    page=page.replace('${i.key}.png','${i.key}.png?v=${i.validation_fingerprint}')
    if any(i.get('revision16') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision16.html">查看两个事件模板与全宽识别区域</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision16')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 两个事件模板复审').replace('<a href="revision16.html">查看两个事件模板与全宽识别区域</a>','<a href="index.html">返回全部模板</a> · <a href="revision16-candidates.png">PNG总览</a> · <a href="revision16-validation.html">区域调整及验证结果</a>')
        (run_dir/'previews/revision16.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision15') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision15.html">查看阶段边框右侧残留修订</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision15')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 阶段边框右侧清理复审').replace('<a href="revision15.html">查看阶段边框右侧残留修订</a>','<a href="index.html">返回全部模板</a> · <a href="revision15-detail.png">右侧放大前后对照</a> · <a href="revision15-candidate.png">完整PNG预览</a>')
        (run_dir/'previews/revision15.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision14') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision14.html">查看难度、阶段边框与文字图标6项复审</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision14')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 6项图标边框及文字复审').replace('<a href="revision14.html">查看难度、阶段边框与文字图标6项复审</a>','<a href="index.html">返回全部模板</a> · <a href="revision14-candidates.png">PNG总览</a> · <a href="revision14-validation.html">局部与阶段遮挡验证</a>')
        (run_dir/'previews/revision14.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision13') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision13.html">查看左右两侧战利品选中边框</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision13')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 左右两侧选中边框复审').replace('<a href="revision13.html">查看左右两侧战利品选中边框</a>','<a href="index.html">返回全部模板</a> · <a href="revision13-validation.html">验证与PNG对照</a>')
        (run_dir/'previews/revision13.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision12') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision12.html">查看统一战利品选中边框</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision12')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 统一战利品选中边框复审').replace('<a href="revision12.html">查看统一战利品选中边框</a>','<a href="index.html">返回全部模板</a> · <a href="revision12-validation.html">普通／紫色验证与PNG对照</a>')
        (run_dir/'previews/revision12.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision11') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision11.html">查看文本三角、11种战利品与选择按钮</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision11')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 13项文本推进与战利品复审').replace('<a href="revision11.html">查看文本三角、11种战利品与选择按钮</a>','<a href="index.html">返回全部模板</a> · <a href="revision11-candidates-1.png">PNG总览1</a> · <a href="revision11-candidates-2.png">PNG总览2</a> · <a href="revision11-candidates-3.png">PNG总览3</a> · <a href="cross_validation.html">战利品交叉验证</a>')
        (run_dir/'previews/revision11.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision10') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision10.html">查看购买商品信息与金额遮罩修订</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision10')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 购买商品信息与金额遮罩复审').replace('<a href="revision10.html">查看购买商品信息与金额遮罩修订</a>','<a href="index.html">返回全部模板</a> · <a href="revision10-candidates.png">本轮PNG总览</a> · <a href="../results/revision10_validation.json">金额变化验证</a>')
        (run_dir/'previews/revision10.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision9') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision9.html">查看替换图标、补给与商店9项</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision9')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 替换图标、补给与商店复审').replace('<a href="revision9.html">查看替换图标、补给与商店9项</a>','<a href="index.html">返回全部模板</a> · <a href="revision9-candidates.png">本轮PNG总览</a> · <a href="../results/revision9_validation.json">验证结果</a>')
        (run_dir/'previews/revision9.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision8') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision8.html">查看候补名称与红色减号3项</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision8')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 候补名称与红色减号复审').replace('<a href="revision8.html">查看候补名称与红色减号3项</a>','<a href="index.html">返回全部模板</a> · <a href="revision8-candidates.png">本轮PNG总览</a> · <a href="../results/revision8_validation.json">验证结果</a>')
        (run_dir/'previews/revision8.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision7') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision7.html">查看4个升阶模板与修整离开按钮</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision7')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 5项升阶与离开模板复审').replace('<a href="revision7.html">查看4个升阶模板与修整离开按钮</a>','<a href="index.html">返回全部模板</a> · <a href="revision7-candidates.png">本轮PNG总览</a> · <a href="../results/revision7_validation.json">升阶交叉验证</a>')
        (run_dir/'previews/revision7.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision6') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision6.html">查看本轮10项按钮与结算文字</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision6')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 10项按钮与结算文字复审').replace('<a href="revision6.html">查看本轮10项按钮与结算文字</a>','<a href="index.html">返回全部模板</a> · <a href="revision6-candidates.png">本轮PNG总览</a>')
        (run_dir/'previews/revision6.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision5') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision5.html">查看商店／补给修订及4种灰态对照</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision5')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 商店／补给与灰态对照').replace('<a href="revision5.html">查看商店／补给修订及4种灰态对照</a>','<a href="revision4.html">返回14项节点审核</a> · <a href="revision5-candidates.png">本轮总览</a> · <a href="revision5-shop-comparison.png">商店右下角对比（左旧／右新）</a>')
        (run_dir/'previews/revision5.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision4') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision4.html">查看14个节点模板修订</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision4')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 14个节点模板复审').replace('<a href="revision4.html">查看14个节点模板修订</a>','<a href="index.html">返回全部模板</a> · <a href="revision4-candidates-1.png">点亮节点总览</a> · <a href="revision4-candidates-2.png">灰态图标总览</a> · <a href="../results/revision4_validation.json">节点交叉验证结果</a>')
        (run_dir/'previews/revision4.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision3') for i in report['items']):
        page=page.replace('</header>',f'<p><a href="revision3.html">查看本轮3个名称模板</a> · 当前累计{approved_count}项已审核通过。</p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision3')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 3个名称模板复审').replace('<a href="revision3.html">查看本轮3个名称模板</a>','<a href="revision1.html">返回35项审核列表</a>')
        revised=revised.replace('<p>搜索区域预览：', '${i.revision3?`<p>末尾文字放大对照：上方为上一版，下方为本次。</p><img style="max-width:100%" src="revision3-tail-${i.key}.png?v=${i.validation_fingerprint}">`:""}<p>搜索区域预览：')
        (run_dir/'previews/revision3.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision2') for i in report['items']):
        page=page.replace('</header>','<p><a href="revision2.html">仅查看本轮再次修正的8项</a> · 上一批其余27项已审核通过。</p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision2')]}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 本轮修正8项').replace('<a href="revision2.html">仅查看本轮再次修正的8项</a>','<a href="revision1.html">返回上一批35项</a>')
        (run_dir/'previews/revision2.html').write_text(revised,encoding='utf-8')
    if any(i.get('revision')=='revision1' for i in report['items']):
        page=page.replace('</header>','<p><a href="revision1.html">仅查看本轮重做的35项</a></p></header>')
        subset={**report,'items':[i for i in report['items'] if i.get('revision')=='revision1']}
        subset_data=json.dumps(subset,ensure_ascii=False).replace('</','<\\/')
        revised=page.replace('__DATA__',subset_data).replace('__SOURCES__',source_data).replace('自动卡厄思 · 候选模板审核包','自动卡厄思 · 本轮重做35项').replace('<a href="revision1.html">仅查看本轮重做的35项</a>','<a href="index.html">返回全部模板</a>')
        (run_dir/'previews/revision1.html').write_text(revised,encoding='utf-8')
    (run_dir/'previews/index.html').write_text(page.replace('__DATA__',data).replace('__SOURCES__',source_data),encoding='utf-8')
    rows=[]
    for s in sources.values():
        alias=s['id'];suffix=Path(s['local']).suffix
        rows.append(f'<article><h2>{html.escape(alias)}</h2><p>{html.escape(s["original"])}</p><p>SHA256: {s["sha256"]}</p><a href="../inputs/{alias}{suffix}">原始素材</a>'+(f'<p>客户区：{s["client_rect"]}</p><img loading="lazy" style="max-width:100%" src="clients/{alias}.png">' if s.get('client_rect') else (f'<img loading="lazy" style="max-width:600px" src="../inputs/{alias}.png">' if suffix=='.png' else ''))+'</article>')
    (run_dir/'previews/sources.html').write_text('<meta charset="utf-8"><title>素材裁剪审核</title><style>body{background:#172030;color:#eee;font:15px Microsoft YaHei;padding:25px}article{padding:25px;border-bottom:1px solid #456}a{color:#9cf}</style><h1>原始素材与客户区边界</h1>'+''.join(rows),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--spec',type=Path)
    parser.add_argument('--candidate-dir',type=Path)
    parser.add_argument('--skip-validation',action='store_true')
    args=parser.parse_args()
    build(args.run_dir,args.spec or args.run_dir/'inputs/template_spec.json',args.candidate_dir,validate=not args.skip_validation)


if __name__=='__main__':main()
