"""进程内固定窗口限速。仅适用单进程部署（生产单 waitress 进程）；
多进程横向扩展需换 Redis。"""
import threading
import time


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        # key -> {'min_win': int, 'min_cnt': int, 'day_win': int, 'day_cnt': int}
        self._buckets = {}

    def allow(self, key, per_minute, per_day, now=None):
        now = time.time() if now is None else now
        min_win = int(now // 60)
        day_win = int(now // 86400)
        with self._lock:
            b = self._buckets.get(key)
            if b is None or b['min_win'] != min_win:
                b = b or {'day_win': day_win, 'day_cnt': 0}
                b['min_win'] = min_win
                b['min_cnt'] = 0
            if b.get('day_win') != day_win:
                b['day_win'] = day_win
                b['day_cnt'] = 0
            if per_minute and b['min_cnt'] >= per_minute:
                return False
            if per_day and b['day_cnt'] >= per_day:
                return False
            b['min_cnt'] += 1
            b['day_cnt'] += 1
            self._buckets[key] = b
            return True
