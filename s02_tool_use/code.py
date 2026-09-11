# s02_tool_use.py - 工具 (Tools)
#
# s01 中的智能体循环（agent loop）保持不变。本课新增了四个工具和一个调度映射表：
#
#     +----------+      +-------+      +--------------------------+
#
#     |   用户   | ---> | 大模型 | ---> | 工具调度器               |
#     |  提示词  |      |       |      | bash       -> run_bash   |
#     +----------+      +---+---+      | read_file  -> run_read   |
#                           ^          | write_file -> run_write  |
#
#                           |          | edit_file  -> run_edit   |
#                           +----------+ glob       -> run_glob   |
#                           工具返回结果+--------------------------+
#
#   + run_read / run_write / run_edit / run_glob
#   + 使用 TOOL_HANDLERS（工具处理器）替代了原来硬编码的 run_bash 调用
#   + safe_path（安全路径）确保文件工具的操作被限制在工作区内部
#
# 核心洞察：循环逻辑保持不变，增长的只是工具的注册与调度机制。

import os

from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam
from openai.types.chat import ChatCompletionMessageParam
from dotenv import load_dotenv
import json
import subprocess
load_dotenv()
from pathlib import Path # 使用更为专业的库处理路径

WORKDIR=Path.cwd()

# # -- bash工具定义（openai格式） --
# 列表，用于告诉模型有什么工具可以使用
TOOLS: list[ChatCompletionToolParam] = [{
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
}]


## s01定义的工具
# -- 工具1：执行bash --
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

## s02新增工具
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


# s02新增工具描述，一共5个 --

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern; ** matches recursively.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

# -- New in s02: dispatch map (replaces s01's hard-coded run_bash call) --

TOOL_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob,
}

# 模型客户端获取
client = OpenAI(
    api_key=os.environ.get('DEEPSEEK_API_KEY',os.getenv("DEEPSEEK_KEY")),
    base_url="https://api.deepseek.com")

MODEL="deepseek-v4-flash"
SYSTEM = f"你是一个编码智能体，你的工作路径在{os.getcwd()}. 使用windows的CMD来完成这个任务，执行而不只是描述,所有回复用中文回答."


# -- 核心板块: 一个持续触发工具调用的循环，直至模型主动终止。 --
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
            return

        # 存在工具调用，则依次执行工具获取结果
        # 目前测试阶段只有一个工具bash 因此无需进行工具：名称映射。直接取参数传入工具就行了
        for call in tool_calls:
            try:

                # 拿到参数字典
                args = json.loads(call.function.arguments)
                # 拿到实际的命令
                command = args["command"]
                print(f"\033[33m$ {command}\033[0m")

                output = str(run_bash(command))
                print(output[:200])

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
    print("s01_agent_loop: Agent Loop (OpenAI Format)")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []
    while True:
        try:
            # \001/\002 tell Readline the ANSI escapes have zero display width.
            query = input("\001\033[36m\002s01 >> \001\033[0m\002")
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