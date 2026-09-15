# s04_hooks.py - 钩子 (Hooks)
#
# 钩子会在代理（Agent）运行循环的固定节点触发回调函数：
#
#     用户提示词 (User prompt)
#
#          |
#          v
#     用户提示词提交 (UserPromptSubmit)
#
#          |
#          v
#     +----------+      +-------+      +------------+      +-------+
#
#     | 消息记录 | ---> | 大模型  | ---> | 工具使用前 | ---> | 工具  |
#     | messages |      |  LLM  |      | 权限检查   |      | Tool  |
#     +----------+      +---+---+      | 日志记录   |      +---+---+
#          ^                | 停止     +------------+          |
#          |                v                                 v
#          |           停止钩子                             工具使用后
#          |                                               |
#          +---------------- 工具执行结果 ------------------+


import os
import re
from typing import List

from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam
from openai.types.chat import ChatCompletionMessageParam
from dotenv import load_dotenv
import json
import subprocess
load_dotenv()
from pathlib import Path # 使用更为专业的库处理路径

WORKDIR=Path.cwd()
print("="*20+f"当前工作空间为{WORKDIR}"+"="*20)


# -- s02: 工具执行脚本 --
def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

def safe_path(p: str) -> Path:
    """
    安全路径解析器，用于防止路径遍历攻击（Path Traversal）。

    该函数将用户提供的相对路径与预设的工作目录（WORKDIR）拼接，
    并解析为绝对路径。同时，它会严格校验解析后的路径是否仍在
    工作目录的范围内，防止通过 '../' 等方式越权访问系统文件。

    Args:
        p (str): 用户提供的相对路径字符串。

    Returns:
        Path: 经过安全校验的绝对路径对象。

    Raises:
        ValueError: 当解析后的路径逃逸出工作目录时抛出。
    """
    # 将相对路径拼接到工作目录下，并解析为真实的绝对路径（消除 . 和 ..）
    path = (WORKDIR / p).resolve()

    # 核心安全检查：判断解析后的路径是否仍在允许的工作区内
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")

    return path


def run_read(path: str, limit: int | None = None) -> str:
    """
    安全读取指定文件的内容。

    Args:
        path (str): 待读取文件的相对路径。
        limit (int | None, optional): 限制返回的最大行数。默认为 None（读取全部）。

    Returns:
        str: 文件内容字符串；若读取失败或超出限制，返回包含错误提示或截断提示的字符串。
    """
    try:
        # 解析安全路径并读取文件内容，按行分割
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()

        # 如果设置了行数限制且实际行数超限，则截断并附加提示信息
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]

        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    """
    将内容写入指定文件。若文件所在目录不存在，会自动递归创建。

    Args:
        path (str): 目标文件的相对路径。
        content (str): 待写入的文本内容。

    Returns:
        str: 写入成功的提示信息（包含写入的字节数）；若写入失败，返回错误提示。
    """
    try:
        # 解析安全路径
        file_path = safe_path(path)

        # 确保父级目录存在，若不存在则递归创建
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # 以 UTF-8 编码写入文件内容
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """
    对指定文件进行局部文本替换（仅替换第一次出现的匹配项）。

    Args:
        path (str): 目标文件的相对路径。
        old_text (str): 需要被替换的原始文本。
        new_text (str): 用于替换的新文本。

    Returns:
        str: 编辑成功的提示信息；若未找到目标文本或发生其他异常，返回错误提示。
    """
    try:
        file_path = safe_path(path)
        text = file_path.read_text(encoding="utf-8")

        # 检查目标文本是否存在于文件中
        if old_text not in text:
            return f"Error: text not found in {path}"

        # 仅替换第一次出现的匹配项，并写回文件
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    """
    在工作区内根据通配符模式搜索匹配的文件路径。

    Args:
        pattern (str): 用于文件匹配的 Glob 模式（支持递归匹配）。

    Returns:
        str: 匹配到的文件路径列表（最多展示200条，按字母排序）；若无匹配项或发生异常，返回相应的提示信息。
    """
    import glob as g
    try:
        # 在工作区内执行递归搜索，并使用集合去重
        matches = sorted({
            match for match in g.glob(
                pattern, root_dir=WORKDIR, recursive=True)
            # 二次安全校验：确保匹配到的路径没有逃逸出工作区
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR)
        })

        # 限制返回结果的数量，防止输出过大导致上下文溢出
        shown = matches[:200]
        if len(matches) > 200:
            shown.append("... (more matches omitted; narrow the pattern)")

        return "\n".join(shown) if shown else "(no matches)"
    except Exception as e:
        return f"Error: {e}"

# -- s02 : 工具定义和映射关系 --
TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": "bash", # 工具名称
            "description": "用于执行shell命令.", # 工具的具体作用，需要准确的描述
            "parameters": { # 用于给大模型解释工具如何使用的参数
                "type": "object", # 告诉模型，这个工具需要的输入参数是一个“对象”（在 Python 里就是字典/Dict）。
                "properties": {"command": {"type": "string"}}, # 这是参数的具体清单。它告诉模型：“这个工具需要一个名叫 command 的参数，并且这个参数必须是一个字符串（string）类型。”
                "required": ["command"] # 这是强制约束。它告诉模型：“command 这个参数是必填项。你在调用工具时，绝对不能漏掉它，否则工具会报错。”
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",  # 工具名称
            "description": "读取文件内容.",  # 工具的具体作用，需要准确的描述
            "parameters": {  # 用于给大模型解释工具如何使用的参数
                "type": "object",  # 告诉模型，这个工具需要的输入参数是一个“对象”（在 Python 里就是字典/Dict）。
                "properties": {"path": {"type": "string"},"limit": {"type": "integer"}},
                # 这是参数的具体清单。它告诉模型：“这个工具需要一个名叫 command 的参数，并且这个参数必须是一个字符串（string）类型。”
                "required": ["path"]  # 这是强制约束。它告诉模型：“command 这个参数是必填项。你在调用工具时，绝对不能漏掉它，否则工具会报错。”
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "向文件中写入内容.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"},"content": {"type": "string"}},
                "required": ["path", "content"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "在文件中仅替换一次精确匹配的文本。.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
                "required": ["path", "old_text", "new_text"]
            },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "查找匹配 glob 通配符模式的文件；其中 ** 表示递归匹配。",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                 "required": ["pattern"]
            },
        }
    },
]


TOOL_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob,
}

# -- s04: 钩子系统 (s03 审批逻辑使用钩子) --
# 钩子的字典，用户提交提示词，工具调用前，工具调用后，停
HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}

# 注册钩子，将方法注册到对应的事件中
def register_hook(event: str, callback):
    # 这部分可以再健壮一点，HOOKS.get('',[])再append
    HOOKS[event].append(callback)

# 触发钩子
def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:  # A hook result blocks this tool call.
            return result
    return None


# s03 审批检查逻辑, 现在包装为hook
# Gate 1: 严格禁止，直接拒绝
DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda"]
# Gate 2: 规则匹配——基于命令内容的检查
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]

def contains_destructive_command(command: str) -> bool:
    """
    检测是否包含破坏性的命令
    :param command:
    :return:
    """
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))

def permission_hook(call):
    """工具调用前: s03 check_permission() 的逻辑移动到这里."""

    tool_name = call.function.name
    tool_args = json.loads(call.function.arguments)

    if tool_name == "bash":
        command = tool_args.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                print(f"\n\033[31m[blocked] '{pattern}'\033[0m")
                return "Permission denied by deny list"
        if contains_destructive_command(command) or any(
            kw in command for kw in DESTRUCTIVE
        ):
            print(f"\n\033[33m[permission] Potentially destructive command\033[0m")
            print(f"   Tool: {tool_name}({tool_args})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    if tool_name in ("read_file", "write_file", "edit_file"):
        path = tool_args.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            print(f"\n\033[33m[permission] Access outside workspace\033[0m")
            print(f"   Tool: {tool_name}({tool_args})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    return None


def log_hook(block):
    """工具调用前: 记录每个工具的调用"""
    # 从 block.input（字典）中获取前2个参数的值，转为字符串，并截取前60个字符，防止日志过长
    args_preview = str(list(block.input.values())[:2])[:60]
    # 在控制台打印灰色的日志，显示工具名称和参数预览
    print(f"\033[90m[HOOK] {block.name}({args_preview})\033[0m")
    # 返回 None 表示不干预当前流程，继续执行工具
    return None

def large_output_hook(block, output):
    """工具调用后: 大输出警告."""
    # 检查工具执行后的输出内容（转为字符串后）长度是否超过了 100,000 个字符
    if len(str(output)) > 100000:
        # 如果超过，打印黄色的警告信息，提示哪个工具产生了多大的输出
        print(f"\033[33m[HOOK] Large output from {block.name}: {len(str(output))} chars\033[0m")
    # 返回 None 表示不干预当前流程，继续执行后续逻辑
    return None

# 用户提示词提交钩子：在用户输入到达大语言模型（LLM）之前，记录该输入，当前只返回工作目录。
def context_inject_hook(query: str):
    # 当用户提交提示词时，打印灰色的日志，显示当前 Agent 正在操作的工作目录（WORKDIR）
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    # 返回 None 表示不修改用户的原始输入，继续发送给大模型
    return None

# stop钩子: 当循环退出时打印汇总信息
def summary_hook(messages: list):
    # 统计整个会话历史（messages）中，工具调用结果（tool_result）的总数
    # 这是一个嵌套的生成器表达式：
    # 1. 遍历 messages 中的每一条消息 m
    # 2. 获取 m 的 content，如果是列表则遍历，否则返回空列表
    # 3. 检查列表中的块 b 是否是字典，且 type 为 "tool_result"
    tool_count = sum(1 for m in messages
                     for b in (m.get("content") if isinstance(m.get("content"), list) else [])
                     if isinstance(b, dict) and b.get("type") == "tool_result")
    # 打印灰色的总结信息，显示本次会话总共调用了多少次工具
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    # 返回 None 表示不干预停止流程
    return None

# 将上述函数注册到对应的生命周期事件上
register_hook("UserPromptSubmit", context_inject_hook)  # 注册到“用户提交提示词”事件
register_hook("PreToolUse", permission_hook)            # 注册到“工具调用前”事件（执行权限检查）
register_hook("PreToolUse", log_hook)                   # 注册到“工具调用前”事件（执行日志记录）
register_hook("PostToolUse", large_output_hook)         # 注册到“工具调用后”事件（检查大输出）
register_hook("Stop", summary_hook)                     # 注册到“会话停止”事件（打印总结）



# 模型客户端获取
client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY',os.getenv("DEEPSEEK_KEY")),
    base_url="https://api.deepseek.com")

MODEL="deepseek-v4-flash"
SYSTEM = f"你是一个编码智能体，你的工作路径在{os.getcwd()}. 使用windows的CMD来完成这个任务，执行而不只是描述,所有回复用中文回答."

# -- Agent loop: 在s02基础上增加人工审批环节 --

def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        if not tool_calls:
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            return

        results = []
        for block in tool_calls:
            # s04 change: hook replaces hard-coded check_permission()
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": str(blocked)})
                continue

            handler = TOOL_HANDLERS.get(block.name)
            output = handler(**block.input) if handler else f"Unknown: {block.name}"

            trigger_hooks("PostToolUse", block, output)  # s04: post hook

            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})

        messages.append({"role": "user", "content": results})


def agent_loop(messages: list):
    # 构建一个新的列表，确保类型对齐
    while True:
        response = client.chat.completions.create(
            messages= [{"role": "system", "content": SYSTEM}]+messages,# type: ignore
            model=MODEL,
            tools=TOOLS,
            max_tokens=8000,
            extra_body={
                "reasoning_effort": "high",
                "thinking": {"type": "disabled"}
            }
        )
        # 模型返回
        assistant_message = response.choices[0].message
        # 将本轮会话的ai输出加入对话上下文中，模型会自动处理message格式，无需手动构造。
        messages.append(assistant_message)
        # messages.append({"role": "assistant", "content": assistant_message})

        # 检查tool_calls(列表),如果没有工具调用，则循环结束返回
        tool_calls = assistant_message.tool_calls or []
        if not tool_calls:
            # 当没有工具的时候说明这轮会话结束，循环停止，触发停止后的钩子
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            return

        # 存在工具调用，则依次执行工具获取结果
        for call in tool_calls:
            try:
                print(f"\033[33m$ {call.function.name}\033[0m")

                if not check_permission(call):
                    messages.append({"role": "tool","tool_call_id": call.id,
                                    "content": "Permission denied."})
                    continue
                handler=TOOL_HANDLERS.get(call.function.name)
                # 拿到参数字典
                args = json.loads(call.function.arguments)
                # 执行之前进行判别

                output = handler(**args) if handler else f"Unknown: {call.function.name}"
                print(str(output)[:200])


            except Exception as exc:
                # 将工具错误反馈给模型，让模型有机会调整
                output = f"工具执行失败: {type(exc).__name__}: {exc}"
            # 每次把tool的返回添加到message中
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": output,
            })


if __name__ == "__main__":
    print("s03: Permission")
    print("输入问题，回车键发送，输入 q 退出.\n")

    history = []
    while True:
        try:
            # \001/\002 tell Readline the ANSI escapes have zero display width.
            query = input("\001\033[36m\002s03 >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break

        # OpenAI 格式：用户消息的 content 必须是字符串
        history.append({"role": "user", "content": query})
        agent_loop(history)
        # OpenAI 格式：提取最后一条助手消息的文本内容
        last_message = history[-1]
        if last_message.role == "assistant":
            final_text = getattr(last_message, "content", "")
            if final_text:
                print(final_text)

        print()
