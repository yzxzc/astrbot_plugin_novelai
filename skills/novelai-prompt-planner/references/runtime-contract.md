# AstrBot / DeepSeek 运行契约

## 推荐系统提示

插件运行时只对自然语言描述调用规划器，并按顺序拼接同目录的 `runtime-system-prompt.txt` 与 `runtime-semantic-expansion.txt`：前者负责机器协议和硬约束，后者负责内容优先补全与校准范例。已经包含 NovelAI 标签、权重或画师字段的 Prompt 必须跳过模型并原样直通。用户原始画面描述放入单独 user message。不要在代码中复制系统提示；更新 skill 后应让运行时自动读取这两个文件。

## 调用建议

- Prompt 规划使用独立 Provider，不要复用群聊会话历史。
- 不传群聊历史或已有 `contexts`；每次规划只包含本次原始描述，避免不同群成员互相污染。
- 温度宜低，目标是稳定结构化转换而非自由聊天。
- 将规划结果长度设上限；普通输入按可见信息是否完整判断，不追求固定标签数量。
- 首次输出失败后最多进行两次修复，并在重试提示中附上原始描述与格式错误；不要把模型原文直接送入 NovelAI API。
- 对模型生成的每个普通逗号项进行本地 Danbooru 词库校验：先解析 NovelAI 权重和 V4 人物动作前缀，再从本地 SQLite 查询 alias、分类和作品数。不存在、低于本地快照覆盖线或属于画师分类的标签必须要求模型替换；低频阈值属于可靠度启发式，不代表 NovelAI 官方词表。
- `source#`、`target#`、`mutual#` 只允许出现在人物 Prompt，去掉前缀后的动作 tag 仍需精确校验。`girl`、`boy`、`other` 是 V4 人物 caption 类型，作为协议专用值处理。
- 词库更新和生成必须解耦。只在用户显式更新时下载每日快照并原子替换 SQLite；每次生成不得访问 Danbooru 或快照服务。严格模式缺少或损坏本地词库时应在调用模型前失败，避免浪费 DeepSeek 请求；若调用方允许关闭校验，界面和日志必须明确只完成了结构校验。
- 对 `prompt` 做长度和禁止字段检查，再与插件管理的画师串拼接。
- 若输入含人物占位符，校验 `character_prompts` 的键集合完全一致；校验通过后再把人物库固定 Prompt 与动态人物 Prompt 合并为原生 V4 `char_captions`。
- API 已启用 `qualityToggle`；规划结果不应再包含质量词。
- 日志记录请求 ID、群号、用户号、耗时和失败类型；不要记录登录 Cookie、Authorization 或完整敏感 Prompt。

## 拼接顺序

推荐由插件完成：

```text
<当前群共享画师串（若用户已选择）>, <planner 返回的 prompt>
```

规划器永远不知道也不修改画师串。这样同一个规划结果可以安全地复用于不同群、不同用户的画师串状态。

## 失败回退

- Provider 超时或 JSON 无效：提示“Prompt 规划暂时失败”，不要把未验证的模型回复当成 Prompt。
- 描述过短：执行内容优先补全，先完善主体、服装、材质、装饰、姿势和表情；不要用无请求的镜头、背景和光影填充长度。
- 描述矛盾：优先保留最后一个明确约束；仍无法判断时返回可读错误，不随机选择。

当前固定的机器协议为：

```json
{"ok":true,"prompt":"...","character_prompts":{},"error":null}
```

有人物库槽位时必须返回完全相同的键：

```json
{"ok":true,"prompt":"...","character_prompts":{"__NAI_CHARACTER_SLOT_1__":"动态动作与表情"},"error":null}
```

硬约束确实无法消解时：

```json
{"ok":false,"prompt":null,"character_prompts":{},"error":"conflicting_constraints"}
```

不得让模型自行增加字段。`error` 目前只允许 `conflicting_constraints`。
