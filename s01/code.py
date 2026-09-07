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

import os  # 用于处理环境变量

from openai import OpenAI
from openai.types.chat import ChatCompletionToolParam

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



# 由于我使用的是deepseek的key需要用openai 因此这部分注释掉
# from anthropic import Anthropic
# from dotenv import load_dotenv
#
# load_dotenv(override=True)
#
# if os.getenv("ANTHROPIC_BASE_URL"):
#     os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
#
# client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
# MODEL = os.environ["MODEL_ID"]
# Please install OpenAI SDK first: `pip3 install openai`






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
    api_key=os.environ.get('DEEPSEEK_API_KEY',),
    base_url="https://api.deepseek.com")

MODEL="deepseek-v4-flash"
SYSTEM = f"你是一个编码智能体，你的工作路径在{os.getcwd()}. 使用bash来完成这个任务，执行而不只是描述."


messages=[{"role": "user", "content": "看下该文件夹下有什么"}]

# -- 核心板块: 一个持续触发工具调用的循环，直至模型主动终止。 --
response = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "system", "content": SYSTEM}] + messages,
    tools=TOOLS,
    max_tokens=8000,
    extra_body={
        "reasoning_effort": "high",
        "thinking": {"type": "disabled"}
    }
)
print(response.choices[0].message.content)

# def agent_loop(messages: list):
#     while True:
#         response = client.messages.create(
#             model=MODEL, system=SYSTEM, messages=messages,
#             tools=TOOLS, max_tokens=8000,
#         )