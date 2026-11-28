#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mem0 生产可用性独立探针 —— 零项目依赖，只用 mem0 + openai + 标准库。

复刻 check-manage 记忆模块（server/utils/memory.py）的真实请求面：
  - POST {base}/chat/completions   LLM（事实抽取 + 记忆更新决策，每次 infer add 共 2 次）
  - POST {base}/embeddings         嵌入（add 每次至少 2 批 / search 每次 1 次）
  - 本地 Chroma 向量库（临时目录，绝不碰生产真实记忆库 ~/.check-manage/mem0）

在生产机上、用后端同一个 Python 环境运行：
    python mem0_availability_probe.py \
        --endpoint https://gw.example.com/v1/chat/completions \
        --api-key sk-xxx \
        --model qwen-max \
        --embedding-model text-embedding-v3

    --endpoint 应与后端 ai_settings.endpoint 完全一致（完整 chat/completions URL，
    探针按同样规则剥出 base_url）。无 timeout 透传（mem0 配置类不支持），
    前置直连检查自带 30s 快失败超时；mem0 步骤沿用 openai 客户端默认超时。

步骤：
  1. 直连 POST /embeddings（失败自动去掉 dimensions 参数重试，鉴别「后端拒参数」）
  2. 直连 POST /chat/completions
  3. mem0 Memory.from_config 初始化（本地临时 chroma）
  4. add(infer=True)  模拟会话抽取写入（LLM×2 + embeddings）
  5. add(infer=False) 原样写入（仅 embeddings，对应手动补写路径）
  6. search 回读：必须命中步骤 4 埋入的指纹串
  7. get_all + delete：全生命周期收尾
  8. 清理临时存储（--keep 保留供排查）

退出码：全部关键步骤通过 = 0，否则 1。
附注：首次 add 冷启动 ~13s（嵌入端点建联）属正常；posthog 遥测已按 MEM0_TELEMETRY=False 关闭。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import uuid

os.environ.setdefault('MEM0_TELEMETRY', 'False')  # 必须在 import mem0 之前关遥测

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PROBE_USER = 'mem0-probe-user'


def step(name):
    print(f'\n== {name}')


def elapsed(t0):
    return f'{time.time() - t0:.1f}s'


def fail_hints(msg):
    """按报错特征给出定向排查提示。"""
    m = str(msg)
    hints = []
    if '404' in m or 'Not Found' in m:
        hints.append('网关未实现该端点（常见：网关只代理 /chat/completions，没有 /embeddings）')
    if 'dimensions' in m:
        hints.append('后端拒绝 dimensions 参数（vLLM 等非 matryoshka 嵌入后端不支持；'
                     '换支持自定义维度的模型，如 DashScope text-embedding-v3）')
    if 'encoding_format' in m:
        hints.append('后端拒绝 encoding_format 参数')
    if '401' in m or '403' in m or 'Unauthorized' in m or 'Forbidden' in m:
        hints.append('鉴权失败：检查 api-key 与网关的模型授权')
    if 'model' in m.lower() and ('not' in m.lower() or 'invalid' in m.lower() or '不存在' in m):
        hints.append('模型名不被网关支持：核对 --model / --embedding-model')
    if any(k in m.lower() for k in ('connect', 'timed out', 'timeout', 'unreachable', 'getaddrinfo')):
        hints.append('网络不通：检查 DNS / 出网代理 / 防火墙（内网环境重点排查代理白名单）')
    if 'dimension' in m and ('mismatch' in m.lower() or 'does not' in m.lower()):
        hints.append('向量集合维度与本次 --dims 不一致（旧存储残留）：清理 --store-dir 或对应集合后重试')
    for h in hints:
        print(f'   提示: {h}')
    if not hints:
        print('   提示: 无匹配的已知模式，附完整报错给网关方核对')


def base_url(endpoint):
    return endpoint.rsplit('/chat/completions', 1)[0] if endpoint else ''


def unwrap(res):
    if isinstance(res, dict):
        return res.get('results', [])
    return res or []


def build_mem0_config(args, store_dir):
    base = base_url(args.endpoint)
    provider = args.embedder_provider
    if provider == 'ollama':
        embedder = {'provider': 'ollama',
                    'config': {'model': args.embedding_model,
                               'ollama_base_url': args.ollama_url,
                               'embedding_dims': args.dims}}
    else:
        embedder = {'provider': 'openai',
                    'config': {'model': args.embedding_model,
                               'openai_base_url': base, 'api_key': args.api_key,
                               'embedding_dims': args.dims}}
    return {
        'vector_store': {'provider': 'chroma',
                         'config': {'collection_name': 'probe', 'path': store_dir}},
        'llm': {'provider': 'openai',
                'config': {'model': args.model, 'openai_base_url': base,
                           'api_key': args.api_key, 'temperature': 0.1}},
        'embedder': embedder,
    }


def main():
    ap = argparse.ArgumentParser(description='mem0 生产可用性独立探针（零项目依赖）',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument('--endpoint', required=True,
                    help='完整 chat/completions URL，与后端 ai_settings.endpoint 一致')
    ap.add_argument('--api-key', required=True)
    ap.add_argument('--model', default='qwen-max', help='主对话模型（LLM 抽取/决策用）')
    ap.add_argument('--embedding-model', default='text-embedding-v3')
    ap.add_argument('--dims', type=int, default=1024, help='嵌入维度（须与后端 embeddingDims 一致）')
    ap.add_argument('--embedder-provider', choices=['api', 'ollama'], default='api',
                    help='api=openai 兼容嵌入端点；ollama=本地 Ollama 服务'
                         '（注意：mem0 2.0.7 的 ollama 嵌入通道需要真实本地 Ollama）')
    ap.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    ap.add_argument('--store-dir', default=None,
                    help='chroma 存储目录（默认用一次性临时目录，不碰生产记忆库）')
    ap.add_argument('--keep', action='store_true', help='保留临时存储目录供排查')
    args = ap.parse_args()

    from openai import OpenAI  # mem0 的既有依赖，非项目依赖

    base = base_url(args.endpoint)
    if not base:
        print('[FAIL] endpoint 必须是完整 chat/completions URL，无法推导 base_url')
        return 1
    store_dir = args.store_dir or tempfile.mkdtemp(prefix='mem0-probe-')
    fingerprint = f'PROBE-{uuid.uuid4().hex[:8].upper()}'

    print('== 探针配置')
    print(f'   base_url        = {base}')
    print(f'   model           = {args.model}')
    print(f'   embedding       = {args.embedder_provider}/{args.embedding_model} dims={args.dims}')
    print(f'   store_dir       = {store_dir}')
    print(f'   fingerprint     = {fingerprint}')
    if args.embedder_provider == 'api' and not base.endswith('/v1'):
        print(f'   [WARN] base_url 不以 /v1 结尾：确认网关嵌入路径是否为 {base}/embeddings')

    critical_ok = True
    t_all = time.time()

    # ── 1. 直连 /embeddings（快失败；dimensions 被拒时自动降参数重试鉴别）──
    step('1/7 直连 POST /embeddings')
    t0 = time.time()
    try:
        client = OpenAI(api_key=args.api_key, base_url=base, timeout=30, max_retries=0)
        client.embeddings.create(model=args.embedding_model, input=['ping'],
                                 dimensions=args.dims, encoding_format='float')
        print(f'   [PASS] 带 dimensions={args.dims} 请求成功 ({elapsed(t0)})')
    except Exception as e:
        print(f'   [FAIL] {type(e).__name__}: {str(e)[:400]} ({elapsed(t0)})')
        fail_hints(e)
        print('   -> 改试不带 dimensions 参数（鉴别「端点不可用」还是「仅拒参数」）')
        t1 = time.time()
        try:
            client = OpenAI(api_key=args.api_key, base_url=base, timeout=30, max_retries=0)
            client.embeddings.create(model=args.embedding_model, input=['ping'],
                                     encoding_format='float')
            print(f'   [WARN] 不带 dimensions 可用：后端拒 dimensions 参数，'
                  f'mem0 固定会传 dimensions -> 记忆模块在该网关不可用')
            critical_ok = False
        except Exception as e2:
            print(f'   [FAIL] 不带 dimensions 也失败: {type(e2).__name__}: {str(e2)[:300]}')
            fail_hints(e2)
            critical_ok = False

    # ── 2. 直连 /chat/completions ──
    step('2/7 直连 POST /chat/completions')
    t0 = time.time()
    try:
        client = OpenAI(api_key=args.api_key, base_url=base, timeout=30, max_retries=0)
        client.chat.completions.create(model=args.model, temperature=0.1, max_tokens=16,
                                       messages=[{'role': 'user', 'content': '回复 OK'}])
        print(f'   [PASS] ({elapsed(t0)})')
    except Exception as e:
        print(f'   [FAIL] {type(e).__name__}: {str(e)[:400]} ({elapsed(t0)})')
        fail_hints(e)
        critical_ok = False

    # ── 3. mem0 初始化（本地临时 chroma，不发模型请求）──
    step('3/7 mem0 Memory 初始化（本地临时 chroma）')
    t0 = time.time()
    try:
        from mem0 import Memory
        mem = Memory.from_config(build_mem0_config(args, store_dir))
        print(f'   [PASS] ({elapsed(t0)})；首次 add 冷启动 ~13s 属正常')
    except Exception as e:
        print(f'   [FAIL] {type(e).__name__}: {str(e)[:500]}')
        traceback.print_exc(limit=3)
        fail_hints(e)
        print('== 结论: mem0 初始化失败，后续步骤跳过')
        return 1

    # ── 4. add(infer=True)：模拟会话抽取写入 ──
    step('4/7 add(infer=True) 会话抽取写入')
    fact = f'本项目的生产服务器编号是 {fingerprint}，部署在华东机房。'
    t0 = time.time()
    try:
        res = mem.add([{'role': 'user', 'content': fact},
                       {'role': 'assistant', 'content': '已了解，为你记住该部署信息。'}],
                      user_id=PROBE_USER, metadata={'source': 'probe'})
        # add 返回体与 search 同形：{"results": [{id, memory, event}, ...]}
        events = unwrap(res)
        summary = [(r.get('event'), str(r.get('memory', ''))[:60]) for r in events]
        print(f'   耗时 {elapsed(t0)}，抽取事件: {json.dumps(summary, ensure_ascii=False)[:300]}')
        if not events:
            print('   [WARN] 模型未抽出任何记忆（抽取 prompt/模型能力问题），继续用回读验证')
        else:
            print('   [PASS]')
    except Exception as e:
        print(f'   [FAIL] {type(e).__name__}: {str(e)[:500]} ({elapsed(t0)})')
        fail_hints(e)
        critical_ok = False

    # ── 5. add(infer=False)：原样写入（手动补写路径，仅 embeddings）──
    step('5/7 add(infer=False) 原样写入')
    t0 = time.time()
    try:
        mem.add([{'role': 'user', 'content': f'探针直写备忘 {fingerprint}'}],
                user_id=PROBE_USER, infer=False, metadata={'source': 'probe'})
        print(f'   [PASS] ({elapsed(t0)})')
    except Exception as e:
        print(f'   [FAIL] {type(e).__name__}: {str(e)[:500]} ({elapsed(t0)})')
        fail_hints(e)
        critical_ok = False

    # ── 6. search 回读指纹 ──
    step('6/7 search 回读（存储→检索闭环）')
    t0 = time.time()
    found = False
    try:
        res = mem.search(query='生产服务器编号是多少', filters={'user_id': PROBE_USER}, limit=5)
        results = unwrap(res)
        texts = [str(r.get('memory', '')) for r in results]
        print(f'   耗时 {elapsed(t0)}，返回 {len(texts)} 条')
        for t in texts:
            print(f'   - {t[:120]}')
        found = any(fingerprint in t for t in texts)
        print(f'   [{"PASS" if found else "FAIL"}] 指纹 {fingerprint} '
              f'{"命中" if found else "未命中（写入或检索链路有问题）"}')
        if not found:
            critical_ok = False
    except Exception as e:
        print(f'   [FAIL] {type(e).__name__}: {str(e)[:500]} ({elapsed(t0)})')
        fail_hints(e)
        critical_ok = False

    # ── 7. get_all + delete 收尾 ──
    step('7/7 get_all + delete 生命周期')
    t0 = time.time()
    try:
        items = unwrap(mem.get_all(filters={'user_id': PROBE_USER}))
        print(f'   get_all: {len(items)} 条 ({elapsed(t0)})')
        # 指纹条目可能有两条（infer 抽取 + verbatim 直写），全部删除后再验证
        targets = [r['id'] for r in items if fingerprint in str(r.get('memory', ''))]
        if targets:
            for tid in targets:
                mem.delete(memory_id=tid)
            left = unwrap(mem.get_all(filters={'user_id': PROBE_USER}))
            ok = all(fingerprint not in str(r.get('memory', '')) for r in left)
            print(f'   [{"PASS" if ok else "FAIL"}] delete {len(targets)} 条后指纹条目已全部移除')
            if not ok:
                critical_ok = False
        else:
            print('   [WARN] 未找到指纹条目可删（若步骤 4/5 已 FAIL 属连带，不另计失败）')
    except Exception as e:
        print(f'   [FAIL] {type(e).__name__}: {str(e)[:500]}')
        fail_hints(e)
        critical_ok = False

    # ── 清理 ──
    if args.keep or args.store_dir:
        print(f'\n== 存储目录保留: {store_dir}（维度不匹配时可检查/清空后重试）')
    else:
        shutil.rmtree(store_dir, ignore_errors=True)
        print('\n== 临时存储已清理')

    print('\n' + '=' * 60)
    print(f'总耗时 {elapsed(t_all)} -> {"PASS：mem0 在该环境可用" if critical_ok else "FAIL：见上方各步骤诊断"}')
    return 0 if critical_ok else 1


if __name__ == '__main__':
    sys.exit(main())
