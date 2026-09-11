"""
s01_agent_loop.py - 智能体循环（Agent Loop）

AI 编程智能体的全部奥秘，都浓缩在以下这一个模式中：

    while True:
        response = LLM(messages, tools)  # 调用大模型
        if response contains no tool_use: # 如果模型没有要求使用工具，则退出循环
            break
        execute tools                     # 执行工具
        append results                    # 将工具执行结果追加到消息列表中

    +----------+      +-------+      +---------+
    |   User   | ---> |  LLM  | ---> |  Tool   |
    |  prompt  |      |       |      | execute |
    +----------+      +---+---+      +----+----+
                          ^               |
                          |   tool_result |
                          +---------------+
                          (循环继续)

这就是核心循环：将工具的执行结果不断反馈给模型，
直到模型决定停止为止。在后续的章节中，我们会在这个核心循环之上，
逐步添加策略控制、钩子（hooks）以及生命周期管理等功能。

使用方法：
    pip install anthropic python-dotenv
    ANTHROPIC_API_KEY=你的API密钥 python s01_agent_loop/code.py
"""

import os

from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam
from openai.types.chat import ChatCompletionMessageParam
from dotenv import load_dotenv
import json
import subprocess
load_dotenv()


# # windows系统无需修复这类问题且不存在内置的readline库，以下代码执行时会跳过
# try:
#     import readline
#     # #143 解决在 macOS 系统下处理中文（或其他 UTF-8 字符）时，按下退格键（Backspace）出现乱码或光标错位的问题
#     readline.parse_and_bind('set bind-tty-special-chars off')
#     readline.parse_and_bind('set input-meta on')
#     readline.parse_and_bind('set output-meta on')
#     readline.parse_and_bind('set convert-meta off')
# except ImportError:
#     pass




# # -- bash工具定义（anthropic格式） --
# TOOLS = [{
#     "name": "bash",
#     "description": "Run a shell command.",
#     "input_schema": {
#         "type": "object",
#         "properties": {"command": {"type": "string"}},
#         "required": ["command"],
#     },
# }]

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


# -- 工具1：执行bash --
def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


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