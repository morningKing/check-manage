# -*- coding: utf-8 -*-
"""OpenCode 图片 part 透传 spike（2026-10-30 第一档 Feature C 闸门）。

对 pinned oc serve 实测两种图片 part 形态，判定模型是否真的"看到"图：
  dataurl  —— {"type":"file","mime":"image/png","url":"data:image/png;base64,..."}
  filepath —— {"type":"file","mime":"image/png","url":"file://<workspace 内绝对路径>"}
用法：
  python spike.py --base-url http://127.0.0.1:4096 --token <oc-serve.log 里插件 token 行> \
                  --model <providerID/modelID 必须是视觉模型> --workspace <会话工作区绝对路径> \
                  --image probe.png --mode dataurl
退出码 0=该形态可用；非 0=不可用。结果 JSON 落 docs/ai-testing/evidence/。
"""
import argparse, base64, json, os, sys, time
import requests


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base-url', required=True)
    ap.add_argument('--token', required=True)
    ap.add_argument('--model', required=True)   # providerID/modelID
    ap.add_argument('--workspace', required=True)
    ap.add_argument('--image', required=True)
    ap.add_argument('--mode', choices=['dataurl', 'filepath'], required=True)
    args = ap.parse_args()

    sess = requests.post(f"{args.base_url}/session",
                         params={'directory': args.workspace},
                         headers={'Authorization': f'Bearer {args.token}'},
                         json={'title': 'image-parts-spike'}).json()
    sid = sess['id']
    if args.mode == 'dataurl':
        with open(args.image, 'rb') as f:
            url = 'data:image/png;base64,' + base64.b64encode(f.read()).decode()
    else:
        url = 'file://' + os.path.abspath(args.image)
    provider, model = args.model.split('/', 1)
    requests.post(f"{args.base_url}/session/{sid}/prompt_async",
                  params={'directory': args.workspace},
                  headers={'Authorization': f'Bearer {args.token}'},
                  json={'model': {'providerID': provider, 'modelID': model},
                        'parts': [
                            {'type': 'text',
                             'text': '用不超过五个字描述这张图片的主要内容。'},
                            {'type': 'file', 'mime': 'image/png', 'url': url},
                        ]})
    texts, deadline = [], time.time() + 120
    while time.time() < deadline:
        time.sleep(3)
        msgs = requests.get(f"{args.base_url}/session/{sid}/message",
                            params={'directory': args.workspace},
                            headers={'Authorization': f'Bearer {args.token}'}).json()
        for m in msgs if isinstance(msgs, list) else msgs.get('data', []):
            info = m.get('info') or m
            if info.get('role') == 'assistant':
                # oc serve 1.26 消息信封：parts 在消息顶层（info 旁），不在 info 内；
                # 两种形态都兼容，否则永远读不到 assistant 回复。
                for p in (m.get('parts') or info.get('parts') or []):
                    if p.get('type') == 'text' and p.get('text'):
                        texts.append(p['text'])
        if texts:
            break
    ok = any('红' in t or 'red' in t.lower() for t in texts)
    out = {'mode': args.mode, 'model': args.model, 'ok': ok,
           'assistant_texts': texts[:3], 'session': sid}
    ev = os.path.join('docs', 'ai-testing', 'evidence',
                      f"image-parts-spike-{time.strftime('%Y%m%d-%H%M%S')}-{args.mode}.json")
    os.makedirs(os.path.dirname(ev), exist_ok=True)
    with open(ev, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if ok else 2)


if __name__ == '__main__':
    main()
