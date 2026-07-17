# AstrBot / DeepSeek 运行契约

## 推荐系统提示

插件运行时只对自然语言描述调用规划器，并按顺序拼接同目录的 `runtime-system-prompt.txt` 与 `runtime-semantic-expansion.txt`：前者负责机器协议和硬约束，后者负责通用视觉补全与校准范例。已经包含 NovelAI 标签、权重或画师字段的 Prompt 必须跳过模型并原样直通。用户原始画面描述放入单独 user message。不要在代码中复制系统提示；更新 skill 后应让运行时自动读取这两个文件。

## 调用建议

- Prompt 规划使用独立 Provider，不要复用群聊会话历史。
- `persist=False`，避免前一位群成员的描述污染后一位。
- 温度宜低，目标是稳定结构化转换而非自由聊天。
- 将规划结果长度设上限；普通单人图通常不需要超长标签串。
- JSON 解析失败时只重试一次，并在重试提示中附上原始描述与格式错误，不要把模型原文直接送入 NovelAI API。
- 对 `prompt` 做长度、控制字符和禁止字段检查，再与插件管理的画师串拼接。
- 若输入含人物占位符，校验输出中的占位符集合和出现次数完全一致；校验通过后再由插件原样替换为群人物库 Prompt。
- 日志记录请求 ID、群号、用户号、耗时和失败类型；不要记录登录 Cookie、Authorization 或完整敏感 Prompt。

## 拼接顺序

推荐由插件完成：

```text
<当前群共享画师串（若用户已选择）>, <planner 返回的 prompt>
```

规划器永远不知道也不修改画师串。这样同一个规划结果可以安全地复用于不同群、不同用户的画师串状态。

## 失败回退

- Provider 超时或 JSON 无效：提示“Prompt 规划暂时失败”，不要把未验证的模型回复当成 Prompt。
- 描述过短：允许最小规划，例如 `1girl` 只做轻量质量补全，不擅自添加服装、场景或姿势。
- 描述矛盾：优先保留最后一个明确约束；仍无法判断时返回可读错误，不随机选择。

当前固定的机器协议为：

```json
{"ok":true,"prompt":"...","error":null}
```

或在硬约束确实无法消解时：

```json
{"ok":false,"prompt":null,"error":"conflicting_constraints"}
```

不得让模型自行增加字段。`error` 目前只允许 `conflicting_constraints`。
