# ask-ai

在终端快速调用 LLM，一次配置，后续直接提问。Python 3.10+，无运行时第三方依赖。

```sh
ask-ai "what is pingpang"
```

## 安装

### 独立二进制（无需 Python）

构建后的单文件位于 `dist/ask-ai`（Windows 为 `dist/ask-ai.exe`）。可以把它单独复制到目标机器运行，配置与 Python 版本共用：

```sh
./dist/ask-ai config
./dist/ask-ai "what is pingpang"

# 可选：安装到 PATH 中，以后在任意目录直接使用
sudo install -m 755 dist/ask-ai /usr/local/bin/ask-ai
ask-ai "what is pingpang"
```

当前本机构建为 Linux x86_64，构建环境使用 glibc 2.40；未验证旧版 Linux 的兼容性，不适用于 Alpine/musl、Windows 或 macOS。单文件包含 Python 运行环境，但仍依赖操作系统基础库。其他平台应在对应操作系统和架构上构建；支持较旧 Linux 时应在最旧目标系统上构建，详见 [PyInstaller 平台说明](https://www.pyinstaller.org/en/stable/usage.html#platform-specific-notes)。

构建方法（只有构建机器需要 Python）：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-build.txt
.venv/bin/python scripts/build_binary.py
.venv/bin/python tests/http_smoke.py --binary dist/ask-ai
```

Windows 将 `.venv/bin/python` 和 `.venv/bin/pip` 分别替换为 `.venv\Scripts\python.exe` 和 `.venv\Scripts\pip.exe`，测试时使用 `dist/ask-ai.exe`。构建同时生成 `dist/SHA256SUMS` 和 `dist/BUILD-INFO.txt`。HTTP 测试会清除子进程 PATH 中的 Python，并在项目目录外验证独立程序。

单文件启动时会将运行库解压到临时目录，需允许该目录执行文件。HTTPS 优先使用运行环境的证书设置；没有可用证书路径时使用系统证书或随包提供的 CA 快照。自定义 CA 可通过 `SSL_CERT_FILE` / `SSL_CERT_DIR` 设置，程序不会覆盖这些设置。

### Python 安装

在项目目录执行（推荐 pipx，安装后命令可直接使用）：

```sh
pipx install .
ask-ai config
ask-ai "what is pingpang"
```

也可以用虚拟环境：

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
source .venv/bin/activate
ask-ai config
ask-ai "what is pingpang"
```

不安装也可以在项目目录运行 `./ask-ai` 或 `python3 -m ask_ai`。

## 一次配置

运行 `ask-ai config`，按提示填写配置名称、服务类型、API 根地址、模型名和 API Key。密钥输入不回显；首个配置自动成为默认配置。模型名需使用服务商或本地服务实际提供的名称。

支持 OpenAI、Anthropic Claude、Gemini，以及 OpenAI 兼容协议的 DeepSeek、通义千问、Groq、OpenRouter、vLLM、Ollama、自定义网关。`ask-ai providers` 查看预设地址及密钥环境变量。所有预设均可用 `--base-url` 覆盖；Qwen 默认使用北京区域，其他区域请填写对应地址。

### vLLM / 自建节点

假设 vLLM 已启动并提供 `/v1/chat/completions`，且服务端模型名为 `my-model`：

```sh
ask-ai config add local --provider vllm \
  --base-url http://127.0.0.1:8000/v1 \
  --model my-model --default

ask-ai "what is pingpang"
```

远程节点同样支持，替换地址即可。无认证节点无需密钥；有认证的节点可运行 `ask-ai config add local` 交互补充密钥，或指定 `--api-key-env VLLM_API_KEY`。

vLLM 需要支持聊天的模型和相应 chat template；模型名应与服务端的 `--served-model-name` 或实际加载名称一致。

### 云端服务和密钥环境变量

```sh
# YOUR_MODEL_ID 替换为你账号可用的模型名称
export OPENAI_API_KEY='你的密钥'
ask-ai config add cloud --provider openai \
  --model YOUR_MODEL_ID --api-key-env OPENAI_API_KEY

ask-ai -p cloud "解释一下 TCP 三次握手"
```

显式指定 `--api-key-env` 后，每次调用从该环境变量取密钥；缺失时报错。未指定时优先使用已保存的密钥，否则读取服务预设的环境变量。希望后续无需设置环境变量，可用交互配置保存密钥。

### 自定义兼容 API

```sh
ask-ai config add gateway --provider custom \
  --base-url https://llm.example.com/v1 \
  --model YOUR_MODEL_ID --api-key-env MY_LLM_KEY
```

`base-url` 填写 API 根地址，包含服务所需的 `/v1` 等路径；不要追加 `/chat/completions`。Claude 根地址默认包含 `/v1`，Gemini 根地址默认包含 `/v1beta`。

## 日常使用

```sh
ask-ai "用中文解释 pingpong"
ask-ai --think "分析这个复杂问题"       # 开启思考
ask-ai -r high "推导并检查这个结论"     # 选择思考强度
ask-ai -p local "写一个 Python 快排"
ask-ai -m another-model "解释这段代码"
ask-ai --system "用中文简洁回答" "What is DNS?"
cat error.log | ask-ai "分析这份日志"
printf '把这句话翻译成英语：你好' | ask-ai
ask-ai --timeout 180 --max-tokens 1024 "解释 B 树"
ask-ai --json "hello" > response.json
ask-ai -- "config"  # 对保留的子命令名称提问
```

单次问答的问题与管道输入同时存在时，管道内容附加到问题后。正常输出只包含答案，错误写入 stderr；`--json` 输出原始 API 响应。单次问答的每次请求独立；需要上下文时使用下面的 `chat` 模式。当前版本一次性输出完整结果，不提供流式输出。

### Terminal 多轮对话

```sh
ask-ai chat
ask-ai chat -p local
ask-ai chat --think
ask-ai chat -r high --system '用中文回答'
ask-ai chat '我正在学习 Python，先帮我制定计划'
ask-ai resume                 # 显示会话列表，输入序号恢复
ask-ai resume --list          # 只查看名称、ID、时间和轮数
ask-ai resume <会话ID>        # 也支持至少 4 位的唯一 ID 前缀
```

进入后逐轮输入，模型可使用当前会话之前的问答：

```text
你> 我叫小明，正在学习 Python
AI> ……
你> 我叫什么？刚才说在学什么？
AI> ……
你> /clear
你> 换个话题
AI> ……
你> /exit
```

- `/help`：查看命令。
- `/save`：手动保存当前会话（每轮成功后也会自动保存）。
- `/clear`：开始新会话，保留系统提示词和启动参数；旧会话仍保留，可随时 resume。
- `/multiline`：输入多行内容，以单独一行 `.` 结束并发送。
- `/exit`、`/quit`、Ctrl+D：退出；Ctrl+C 取消并退出。
- 使用 `//` 开头可将字面量 `/` 发送给模型，例如 `//help` 会发送 `/help`。

Chat 支持与单次问答相同的 `-p`、`-m`、`--system`、`--think`、`-r`、超时和输出限制参数，默认仍关闭思考。启动参数作用于整个会话。每轮发送完整上下文，长对话接近模型上下文上限时可用 `/clear` 开始新会话。

每轮成功后会话自动保存在配置文件旁的 `sessions/` 目录，默认是 `~/.config/ask-ai/sessions/`；可用 `ASK_AI_SESSIONS_DIR` 指定其他目录。文件包含对话文本、模型原生上下文、原配置名称、模型和会话参数，**不复制 API Key**。文件采用原子写入，POSIX 权限为 `0600`。退出后会话仍保留，也不会写入 readline 的输入历史文件。

首轮成功后，额外调用一次当前 LLM 自动生成简短会话名称（会产生一次额外 API 请求，沿用当前思考设置）。回答会先显示和保存，再生成名称；命名失败使用首条问题作名称，不影响已保存对话。会话后续轮次或 resume 不会重复命名。

`ask-ai resume` 在终端显示按最近使用时间排序的名称列表，输入序号选择，回车选择最近会话，`q` 取消。恢复后继续原有完整上下文，并复用原模型、系统提示词和请求参数；可用 `ask-ai resume <ID> --think` 或 `-r high` 修改该会话的思考设置。API Key 从原配置重新读取，允许更新密钥；原配置的服务类型或 API 地址改变时会提示先还原，避免将旧上下文发给不同节点。同一会话不能被两个终端同时恢复。

请求失败后保留先前成功的问答，本轮不加入历史，可重新输入。Claude 的思考块和 Gemini 的签名会原样保存并用于恢复后的后续请求。Chat 不提供工具调用。非交互式恢复需提供 ID，避免选择提示消耗管道中的问题。

也可通过管道逐行提供多轮输入；每行是一轮，`/multiline` 可用于多行问题，EOF 结束会话。加 `--json` 后每轮输出一行原始 JSON；管道中有请求失败时，仍处理后续输入并最终返回非零状态。

```sh
printf '我叫小明\n我叫什么？\n' | ask-ai chat
ask-ai -- 'chat'  # 单次提问字面量 chat
```

`--temperature` 仅在显式指定时发送，以兼容不支持调整温度的模型。`--max-tokens` 对 OpenAI 映射为 `max_completion_tokens`，对其他兼容节点映射为 `max_tokens`，Claude 默认上限为 4096，Gemini 使用 `maxOutputTokens`。具体参数支持范围取决于模型。

### 思考模式

默认 `--reasoning-effort none`，请求关闭思考以减少推理耗时。需要时用 `--think` 开启（等价于 `-r medium`），或用 `-r low|medium|high` 选择强度。这些选项仅影响本次请求，不修改已保存配置，`--think` 与 `-r` 不能同时使用。

| 服务 | 默认关闭 | 开启/强度 |
| --- | --- | --- |
| OpenAI、Ollama、Groq、自定义兼容节点 | `reasoning_effort: none` | `reasoning_effort: low/medium/high` |
| vLLM | `chat_template_kwargs.enable_thinking: false` | 模板开关为 true，并发送 `reasoning_effort` |
| DeepSeek | `thinking.type: disabled` | `enabled`；本客户端在此仅映射开关，三个强度都开启思考 |
| Qwen | `enable_thinking: false` | 开启并设置 1024/4096/8192 token 思考预算 |
| OpenRouter | `reasoning.enabled: false` | 开启并发送 `reasoning.effort` |
| Claude | `thinking.type: disabled` | 新模型使用 adaptive + effort；旧模型使用 1024/4096/8192 token 预算 |
| Gemini 2.5 | `thinkingBudget: 0` | 1024/4096/8192 token 预算 |
| Gemini 3 | 不支持关闭，提示显式开启 | `thinkingLevel`；3 Pro 的 medium 映射为 low（该模型仅支持 low/high） |

已识别的普通非推理模型（例如 GPT-4o、GPT-4.1、Gemini 2.0）不发送思考参数。Gemini 2.5 Pro 同样不能关闭思考。其他强制推理模型可能拒绝默认关闭参数，需显式开启或更换模型。vLLM 的开关取决于服务端模型的 chat template 是否支持，强度也由模型决定。

旧网关或不支持这些参数的节点可用 `--reasoning-effort auto`，完全不发送思考控制参数，采用服务端默认行为（可能开启思考）。程序不会在报错后自动去掉关闭参数重试，也不会把隐藏思考文本当作关闭思考。响应耗时仍取决于模型、网络、输出长度和节点负载，不能保证固定时延。

Claude 旧模型开启思考后，默认总输出上限为思考预算加 4096；手动指定 `--max-tokens` 时必须大于思考预算。各服务对 `--temperature`、预算和强度的支持范围不同。

映射参考 [OpenAI reasoning](https://developers.openai.com/api/docs/guides/reasoning)、[vLLM reasoning](https://docs.vllm.ai/en/stable/features/reasoning_outputs/)、[DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/)、[Claude thinking](https://platform.claude.com/docs/en/docs/build-with-claude/extended-thinking) 和 [Gemini thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking)。

## 配置管理

```sh
ask-ai config list
ask-ai config show local        # 密钥脱敏
ask-ai config use cloud         # 更改默认配置
ask-ai config add cloud --model another-model
ask-ai config add local --system '请用中文回答'
ask-ai config add local --no-key # 清除已保存的密钥及环境变量引用
ask-ai config remove cloud
ask-ai config path
```

更新已有同名配置会保留未指定字段；切换 provider 时会清除旧服务设置和密钥。删除默认配置后，剩余的第一个配置成为默认。

配置位置：`${XDG_CONFIG_HOME:-~/.config}/ask-ai/config.json`。可用 `ASK_AI_CONFIG` 指定其他文件；`ASK_AI_PROFILE` 指定默认档案，优先级低于命令行 `-p`，高于配置文件默认值。

保存的密钥以明文存放在本地配置文件中；文件使用原子写入，在 POSIX 系统上权限为 `0600`。`--api-key` 可用于自动化，但会进入 shell 历史；建议交互输入或使用环境变量引用。`--no-key` 不会禁用云端服务预设环境变量的回退读取。单次问答不保存历史；chat 会自动保存会话。程序不自动重试付费请求。标准 `HTTP_PROXY`、`HTTPS_PROXY` 和 `NO_PROXY` 环境变量由 Python HTTP 客户端处理。

## 验证

```sh
python3 -m unittest discover -s tests -v
```

测试覆盖配置生命周期和权限、密钥处理、三种协议的请求/响应、管道输入、临时覆盖、JSON 输出及网络错误。测试使用模拟响应，无需真实密钥，也不会调用付费 API。

安装到项目 `.venv` 后，可运行 `.venv/bin/python tests/http_smoke.py`。该端到端验证会启动临时的本地 HTTP 模拟节点，在独立进程中测试已安装命令、持久化配置及三种协议，需要允许监听 `127.0.0.1` 端口。

## 接口文档

协议实现参考 [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat)、[vLLM OpenAI-compatible server](https://docs.vllm.ai/en/stable/serving/online_serving/openai_compatible_server/)、[Claude Messages](https://platform.claude.com/docs/en/api/messages/create) 和 [Gemini generateContent](https://ai.google.dev/api/generate-content)。
