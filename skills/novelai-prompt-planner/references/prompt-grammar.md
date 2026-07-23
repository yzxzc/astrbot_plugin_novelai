# Prompt 语法与标签组织

## 1. 主 Prompt 的基本形态

使用英文逗号分隔的标签序列：

```text
1girl, solo, silver hair, blue eyes, white coat, standing, looking at viewer, cowboy shot, snowy street, night, rim lighting, blue theme
```

高信息量、决定题意的标签放前面；装饰和氛围放后面。运行时 API 已启用 `qualityToggle`，规划器不要重复输出质量词。

## 2. 权重

资料中大量使用 NAI 4.5 数字权重组：

```text
1.3::from below::, 1.3::looking up::
0.8::fog::, 0.8::sparkle::
-1.5::multiple views::
```

- `权重::标签::` 控制一个已经验证的标签，必须成对闭合。
- 大于 1 强化，小于 1 弱化；负值属于强抑制手段，应谨慎使用。
- 默认只给核心动作、关键构图或容易被模型忽略的道具加权。
- 优先采用温和范围，例如 `1.1`–`1.4`；只有经过验证的特殊串才使用更极端值。
- 旧式 `{tag}` / `{{tag}}` 强化和 `[tag]` / `[[tag]]` 弱化在资料中也很常见，但新生成的 V4.5 Prompt 优先使用数字权重，避免混合多套注意力语法和深层嵌套。

## 3. 严格标签语义

本项目使用严格 Danbooru 标签模式。每个逗号项必须能精确对应到现行、非弃用且有实际作品的 Danbooru tag，或是协议明确允许的 NovelAI 专用语法。抽象概念和复杂关系只能用于内部规划，必须拆成多个可验证的视觉标签：

```text
2girls, back-to-back, looking away, looking left, looking right
```

禁止输出自然语言句子、自造复合短语、逐词翻译短语、故事背景、人物心理活动或因果解释。不要因为 V4.5 也能理解自然语言，就把自然语言兼容能力当成 Danbooru tag；本项目选择的是更可控的严格标签契约。

## 4. 质量开关与画师串

当前 AstrBot API 请求固定启用 NovelAI `qualityToggle=true`，因此规划器不输出 `masterpiece`、`best quality`、`very aesthetic`、`absurdres`、`amazing quality`、`highres` 或分数类质量标签。这样把 Prompt 容量留给主体、服装、动作和可见细节，也避免重复质量基线。

特定媒介或画面风格属于视觉规划，不应伪装成质量词。插件还会独立拼接画师串；规划器不加入画师、年份或合作抑制标签。

## 5. Undesired Content

当前 QQ 插件默认只规划主 Prompt，API 的 negative prompt 默认留空。群成员可通过 `/nai 负面` 独立设置基础负面提示词，人物库也可保存对应的 V4 人物负面 caption；这些内容不交给规划模型改写。若调用方明确要求生成负面提示，应保持单独字段，并根据画面选择少量必要项，例如：

```text
lowres, blurry, bad anatomy, bad hands, extra digits, missing fingers, text, signature, watermark, multiple views
```

不要机械复制整套负面词；有意的模糊、裁切、单色、景深或多视图不应同时被否定。

## 6. 冲突处理

在输出前清理以下常见冲突：

- `close-up` 与 `full body`，除非有明确的分屏或插图布局。
- `day` 与 `night`，或互斥天气、季节。
- `from above` 与 `from below`。
- `looking at viewer` 与明确的闭眼、背对镜头要求。
- `solo` 与两人或多人互动。
- 静止站立与高速奔跑等互斥动作。
- 主 Prompt 中的期望效果与 Undesired Content 中的同名否定项。

用户明确要求冲突效果时，保留要求并用可实现的构图解释，例如 `split screen`、`reflection` 或 `double exposure`，不要静默删除。

## 7. 资料范围

本规则由用户提供的三份 2026-05-20 版个人法典整理而来：

- `所长常规NovelAI个人法典（2026.5.20版，一般所长整理）.docx`
- `所长色色NovalAI个人法典（上）（2026.5.20版，一般所长整理）.docx`
- `所长色色NovalAI个人法典（下）（2026.5.20版，一般所长整理）.docx`

三份文档合计约 3.57 万个非空段落，包含大量标签、数字权重、构图组合和自然语言范例。这里只保留跨题材、适合 V4.5 的稳定方法；未收录年龄含混、未成年、非自愿、剥削性或其他不安全类别。`粉红之书.docx` 与 `魔法咒語.docx` 不在截图指定的三份文档中，因此没有作为本 skill 的来源。
