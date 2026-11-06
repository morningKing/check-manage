import logging

logger = logging.getLogger(__name__)

class _SessionTimeout(Exception):
    """会话超时/停滞。区分几种情况给出可读的中文原因（而不是原始英文短语，那对
    管理员是噪音）：`stalled (no progress)` 是「STALL_TIMEOUT_SEC 内既无新文本
    也无工具在跑」的停滞检测；`tool stuck` 是「工具调用长时间无任何进展」的
    卡死检测（question 挂起、MCP 服务无响应等都落在这里）；其余情况是达到
    SESSION_TIMEOUT_SEC 硬上限。"""

    def __init__(self, seconds: int, reason: str = 'timeout',
                 diagnosis: dict | None = None):
        if reason == 'stalled (no progress)':
            msg = (f'AI 会话已 {seconds} 秒没有任何新进展（既无新文本输出，也没有工具调用在执行），'
                   f'可能是模型响应卡住、上游服务异常或网络问题，请重试该会话')
        elif reason == 'poll unreachable':
            msg = (f'连续 {seconds} 秒无法读取 AI 会话状态（OpenCode 接口不可达、网络异常'
                   f'或工作目录错位），已中断该回合，请重试该会话')
        elif reason == 'tool stuck':
            msg = (f'AI 会话的工具调用已持续 {seconds} 秒没有任何进展'
                   f'（可能是提问被挂起、工具或 MCP 服务卡死），已自动中断该回合，请重试该会话')
        else:
            msg = (f'AI 会话执行时间超过 {seconds} 秒仍未完成，已达到系统设置的最长执行时间上限，'
                   f'请重试或联系管理员调整超时设置')
        super().__init__(msg)
        self.seconds = seconds
        self.reason = reason
        # 判定现场快照（_stall_diagnosis 构建）：触发瞬间最后一条消息的
        # finish/running_tool/工具 part 状态 + pending question/permission
        # 计数。事后区分「provider 真死 / 轮询看不见 / ask 挂起没被自动拒」
        # 的一手证据——error_message 只剩最后一次，事件才留逐次记录。
        self.diagnosis = diagnosis or {}


def _format_diagnosis(d: dict) -> str:
    """诊断 dict → 单行紧凑文本（error_message 尾部可读后缀）。全防御式：
    任何缺键/畸形都不抛——诊断是为失败路径服务的，不能再炸失败路径。"""
    try:
        tools = ','.join(f"{t.get('name')}:{t.get('status')}"
                         for t in (d.get('toolParts') or [])
                         if isinstance(t, dict))
        return (f"lastMsg={d.get('lastMsgId')} finish={d.get('finish')} "
                f"runningTool={d.get('runningTool')} tools=[{tools}] "
                f"stepActive={d.get('stepActive')} "
                f"emptyParts={d.get('emptyParts')} "
                f"pendingQ={d.get('pendingQuestions')} "
                f"pendingP={d.get('pendingPermissions')} "
                f"msgs={d.get('msgCount')}")
    except Exception:  # noqa: BLE001
        return ''


class _TurnFailed(Exception):
    """OpenCode 明确报告这一轮失败了（AssistantMessage.error）。

    与 _SessionTimeout 的区别是「知道原因」：OpenCode 的 OpenAPI 把 error 定义为
    ProviderAuthError / UnknownError / MessageOutputLengthError /
    MessageAbortedError / StructuredOutputError / ContextOverflowError / APIError
    七选一。这类消息通常没有文本片段、没有运行中的工具，而 `finish` 与
    `time.completed` 在规格里都不是必填、出错时一般也不带 —— 所以若不读 error，
    它在进度签名里就是「什么都没发生」，会一路拖到 STALL_TIMEOUT_SEC 才以
    `stalled (no progress)` 失败：把一个精确、可立即行动的原因，换成了一个通用且
    误导的超时（provider 密钥没配好时，整批子任务会全部呈现为这个症状）。
    """

    def __init__(self, error: dict):
        from ai.opencode.opencode_parts import format_opencode_error
        self.error = error or {}
        super().__init__(format_opencode_error(self.error))
        self.name = self.error.get('name') or 'UnknownError'


class _SessionCancelled(Exception):
    """协作式取消：调用方经 POST .../cancel 把 cancel_requested 置真，
    _await_finished 的轮询循环下一轮发现后主动 abort OpenCode 会话并提前退出，
    而不是继续等到完成或超时。跟 etl_scheduler.py 的 cancel_requested 检查是
    同一个模式，只是这里是在轮询循环内部检查，而不是逐步骤检查。"""

    def __init__(self):
        super().__init__('已被调用方取消')


class _LeaseLost(Exception):
    """子会话执行租约丢失（心跳续租 rowcount=0）：执行权已被接管/释放。
    本 worker 必须立刻停止该 child 的工作并**不做任何终态写回**——新 owner
    会接管收口；这里的迟到写回会被 fencing 拒绝（spec §6.2）。"""

    def __init__(self):
        super().__init__('执行租约已丢失（执行权被接管）')


class _SessionPaused(Exception):
    """协作式暂停：与 _SessionCancelled 同一机制（pause_requested 置真 →
    轮询发现 → abort OpenCode → 提前退出），区别是落库为 'paused' 而非
    'cancelled'：暂停不占 failed 计数（不是失败），且批次整体显示已暂停，
    resume 可从原 OpenCode 会话续跑。"""

    def __init__(self):
        super().__init__('已被用户暂停')



__all__ = [
    '_SessionTimeout',
    '_format_diagnosis',
    '_TurnFailed',
    '_SessionCancelled',
    '_LeaseLost',
    '_SessionPaused',
    'logger',
]
