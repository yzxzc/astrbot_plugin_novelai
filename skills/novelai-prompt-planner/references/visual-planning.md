# 视觉规划方法

## 1. 画面槽位

把描述拆成以下槽位。年龄、性别、人数、身份和人物固定外观没有依据时留空。主体、服装、动作和表情是短输入的主要补全区；镜头、环境、光照和氛围只有在用户要求或动作成立需要时才补，不要为了数量加入无关细节。

| 槽位 | 典型内容 |
|---|---|
| 主体 | 人数、用户明确提供的年龄或性别表达、角色类型、物种 |
| 外观 | 发型、发色、眼睛、体型、表情、可见特征 |
| 服装与道具 | 服装层次、材质、配饰、手持物、关键颜色 |
| 动作 | 姿势、手部动作、视线、移动方向、人物互动 |
| 镜头 | 景别、视角、镜头倾斜、焦点、透视 |
| 构图 | 主体位置、对称、留白、前中后景、反射、分割 |
| 环境 | 室内外、地点、时间、天气、季节、背景元素 |
| 光色 | 主光方向、轮廓光、体积光、明暗关系、色彩主题 |
| 氛围与效果 | 宁静、紧张、梦幻、粒子、雾、运动感 |

### 服装细化顺序

对短角色主题，按“主件、内外层、剪裁、领口和袖型、腰部、下装、鞋袜、材质、配色、纹样、闭合件、金属件和饰品”逐层选择相容细节。不要机械填满每一层，也不要用三个泛化词代替完整设计。

用户已指定服装方向时只能细化其结构，不能换装。人物库占位符已经携带固定服装，动态人物 Prompt 不得再补服装或材质。

## 2. 从抽象词到可见细节

先区分两类内容：纯心理状态要转成身体语言、表情和可见痕迹；原文中可直接描绘的空间、物质或动作隐喻则是画面约束，不能删掉。例如“悲伤之海中下沉”应保留水下、海、下沉与漂浮状态，而不是只输出 `sad`、`depressed`。

空间隐喻按三层规划：先确定承载意象的环境或材质，再确定主体与它的动作、方向、距离或尺度关系，最后只补一个主景别/视角和 2–4 个让这种关系可见的构图或光照标签。例如深海下沉可以从 `wide shot, from above, negative space, darkness, light rays` 中选择相容项；牢笼、冰封、城市压迫或灰烬场景应根据各自空间关系另选标签，不能照抄深海模板。相反，只有“疲惫、悲伤、发呆”的输入停留在姿态和表情层，不补远景、留白、黑暗或光线。

抽象词只作为方向，需要用少量视觉信号落地：

- “电影感”：`cinematic composition, dramatic lighting, depth of field`，再结合明确景别和光源。
- “压迫感”：`low angle, close framing, looming, strong contrast, deep shadows`。
- “梦幻”：`soft lighting, pastel colors, glowing particles, mist, ethereal atmosphere`。
- “速度感”：`dynamic pose, motion blur, wind, flowing clothes, dutch angle`。
- “孤独”：`solo, wide shot, negative space, distant background, muted colors`。

这些是候选映射，不是固定套餐。每次选择 2–4 个真正支持用户意图且已确认有效的标签。抽象意象需要空间关系时，构图信息优先于继续叠加 `sad`、`depressed`、`expressionless` 等近义情绪标签。

## 3. 景别与视角

常用景别：

- `close-up`：面部和局部情绪。
- `upper body` / `portrait`：人物上半身。
- `cowboy shot`：约大腿以上，兼顾人物与动作。
- `full body`：完整姿态和服装。
- `wide shot` / `very wide shot`：环境叙事和尺度感。

常用视角与镜头：

- `from above`, `from below`, `from side`, `from behind`
- `low angle`, `high angle`, `dutch angle`
- `fisheye`, `wide-angle lens`, `foreshortening`
- `pov`, `looking at viewer`, `looking away`, `looking afar`
- `depth of field`, `sharp focus`, `foreground blur`

每次优先选一个主景别和一个主视角。除非用户要求特殊画面，不混用互斥镜头。

## 4. 构图

资料中反复出现的稳定构图手段包括：

- 对称与中心：`symmetrical composition, centered composition`。
- 对角线与动势：`diagonal composition, dynamic angle, dutch angle`。
- 留白与孤立：`negative space, off-center composition`。
- 层次：明确 `foreground / midground / background` 中的物体。
- 镜像与反射：`mirror, reflection, water reflection`。
- 双人关系：`back-to-back, facing each other, side by side`。
- 分割：`split screen, divided composition`，仅在用户真的需要并列场景时使用。
- 水面边界：`half underwater, waterline`，同时指定水上和水下元素。

构图标签应服务主体，不要一次堆叠多个互相抢夺画面的模板。

## 5. 光照与色彩

先确定主光逻辑，再加氛围效果：

- 方向：`backlighting, side lighting, rim lighting, window light`。
- 明暗：`chiaroscuro, hard shadows, soft shadows, high contrast`。
- 空气：`volumetric lighting, light rays, dappled sunlight, fog`。
- 色彩：`blue theme, warm colors, limited palette, monochrome, complementary colors`。
- 时间：`golden hour, sunset, night, moonlight`。

不要同时要求柔和均匀光和极端硬阴影，除非明确区分主光与环境光。

## 6. 多人物

当前自动化使用 NovelAI V4 原生 `char_captions`，但不接受 `char1:`、`char2:` 文本字段：

1. 主 Prompt 写准确总人数、共享动作、场景、镜头、光照和总体构图。
2. 没有人物库占位符时，用位置、服装颜色、发色或动作区分人物，并用 `facing each other`、`holding hands` 等明确关系短语连接。
3. 有人物库占位符时，固定身份与服装由插件注入；规划器只在对应 `character_prompts` 值中写本图动作、互动、表情、姿势和视线。
4. 互动使用 V4 角色动作语法，例如双方 `mutual#hug`，或主动方 `source#pushing`、被动方 `target#pushing`；去掉前缀后的动作仍须是现行 Danbooru tag。
5. 多人信息密度高，应减少次要装饰，优先保证人数、角色分工、位置和互动结果。

## 7. 严格标签压缩

将中文描述转成标签时：

1. 删除叙事连接词、不可见原因和评价；心理活动转成可见信号，但保留原文已经给出的空间、物质和动作隐喻。
2. 保留名词、动作、空间关系、材质、颜色和光照。
3. 合并同义项，选择更具体的表达。
4. 把复杂空间关系拆成多个现行 Danbooru 标签；不保留英文自然语言片段。
5. 最后按主次顺序重排，而不是照着中文句序逐词翻译。

视线和道具不能用可读英文代替精确标签。“眺望远方”使用 `looking afar`；“盒装饮料”使用 `juice box`，手持动作另用 `holding drink`。不要输出 `looking at distance`、`drink box`、`juice pack` 或合并词 `holding juice box`。

例如“她在雨夜车站等人，心里很失落”可以落实为：单人、站台、雨夜、等待姿态、低垂视线、湿衣、冷色、远景和留白；不要直接把“心里很失落”当作抽象标签堆入。相反，“沉入悲伤之海”已经给出可见空间和动作，应落实为水下、海、下沉、漂浮状态和相容表情，不能按不可见心理活动整体删除。
