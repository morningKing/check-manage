"""AI 批任务对外 API 集成示例 — 进阶篇

演示 5 个新端点的完整用法：
  1. 获取子会话对话历史        GET  /sessions/{childId}/messages
  2. 获取子会话文件列表        GET  /sessions/{childId}/files
  3. 下载单个文件              GET  /sessions/{childId}/files/download
  4. 打包下载所有产出文件      GET  /sessions/{childId}/files/download-all
  5. 在子会话上继续对话        POST /sessions/{childId}/continue

前置条件：
  - 已有一个绑定用户的 API Key（未绑定的存量密钥会返回 403）
  - 已安装 requests：pip install requests

运行：
  python batch_api_advanced.py --api-key cm_xxx --base-url http://localhost:8080/api/v1/ai-batches
"""

import argparse
import json
import os
import sys
import time

import requests


class BatchClient:
    """AI 批任务 API 客户端封装。"""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.headers = {
            'X-API-Key': api_key,
            'Content-Type': 'application/json',
        }

    def _url(self, *parts) -> str:
        return '/'.join([self.base_url] + [str(p) for p in parts])

    def _get(self, *parts, **kwargs) -> dict:
        resp = requests.get(self._url(*parts), headers=self.headers, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, *parts, json_body=None, **kwargs) -> requests.Response:
        resp = requests.post(self._url(*parts), headers=self.headers,
                             json=json_body, **kwargs)
        resp.raise_for_status()
        return resp

    def _delete(self, *parts) -> dict:
        resp = requests.delete(self._url(*parts), headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # 4.1 上传文件
    # ------------------------------------------------------------------
    def upload_files(self, file_paths: list[str]) -> list[dict]:
        """上传文件到暂存区，返回 files 数组。"""
        files = [('files', (os.path.basename(p), open(p, 'rb'))) for p in file_paths]
        try:
            resp = requests.post(
                self._url('uploads'),
                headers={'X-API-Key': self.headers['X-API-Key']},
                files=files,
            )
            resp.raise_for_status()
            return resp.json()['files']
        finally:
            for _, (_, f) in files:
                f.close()

    # ------------------------------------------------------------------
    # 4.2 创建批任务
    # ------------------------------------------------------------------
    def create_batch(self, name: str, prompt: str, files: list[dict],
                     agent: str = '', model: str = '') -> dict:
        body = {'name': name, 'prompt': prompt, 'files': files}
        if agent:
            body['agent'] = agent
        if model:
            body['model'] = model
        resp = self._post(json_body=body)
        return resp.json()

    # ------------------------------------------------------------------
    # 4.4 查询批任务详情
    # ------------------------------------------------------------------
    def get_batch(self, batch_id: str) -> dict:
        return self._get(batch_id)

    # ------------------------------------------------------------------
    # 4.5 取回处理结果
    # ------------------------------------------------------------------
    def get_results(self, batch_id: str) -> dict:
        return self._get(batch_id, 'results')

    # ------------------------------------------------------------------
    # 4.6 删除批任务
    # ------------------------------------------------------------------
    def delete_batch(self, batch_id: str) -> dict:
        return self._delete(batch_id)

    # ------------------------------------------------------------------
    # 4.11 获取子会话对话历史
    # ------------------------------------------------------------------
    def get_session_messages(self, batch_id: str, child_id: str) -> dict:
        """获取子会话完整对话。child_id 为文件名或序号。"""
        return self._get(batch_id, 'sessions', child_id, 'messages')

    # ------------------------------------------------------------------
    # 4.12 获取子会话文件列表
    # ------------------------------------------------------------------
    def get_session_files(self, batch_id: str, child_id: str) -> dict:
        """实时扫描子会话工作区，返回文件列表。"""
        return self._get(batch_id, 'sessions', child_id, 'files')

    # ------------------------------------------------------------------
    # 4.13 下载子会话单个文件
    # ------------------------------------------------------------------
    def download_session_file(self, batch_id: str, child_id: str,
                              path: str, output_dir: str = '.') -> str:
        """下载单个文件到指定目录，返回本地文件路径。"""
        resp = requests.get(
            self._url(batch_id, 'sessions', child_id, 'files', 'download'),
            headers=self.headers,
            params={'path': path},
            stream=True,
        )
        resp.raise_for_status()
        filename = os.path.basename(path)
        local_path = os.path.join(output_dir, filename)
        with open(local_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return local_path

    # ------------------------------------------------------------------
    # 4.14 打包下载子会话产出文件
    # ------------------------------------------------------------------
    def download_session_files_zip(self, batch_id: str, child_id: str,
                                   output_dir: str = '.') -> str:
        """打包下载所有新增/修改文件，返回本地 ZIP 路径。"""
        resp = requests.get(
            self._url(batch_id, 'sessions', child_id, 'files', 'download-all'),
            headers=self.headers,
            stream=True,
        )
        resp.raise_for_status()
        # 从 Content-Disposition 提取文件名
        cd = resp.headers.get('Content-Disposition', '')
        if 'filename=' in cd:
            zip_name = cd.split('filename=')[-1].strip('"')
        else:
            zip_name = f'session-{child_id}.zip'
        local_path = os.path.join(output_dir, zip_name)
        with open(local_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return local_path

    # ------------------------------------------------------------------
    # 4.15 在子会话上继续对话
    # ------------------------------------------------------------------
    def continue_session(self, batch_id: str, child_id: str,
                         prompt: str) -> dict:
        """在已完成/失败的子会话上继续对话，返回响应。"""
        resp = self._post(batch_id, 'sessions', child_id, 'continue',
                          json_body={'prompt': prompt})
        return resp.json()

    # ------------------------------------------------------------------
    # 4.7 重试失败子任务
    # ------------------------------------------------------------------
    def retry_failed(self, batch_id: str) -> dict:
        resp = self._post(batch_id, 'retry-failed')
        return resp.json()


# ======================================================================
# 完整集成流程
# ======================================================================

def wait_for_batch(client: BatchClient, batch_id: str,
                   interval: int = 10, timeout: int = 600) -> str:
    """轮询批任务状态直到进入终态，返回最终状态。"""
    start = time.time()
    while time.time() - start < timeout:
        batch = client.get_batch(batch_id)
        status = batch['status']
        print(f'  [轮询] 状态={status}, 已完成={batch.get("done", 0)}, '
              f'失败={batch.get("failed", 0)}, 总数={batch.get("total", 0)}')
        if status in ('completed', 'partial', 'failed'):
            return status
        time.sleep(interval)
    raise TimeoutError(f'批任务 {batch_id} 在 {timeout}s 内未进入终态')


def demo_messages(client: BatchClient, batch_id: str, child_name: str):
    """演示：获取子会话完整对话历史。"""
    print('\n' + '=' * 60)
    print('【演示】获取子会话对话历史')
    print('=' * 60)

    result = client.get_session_messages(batch_id, child_name)
    print(f'  子会话: {result["child"]["name"]} (seq={result["child"]["seq"]})')
    print(f'  消息总数: {result["total"]}, 截断: {result["truncated"]}')

    for msg in result['messages']:
        role = msg['role']
        # 只显示前 200 字符
        parts = []
        for part in msg.get('content', []):
            if part.get('type') == 'text':
                text = part['text'][:200]
                if len(part['text']) > 200:
                    text += '...'
                parts.append(text)
            elif part.get('type') == 'tool_use':
                parts.append(f'[工具调用: {part["name"]}]')
        preview = ' | '.join(parts) if parts else '(空)'
        print(f'  [{role}] {preview}')


def demo_files(client: BatchClient, batch_id: str, child_name: str,
               output_dir: str):
    """演示：列出并下载子会话产出文件。"""
    print('\n' + '=' * 60)
    print('【演示】获取子会话文件列表')
    print('=' * 60)

    result = client.get_session_files(batch_id, child_name)
    print(f'  子会话: {result["child"]["name"]}')
    print(f'  文件数: {len(result["files"])}')

    for f in result['files']:
        print(f'    {f["dir"]:10s} {f["path"]:40s} {f["size"]:>10,} bytes')

    # 下载单个文件（如果有 outputs 文件）
    output_files = [f for f in result['files'] if f['dir'] == 'outputs']
    if output_files:
        target = output_files[0]
        print(f'\n  下载单个文件: {target["path"]}')
        local = client.download_session_file(
            batch_id, child_name, target['path'], output_dir)
        print(f'  已保存到: {local}')

    # 打包下载所有产出文件
    print(f'\n  打包下载所有产出文件...')
    zip_path = client.download_session_files_zip(batch_id, child_name, output_dir)
    print(f'  ZIP 已保存到: {zip_path}')


def demo_continue(client: BatchClient, batch_id: str, child_name: str):
    """演示：在已完成的子会话上继续对话。"""
    print('\n' + '=' * 60)
    print('【演示】在子会话上继续对话')
    print('=' * 60)

    # 先检查子会话状态
    results = client.get_results(batch_id)
    child_result = next((r for r in results['results']
                         if r['name'] == child_name), None)
    if not child_result:
        print(f'  子会话 {child_name} 不存在，跳过')
        return

    print(f'  子会话 {child_name} 当前状态: {child_result["status"]}')

    if child_result['status'] not in ('completed', 'failed'):
        print(f'  子会话不在终态，无法继续对话')
        return

    # 发送继续对话请求
    prompt = '请对前面的分析结果做一个简要总结，列出 3 个最重要的发现。'
    print(f'  发送继续对话请求: {prompt[:50]}...')
    result = client.continue_session(batch_id, child_name, prompt)
    print(f'  响应: {json.dumps(result, ensure_ascii=False, indent=2)}')

    # 轮询等待继续对话完成
    print('\n  等待继续对话完成...')
    wait_for_batch(client, batch_id, interval=5, timeout=300)

    # 获取更新后的对话（只看最后几条）
    print('\n  继续对话后的最新消息:')
    msgs = client.get_session_messages(batch_id, child_name)
    for msg in msgs['messages'][-3:]:  # 只看最后 3 条
        role = msg['role']
        parts = []
        for part in msg.get('content', []):
            if part.get('type') == 'text':
                parts.append(part['text'][:200])
        preview = ' | '.join(parts) if parts else '(空)'
        print(f'    [{role}] {preview}')


def main():
    parser = argparse.ArgumentParser(
        description='AI 批任务对外 API 进阶集成示例')
    parser.add_argument('--api-key', required=True,
                        help='API Key（需绑定用户）')
    parser.add_argument('--base-url',
                        default='http://localhost:8080/api/v1/ai-batches',
                        help='API Base URL')
    parser.add_argument('--files', nargs='+',
                        help='要上传的文件路径（不传则跳过创建，只演示已有批任务）')
    parser.add_argument('--batch-id',
                        help='已有批任务 ID（不传则自动创建）')
    parser.add_argument('--prompt',
                        default='请分析这份文件，列出关键要点和风险点。',
                        help='批任务 prompt')
    parser.add_argument('--name', default='集成示例批任务',
                        help='批任务名称')
    parser.add_argument('--output-dir', default='./batch-output',
                        help='文件下载目录')
    parser.add_argument('--skip-continue', action='store_true',
                        help='跳过继续对话演示')
    parser.add_argument('--cleanup', action='store_true',
                        help='演示结束后删除批任务')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    client = BatchClient(args.base_url, args.api_key)

    # ------------------------------------------------------------------
    # Step 1: 获取或创建批任务
    # ------------------------------------------------------------------
    batch_id = args.batch_id

    if not batch_id:
        if not args.files:
            print('错误：需要指定 --files（上传文件并创建）或 --batch-id（使用已有批任务）')
            sys.exit(1)

        print('=' * 60)
        print('Step 1: 上传文件并创建批任务')
        print('=' * 60)

        # 上传
        print(f'  上传 {len(args.files)} 个文件...')
        uploaded = client.upload_files(args.files)
        for f in uploaded:
            print(f'    {f["name"]} -> {f["path"]}')

        # 创建
        print(f'\n  创建批任务: {args.name}')
        result = client.create_batch(
            name=args.name,
            prompt=args.prompt,
            files=uploaded,
        )
        batch_id = result['batchId']
        print(f'  批任务 ID: {batch_id}')
        print(f'  初始状态: {result["status"]}, 文件数: {result["total"]}')

        # 等待完成
        print('\n  等待批任务完成...')
        final_status = wait_for_batch(client, batch_id)
        print(f'\n  批任务完成，最终状态: {final_status}')
    else:
        print(f'使用已有批任务: {batch_id}')
        batch = client.get_batch(batch_id)
        print(f'  状态: {batch["status"]}, 完成: {batch["done"]}, '
              f'失败: {batch["failed"]}, 总数: {batch["total"]}')

    # ------------------------------------------------------------------
    # Step 2: 取回结果
    # ------------------------------------------------------------------
    print('\n' + '=' * 60)
    print('Step 2: 取回处理结果')
    print('=' * 60)

    results = client.get_results(batch_id)
    print(f'  批任务状态: {results["status"]}')
    for r in results['results']:
        output_preview = (r['output'] or '')[:80]
        if r['output'] and len(r['output']) > 80:
            output_preview += '...'
        print(f'  [{r["status"]:9s}] {r["name"]:30s} '
              f'{output_preview or r.get("error") or "(无输出)"}')

    # 选择第一个完成的子会话用于后续演示
    completed = [r for r in results['results'] if r['status'] == 'completed']
    if not completed:
        print('\n  没有已完成的子会话，跳过后续演示')
        return

    child_name = completed[0]['name']
    print(f'\n  选择子会话 [{child_name}] 进行后续演示')

    # ------------------------------------------------------------------
    # Step 3: 获取对话历史
    # ------------------------------------------------------------------
    demo_messages(client, batch_id, child_name)

    # ------------------------------------------------------------------
    # Step 4: 列出并下载产出文件
    # ------------------------------------------------------------------
    demo_files(client, batch_id, child_name, args.output_dir)

    # ------------------------------------------------------------------
    # Step 5: 继续对话
    # ------------------------------------------------------------------
    if not args.skip_continue:
        demo_continue(client, batch_id, child_name)

    # ------------------------------------------------------------------
    # 清理（可选）
    # ------------------------------------------------------------------
    if args.cleanup:
        print('\n' + '=' * 60)
        print('清理: 删除批任务')
        print('=' * 60)
        client.delete_batch(batch_id)
        print(f'  批任务 {batch_id} 已删除')

    print('\n' + '=' * 60)
    print('演示完成')
    print('=' * 60)


if __name__ == '__main__':
    main()
