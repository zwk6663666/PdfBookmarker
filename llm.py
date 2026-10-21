"""用视觉大模型读取目录页（可选功能，默认不启用）。

只负责把目录页图片交给大模型、取回 (标题, 页码, 层级) 列表；
查找目录页、推算页码偏移等步骤仍由 core 在本地完成。
只用标准库发请求，不增加依赖；会自动使用系统代理。
"""
from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.request

# 预设接口：(id, 显示名, 协议, 默认地址, 推荐模型, 是否需要密钥)
# 协议 openai 指 OpenAI 兼容的 /chat/completions，大多数国内外厂商都支持；
# anthropic 指 Claude 的 /messages。模型名只是建议，可在界面里改成任意值。
PROVIDERS = [
    ("openai", "OpenAI", "openai", "https://api.openai.com/v1",
     ["gpt-5.5", "gpt-5.6", "gpt-5-mini"], True),
    ("anthropic", "Anthropic Claude", "anthropic", "https://api.anthropic.com/v1",
     ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"], True),
    ("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
     ["gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-flash-latest"], True),
    ("qwen", "通义千问（阿里云百炼）", "openai", "https://dashscope.aliyuncs.com/compatible-mode/v1",
     ["qwen3-vl-plus", "qwen3-vl-flash", "qwen3.7-plus"], True),
    ("zhipu", "智谱 GLM", "openai", "https://open.bigmodel.cn/api/paas/v4",
     ["glm-4.6v", "glm-4.6v-flash"], True),
    ("doubao", "豆包（火山方舟）", "openai", "https://ark.cn-beijing.volces.com/api/v3",
     ["doubao-seed-1.6", "doubao-1.5-thinking-vision-pro"], True),
    ("moonshot", "Kimi（月之暗面）", "openai", "https://api.moonshot.cn/v1",
     ["kimi-k2.6", "moonshot-v1-32k-vision-preview"], True),
    ("siliconflow", "硅基流动", "openai", "https://api.siliconflow.cn/v1",
     ["Qwen/Qwen3-VL-235B-A22B-Instruct", "zai-org/GLM-4.6V"], True),
    ("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
     ["openai/gpt-5.5", "google/gemini-3.5-flash", "anthropic/claude-sonnet-5.5"], True),
    ("ollama", "Ollama（本机）", "openai", "http://localhost:11434/v1",
     ["qwen3-vl", "gemma3"], False),
    ("custom", "自定义", "openai", "", [], True),
]
PROVIDER_MAP = {p[0]: p for p in PROVIDERS}
PROTOCOLS = [("openai", "OpenAI 兼容（/chat/completions）"), ("anthropic", "Anthropic（/messages）")]

PROMPT = """这是一本书的目录页图片（共 {n} 张，按顺序排列）。请逐条提取目录中的所有条目。

只输出一个 JSON 数组，不要任何解释或 Markdown 代码块。每个元素格式：
{{"title": "标题（保留章节编号，例如“第1章 绪论”“1.2 研究方法”）", "page": "书中印刷页码，原样照抄（阿拉伯数字或罗马数字），没有页码时为空字符串", "level": 层级整数}}

要求：
- level 从 1 开始，按目录的层次结构（编号规则和缩进）判断：篇/部或章为 1，节为 2，小节为 3，依此类推。
- 不要输出“目录”“Contents”这类页面标题本身，也不要输出页眉页脚。
- 标题跨行时合并成一条；去掉标题与页码之间的引导点（……）。
- 按目录中出现的顺序输出，不要遗漏、不要编造。"""


class LLMError(RuntimeError):
    def __init__(self, msg, code=None, detail=""):
        super().__init__(msg)
        self.code, self.detail = code, detail


def endpoint(protocol, base):
    base = (base or "").strip().rstrip("/")
    if not base:
        raise LLMError("没有填写接口地址。")
    tail = "/messages" if protocol == "anthropic" else "/chat/completions"
    return base if base.endswith(tail) else base + tail


def _post(url, headers, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"接口返回错误 {e.code}：{detail}", e.code, detail) from e
    except urllib.error.URLError as e:
        raise LLMError(f"无法连接接口：{e.reason}") from e
    except TimeoutError as e:
        raise LLMError("接口响应超时。") from e
    except json.JSONDecodeError as e:
        raise LLMError("接口返回的不是 JSON，请检查接口地址。") from e


def chat(cfg, prompt, images=(), max_tokens=8192, timeout=180):
    """发送一条带图片的消息，返回模型回复的文本。images 为 JPEG 字节串列表。"""
    protocol = cfg.get("protocol", "openai")
    model = (cfg.get("model") or "").strip()
    key = (cfg.get("key") or "").strip()
    if not model:
        raise LLMError("没有填写模型名称。")
    url = endpoint(protocol, cfg.get("base"))
    b64 = [base64.b64encode(im).decode("ascii") for im in images]
    if protocol == "anthropic":
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": d}}
                   for d in b64]
        content.append({"type": "text", "text": prompt})
        body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": content}]}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        data = _post(url, headers, body, timeout)
        try:
            return "".join(c.get("text", "") for c in data["content"] if c.get("type") == "text")
        except (KeyError, TypeError) as e:
            raise LLMError(f"无法解析接口返回：{str(data)[:300]}") from e
    content = [{"type": "text", "text": prompt}]
    content += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{d}"}} for d in b64]
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}]}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    # 各家对参数的支持不一：OpenAI 推理模型（GPT-5 等）要求用 max_completion_tokens、
    # 不接受 temperature=0；部分接口限制 max_tokens 上限。被拒时按提示调整后重试。
    for _ in range(4):
        try:
            data = _post(url, headers, body, timeout)
            break
        except LLMError as e:
            d = e.detail.lower()
            if e.code not in (400, 422):
                raise
            if "max_completion_tokens" in d and "max_tokens" in body:
                body["max_completion_tokens"] = body.pop("max_tokens")
            elif "temperature" in d and "temperature" in body:
                body.pop("temperature")
            elif "max_tokens" in d and body.get("max_tokens", 0) > 4096:
                body["max_tokens"] = 4096
            else:
                raise
    else:
        raise LLMError("接口多次拒绝请求参数。")
    try:
        msg = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"无法解析接口返回：{str(data)[:300]}") from e
    if isinstance(msg, list):
        msg = "".join(p.get("text", "") for p in msg if isinstance(p, dict))
    return msg or ""


def parse_items(text):
    """从模型回复中取出 JSON 数组，返回 [(title, page_str, level)]。"""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        raise LLMError("模型没有返回目录列表。回复开头：" + t[:200])
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError("模型返回的 JSON 格式不正确。") from e
    out = []
    for o in arr:
        if not isinstance(o, dict):
            continue
        title = str(o.get("title") or "").strip()
        if not title:
            continue
        page = str(o.get("page") if o.get("page") is not None else "").strip()
        try:
            level = max(1, int(o.get("level") or 1))
        except (TypeError, ValueError):
            level = 1
        out.append((title, page, level))
    return out


def _interruptible(fn, check, *args):
    """在后台线程里执行 fn，期间每 0.2 秒调用一次 check()，以便请求进行中也能立即终止。"""
    if check is None:
        return fn(*args)
    box = {}
    done = threading.Event()

    def run():
        try:
            box["res"] = fn(*args)
        except BaseException as e:  # noqa
            box["err"] = e
        done.set()
    threading.Thread(target=run, daemon=True).start()
    while not done.wait(0.2):
        check()  # 抛出异常即放弃等待，残留的请求线程结束后自行退出
    if "err" in box:
        raise box["err"]
    return box["res"]


def read_toc(images, cfg, batch=4, check=None, progress=None):
    """把目录页图片分批交给模型识别。images 为 [(pno, jpeg_bytes)]，返回 [(pno, title, page, level)]。"""
    out = []
    for i in range(0, len(images), batch):
        if check:
            check()
        part = images[i:i + batch]
        if progress:
            progress(i, len(images))
        items = parse_items(_interruptible(chat, check, cfg, PROMPT.format(n=len(part)), [im for _, im in part]))
        # 每批的条目记在该批第一页名下（只用于排序和插入“目录”书签）
        out += [(part[0][0], *it) for it in items]
    return out


def test(cfg):
    """测试连通性，返回模型回复的前几十个字。"""
    # 推理模型会先消耗一部分 token 思考，上限不能给得太小，否则回复为空
    return chat(cfg, "请只回复：OK", max_tokens=1024, timeout=90).strip()[:60]
