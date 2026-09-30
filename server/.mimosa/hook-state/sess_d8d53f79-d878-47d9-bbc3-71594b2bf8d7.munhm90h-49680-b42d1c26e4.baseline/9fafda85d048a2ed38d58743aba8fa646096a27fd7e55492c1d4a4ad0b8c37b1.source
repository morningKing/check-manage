"""JWT_SECRET 的启动期校验。

背景：默认值曾是 'dev-only-change-me'（18 字节），而 PyJWT 自 2.10 起对 HS256
强制要求密钥不短于摘要长度（32 字节，见 RFC 7518 §3.2）。requirements.txt 写的是
`PyJWT>=2.8.0` 无上界，于是同一份代码在旧环境正常、在新装环境登录即炸，报
"The HMAC key is 18 bytes long..."——一个与真实问题（用了公开已知的密钥）无关的
密码学细节报错。

这里钉住两件事：
  1. 显式配了但太短 -> **启动期**就带可执行建议地失败，而不是等第一次登录才炸；
  2. 没配 -> 用一个足够长的开发默认值（任何 PyJWT 版本都能跑），并把"正在用默认值"
     这个事实暴露成一个标志位，由 app.py 在启动时大声告警。
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _reload_config(monkeypatch, secret):
    """在指定的 JWT_SECRET 环境下重新导入 config。"""
    if secret is None:
        monkeypatch.delenv('JWT_SECRET', raising=False)
    else:
        monkeypatch.setenv('JWT_SECRET', secret)
    import config
    return importlib.reload(config)


def test_explicit_short_secret_fails_at_import(monkeypatch):
    """显式配置的短密钥必须在启动期失败，且提示要能照着做。"""
    with pytest.raises(RuntimeError) as ei:
        _reload_config(monkeypatch, 'dev-only-change-me')   # 18 字节
    msg = str(ei.value)
    assert 'JWT_SECRET' in msg
    assert '18' in msg and '32' in msg          # 说清差在哪
    assert 'secrets.token_urlsafe' in msg       # 给出可直接执行的生成方式


def test_default_secret_is_long_enough_for_hs256(monkeypatch):
    """没配时用的开发默认值本身必须 >= 32 字节，否则新版 PyJWT 下开发环境也跑不起来。"""
    cfg = _reload_config(monkeypatch, None)
    assert len(cfg.JWT_SECRET.encode('utf-8')) >= 32
    assert cfg.JWT_SECRET_IS_DEFAULT is True


def test_explicit_long_secret_is_used_verbatim(monkeypatch):
    """正常配置原样使用，且不再被标记为默认值。"""
    good = 'x' * 48
    cfg = _reload_config(monkeypatch, good)
    assert cfg.JWT_SECRET == good
    assert cfg.JWT_SECRET_IS_DEFAULT is False


def test_default_secret_actually_signs_under_current_pyjwt(monkeypatch):
    """端到端的真正保障：用默认密钥能签出 HS256 token。

    这条比"长度 >= 32"更有鉴别力 —— 它直接调用装在本环境里的 PyJWT，
    所以无论其版本是否带 check_key_length，都能挡住"默认值又被改短"这类回归。
    """
    import jwt
    cfg = _reload_config(monkeypatch, None)
    token = jwt.encode({'sub': 'u1'}, cfg.JWT_SECRET, algorithm='HS256')
    assert jwt.decode(token, cfg.JWT_SECRET, algorithms=['HS256'])['sub'] == 'u1'


@pytest.fixture(autouse=True)
def _restore_config():
    """本文件反复 reload config，收尾恢复成真实环境下的模块状态，
    免得污染同一进程里后续用到 config 的测试。"""
    yield
    import config
    importlib.reload(config)
