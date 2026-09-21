"""Read-only model discovery through the local runner, with bounded network time."""
import re
from urllib.parse import urlsplit, urlunsplit
import httpx

def model_endpoint(base,auth='bearer'):
    if auth not in ('bearer','anthropic','gemini','none'):raise ValueError('模型列表鉴权方式无效')
    raw=str(base).strip()
    try:
        url=urlsplit(raw)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or re.search(r'[\x00-\x20\\]',raw):raise ValueError()
        _=url.port
    except ValueError:raise ValueError('渠道地址必须是完整 HTTP(S) 地址，不能带账号、查询参数、片段或空白字符。')
    path=re.sub(r'/(?:v\d+(?:beta)?|api/v\d+)$','',url.path.rstrip('/'))
    path+=('/v1beta' if auth=='gemini' else '/v1')+'/models'
    return urlunsplit((url.scheme,url.netloc,path,'',''))

def fetch_models(base,key,auth='bearer',transport=None):
    endpoint=model_endpoint(base,auth)
    if not isinstance(key,str) or (auth!='none' and not key.strip()) or any(c in key for c in ('\n','\r')):raise ValueError('请输入有效密钥，或选择不使用鉴权')
    headers={}
    if auth=='bearer':headers={'Authorization':'Bearer '+key}
    elif auth=='anthropic':headers={'x-api-key':key,'anthropic-version':'2023-06-01'}
    elif auth=='gemini':headers={'x-goog-api-key':key}
    with httpx.Client(timeout=25,follow_redirects=False,trust_env=False,transport=transport) as client:
        r=client.get(endpoint,headers=headers)
        if not r.is_success:
            raise ValueError('获取模型列表失败：HTTP '+str(r.status_code)+'。请检查地址、密钥权限和鉴权方式。')
        try:data=r.json()
        except ValueError:raise ValueError('模型列表接口没有返回 JSON')
        if isinstance(data,dict):
            if data.get('error') is not None:raise ValueError('模型列表接口返回错误；仍可手动填写模型 ID。')
            if 'data' in data:rows=data['data']
            elif 'models' in data:rows=data['models']
            else:raise ValueError('模型列表返回结构无法识别；仍可手动填写模型 ID。')
        else:rows=data
        if not isinstance(rows,list):raise ValueError('模型列表返回结构无法识别；仍可手动填写模型 ID。')
        ids=set()
        for row in rows:
            candidates=(row.get('id'),row.get('name')) if isinstance(row,dict) else (row,)
            value=next((v.strip() for v in candidates if isinstance(v,str) and v.strip()),None)
            if value is not None:
                if auth=='gemini':value=re.sub(r'^models/','',value)
                if value:ids.add(value)
        if rows and not ids:raise ValueError('模型列表没有可识别的模型 ID；仍可手动填写。')
        ids=sorted(ids)
        return {'models':ids,'total':len(ids)}
