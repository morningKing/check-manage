"""AI 批任务新端点快速参考

5 个新端点的独立调用示例，每个函数可单独运行。
假设你已有一个批任务 ID 和至少一个已完成的子会话。

用法：
  from batch_api_snippets import BatchAPI
  api = BatchAPI('http://localhost:8080/api/v1/ai-batches', 'cm_xxx')

  # 获取对话
  msgs = api.get_messages('batch-id', 'report.pdf')

  # 列出文件
  files = api.list_files('batch-id', 'report.pdf')

  # 下载单个文件
  api.download_file('batch-id', 'report.pdf', 'output/result.md', '.')

  # 打包下载
  api.download_all('batch-id', 'report.pdf', '.')

  # 继续对话
  api.continue_conversation('batch-id', 'report.pdf', '请进一步分析...')
"""

import os
import requests


class BatchAPI:
    def __init__(self, base_url: str, api_key: str):
        self.base = base_url.rstrip('/')
        self.key = api_key

    def _headers(self, content_type='application/json'):
        h = {'X-API-Key': self.key}
        if content_type:
            h['Content-Type'] = content_type
        return h

    # ──────────────────────────────────────────────────────────────
    # 1. 获取子会话对话历史
    # ──────────────────────────────────────────────────────────────
    def get_messages(self, batch_id: str, child_id: str) -> dict:
        """获取完整对话历史（含工具调用）。

        Args:
            batch_id: 批任务 ID（创建时返回的 batchId）
            child_id: 子会话标识（文件名如 'report.pdf'，或序号如 '1'）

        Returns:
            {
                "batchId": "...",
                "child": {"name": "report.pdf", "seq": 0, "status": "completed"},
                "messages": [
                    {"id": "...", "role": "user", "content": [...], "createdAt": "..."},
                    {"id": "...", "role": "assistant", "content": [...], "createdAt": "..."},
                ],
                "total": 12,
                "truncated": false
            }
        """
        resp = requests.get(
            f'{self.base}/{batch_id}/sessions/{child_id}/messages',
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────
    # 2. 获取子会话文件列表
    # ──────────────────────────────────────────────────────────────
    def list_files(self, batch_id: str, child_id: str) -> dict:
        """实时扫描工作区，返回当前文件列表。

        Returns:
            {
                "files": [
                    {"name": "result.md", "path": "output/result.md", "dir": "outputs", "size": 5120},
                    ...
                ]
            }
        """
        resp = requests.get(
            f'{self.base}/{batch_id}/sessions/{child_id}/files',
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ──────────────────────────────────────────────────────────────
    # 3. 下载单个文件
    # ──────────────────────────────────────────────────────────────
    def download_file(self, batch_id: str, child_id: str,
                      path: str, output_dir: str = '.') -> str:
        """下载工作区中的单个文件。

        Args:
            path: 工作区相对路径（来自 list_files 的 files[].path）
            output_dir: 本地保存目录

        Returns:
            本地文件路径
        """
        resp = requests.get(
            f'{self.base}/{batch_id}/sessions/{child_id}/files/download',
            headers=self._headers(),
            params={'path': path},
            stream=True,
        )
        resp.raise_for_status()
        local = os.path.join(output_dir, os.path.basename(path))
        with open(local, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return local

    # ──────────────────────────────────────────────────────────────
    # 4. 打包下载所有产出文件（ZIP）
    # ──────────────────────────────────────────────────────────────
    def download_all(self, batch_id: str, child_id: str,
                     output_dir: str = '.') -> str:
        """打包下载所有新增/修改文件为 ZIP。

        Returns:
            本地 ZIP 文件路径
        """
        resp = requests.get(
            f'{self.base}/{batch_id}/sessions/{child_id}/files/download-all',
            headers=self._headers(),
            stream=True,
        )
        resp.raise_for_status()
        cd = resp.headers.get('Content-Disposition', '')
        name = cd.split('filename=')[-1].strip('"') if 'filename=' in cd \
            else f'session-{child_id}.zip'
        local = os.path.join(output_dir, name)
        with open(local, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return local

    # ──────────────────────────────────────────────────────────────
    # 5. 继续对话
    # ──────────────────────────────────────────────────────────────
    def continue_conversation(self, batch_id: str, child_id: str,
                              prompt: str) -> dict:
        """在已完成/失败的子会话上继续对话。

        Args:
            prompt: 新的提示词（AI 能看到之前的完整对话历史）

        Returns:
            {"batchId": "...", "child": {"name": "...", "seq": 0}, "status": "running"}

        Raises:
            409: 子会话不在终态
        """
        resp = requests.post(
            f'{self.base}/{batch_id}/sessions/{child_id}/continue',
            headers=self._headers(),
            json={'prompt': prompt},
        )
        resp.raise_for_status()
        return resp.json()


# ──────────────────────────────────────────────────────────────────
# 命令行快速测试
# ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse
    import json

    p = argparse.ArgumentParser(description='AI 批任务新端点快速测试')
    p.add_argument('--api-key', required=True)
    p.add_argument('--base-url', default='http://localhost:8080/api/v1/ai-batches')
    p.add_argument('--batch-id', required=True)
    p.add_argument('--child', required=True, help='子会话文件名或序号')
    p.add_argument('--action', required=True,
                   choices=['messages', 'files', 'download', 'download-all', 'continue'],
                   help='要测试的端点')
    p.add_argument('--path', help='下载文件的路径（download 时必填）')
    p.add_argument('--prompt', help='继续对话的 prompt（continue 时必填）')
    p.add_argument('--output', default='.', help='下载输出目录')
    args = p.parse_args()

    api = BatchAPI(args.base_url, args.api_key)

    if args.action == 'messages':
        result = api.get_messages(args.batch_id, args.child)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == 'files':
        result = api.list_files(args.batch_id, args.child)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.action == 'download':
        if not args.path:
            p.error('--path is required for download')
        local = api.download_file(args.batch_id, args.child, args.path, args.output)
        print(f'已下载到: {local}')

    elif args.action == 'download-all':
        local = api.download_all(args.batch_id, args.child, args.output)
        print(f'ZIP 已下载到: {local}')

    elif args.action == 'continue':
        if not args.prompt:
            p.error('--prompt is required for continue')
        result = api.continue_conversation(args.batch_id, args.child, args.prompt)
        print(json.dumps(result, ensure_ascii=False, indent=2))
