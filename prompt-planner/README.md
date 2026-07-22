# NAI Prompt Planner

这是从 AstrBot NovelAI 插件中抽出的独立 Prompt 规划程序。它只负责把自然语言转换成 NovelAI V4.5 Prompt，不连接 QQ、NapCat 或 NovelAI，也不读取人物库、画师串和 NovelAI PAT。

程序同时提供命令行和本机 HTTP API。两种入口共享同一套 Prompt、校验和 DeepSeek 调用逻辑。

## Windows 图形界面

打包后的 `NAI-Prompt-Planner.exe` 把 API Key、Base URL、模型、Thinking、Reasoning、超时、最大输出 Tokens、JSON Output、Prompt 字符上限、Danbooru 严格校验和画面描述全部放在一个窗口中。API Key 只在当前进程内存中使用，不保存到设置文件；其他非敏感选项会写入当前 Windows 用户的 `%APPDATA%\NAIPromptPlanner\settings.json`。

窗口提供主 Prompt、人物 Prompts 和完整 JSON 三个结果页，以及复制按钮。DeepSeek 请求在后台线程执行，不会阻塞窗口。严格校验默认开启，但生成时不会访问 Danbooru 或其他词库服务：先点击“更新本地词库”，程序会下载一次每日标签快照并转换为 `%APPDATA%\NAIPromptPlanner\danbooru-tags.sqlite3`；以后每次生成只查询本地 SQLite。失败会让 DeepSeek 最多重写两次，仍不合格就拒绝返回伪 tag。

本地词库来源是 [HDiffusion/historical-danbooru-tag-counts](https://huggingface.co/datasets/HDiffusion/historical-danbooru-tag-counts)，由 BetaDoggo/HDiffusion 从 Danbooru API 维护的每日快照，使用 Apache-2.0 协议。它不是 NovelAI 官方词表，也不是 Danbooru 官方 dump。当前快照覆盖作品数不少于 50 的约 12 万个标签，并包含分类、作品数和 active aliases。更新词库时联网一次；生成 Prompt 时不会把候选 tags 发送给任何词库服务。

重新打包：

```powershell
.\build-exe.ps1
```

输出文件：

```text
dist\NAI-Prompt-Planner.exe
```

## 安装

```powershell
cd D:\AstrBot\data\plugins\astrbot_plugin_novelai\prompt-planner
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

只通过环境变量提供 DeepSeek API Key，不要把密钥写进仓库、命令行参数或请求体：

```powershell
$env:DEEPSEEK_API_KEY = 'sk-...'
$env:DEEPSEEK_MODEL = 'deepseek-v4-flash'
```

可选配置：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容 Base URL，也可换成中转地址 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 模型名 |
| `DEEPSEEK_TIMEOUT_SECONDS` | `60` | 单次 HTTP 超时 |
| `DEEPSEEK_MAX_TOKENS` | `2048` | 最大输出 token |
| `DEEPSEEK_THINKING` | `disabled` | `disabled`、`enabled` 或 `omit` |
| `DEEPSEEK_REASONING_EFFORT` | `high` | thinking 启用时可用 `high` 或 `max` |
| `DEEPSEEK_JSON_MODE` | `true` | 是否发送 `response_format=json_object` |
| `DANBOORU_VALIDATE_TAGS` | `true` | 是否严格在线校验每个候选 tag；关闭后只做本地结构与语义检查 |
| `DANBOORU_MIN_POST_COUNT` | `50` | 可靠度启发式阈值；不能低于每日快照的 50 作品覆盖线 |
| `DANBOORU_CACHE_PATH` | `%APPDATA%\NAIPromptPlanner\danbooru-tags.sqlite3` | 本地 SQLite 词库覆盖路径 |
| `PLANNER_SERVICE_TOKEN` | 空 | HTTP 服务独立 Bearer Token；局域网监听时必填 |

也可以在命令行显式更新词库：

```powershell
nai-prompt-planner update-tags
```

Danbooru 快照不是 NovelAI 模型词表的官方镜像，因此这个检查是偏保守的质量门禁，不是对 NovelAI 理解能力的绝对判定。NovelAI V4.5 也支持自然语言，但本项目选择严格 Danbooru tag 契约，以避免 `ancient Chinese knight-errant` 一类可读却不可控的自造短语。

## 命令行

```powershell
nai-prompt-planner plan "可爱的女孩正在吃冰淇淋"
```

也可以从标准输入读取：

```powershell
"上了一天班很疲惫的女孩" | nai-prompt-planner plan
```

成功时 stdout 只输出机器可读 JSON；配置、网络或模型输出错误写入 stderr，并返回非零退出码。

## HTTP 服务

```powershell
nai-prompt-planner serve
```

默认地址为 `http://127.0.0.1:8765`：

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8765/v1/plan `
  -ContentType 'application/json' `
  -Body '{"description":"可爱的女孩正在吃冰淇淋","max_length":4000}'
```

人物槽位直接写入描述，服务会自动提取并要求模型在 `character_prompts` 中原样返回：

```json
{
  "description": "__NAI_CHARACTER_SLOT_1__正在吃冰淇淋",
  "max_length": 4000
}
```

若监听 `0.0.0.0` 或其他非本机地址，程序会拒绝在没有 `PLANNER_SERVICE_TOKEN` 的情况下启动。调用时使用：

```text
Authorization: Bearer <PLANNER_SERVICE_TOKEN>
```

`GET /health` 只返回服务是否已配置和当前模型，不返回 API Key 或 Base URL。

## 与 AstrBot 插件的边界

独立服务只应接收已经替换好人物槽位的自然语言。以下逻辑仍应留在插件端：

- 判断现成 NovelAI tags 并原样直通；
- 群人物名称替换和固定人物 Prompt 隔离；
- 画师串、负面 Prompt 与最终 V4 captions 拼接；
- NovelAI API 请求、队列和 QQ 图片返回。

项目沿用父仓库的 AGPL-3.0-only 协议。若单独发布或作为网络服务提供给他人，应同时保留对应源码和许可证义务。
