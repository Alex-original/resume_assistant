"""
配置：密钥、模型、路径。

## 密钥解析顺序（三级回退）

    1. 项目自己的 .env            ← 优先，用户在这里覆盖
    2. 工作区 .env（../../.env）   ← 已有的公共配置
    3. ~/.dsh/.env                ← DSH 的密钥位置

这样开箱即用（能读到已有 key），同时用户可以在项目 .env 里单独覆盖。

## 为什么密钥不写死在代码里

`DASHSCOPE_API_KEY` 留给用户填。项目 .env 已被 .gitignore 忽略。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # product/job_assistant
WORKSPACE = ROOT.parent.parent                          # Agent-100-Days
# 容器里数据要挂到卷上，所以允许用环境变量指定（compose 里就是这么做的）
DATA_DIR = Path(os.environ.get('DATA_DIR') or (ROOT / 'data'))
UPLOAD_DIR = DATA_DIR / 'uploads'
WEB_DIR = ROOT / 'web'

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ENV_CANDIDATES = [
    ROOT / '.env',
    WORKSPACE / '.env',
    Path.home() / '.dsh' / '.env',
]

DEFAULTS = {
    'DEEPSEEK_API_KEY': '',
    'DEEPSEEK_BASE_URL': 'https://api.deepseek.com/v1',
    'DEEPSEEK_MODEL': 'deepseek-chat',
    'DASHSCOPE_API_KEY': '',
    'DASHSCOPE_BASE_URL': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    'VISION_MODEL': 'qwen-vl-max',
    'ASR_MODEL': 'qwen3-asr-flash',
    'TTS_MODEL': 'qwen3-tts-flash',
    'TTS_VOICE': 'Cherry',
    # 扫描版 PDF 逐页转图的上限（一页约 18 秒；超了会在摘要里注明"只读了前 N 页"）
    'SCAN_MAX_PAGES': '8',
    'APP_PORT': '7870',
    'APP_HOST': '127.0.0.1',
    # ── 多用户 / 部署相关 ──
    # 这些必须列在这里：_resolve() 只把**已在字典里的键**从进程环境变量里捞出来，
    # 没列进来的环境变量会被直接忽略——Docker 的 env_file 就是这么传配置的，
    # 漏一个就等于配置不生效（表现是"明明填了 SECRET_KEY 却还是自己生成"）。
    'SECRET_KEY': '',
    'ADMIN_USERNAME': '',
    'ADMIN_PASSWORD': '',
    'COOKIE_SECURE': '',
    'WORKSPACE_DIR': '',
    'ASSISTANT_DIR': '',
    # ── 手机号登录 / 阿里云短信 ──
    # 和 video_note 项目用的是同一套变量名，配置可以直接搬过来
    'ADMIN_PHONE': '',
    'ALIYUN_ACCESS_KEY_ID': '',
    'ALIYUN_ACCESS_KEY_SECRET': '',
    'SMS_SIGN_NAME': '',
    'SMS_TEMPLATE_CODE': '',
    'SMS_DEV_MODE': '',        # =1 时把验证码也返回给前端（仅本机开发用）
}


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            out[key] = value
    return out


def _resolve() -> dict[str, str]:
    """按优先级合并：默认值 → 各级 .env → 进程环境变量。"""
    config = dict(DEFAULTS)
    for path in reversed(ENV_CANDIDATES):     # 反序：优先级高的后覆盖
        config.update(_parse_env_file(path))
    for key in list(config.keys()):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


CONFIG = _resolve()


def get(key: str, default: str = '') -> str:
    return CONFIG.get(key, default) or default


def key_status() -> dict[str, dict]:
    """给设置页用：显示每个密钥是否已配置，但绝不回显内容。"""
    out = {}
    for name in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY'):
        value = get(name)
        out[name] = {
            'configured': bool(value),
            'masked': f'{value[:6]}…{value[-4:]}' if len(value) > 12 else ('已配置' if value else '未配置'),
        }
    return out


def missing_keys() -> list[str]:
    return [k for k in ('DEEPSEEK_API_KEY', 'DASHSCOPE_API_KEY') if not get(k)]

def workspace_dir() -> Path:
    """
    资料源目录（求职助手/ 的上一层）。服务器上用 WORKSPACE_DIR 指定。

    默认取项目上两级——本地开发时正好是 Agent-100-Days；
    容器里通常不存在，调用方要自己判断文件在不在。
    """
    import os
    return Path(os.environ.get('WORKSPACE_DIR') or WORKSPACE)
