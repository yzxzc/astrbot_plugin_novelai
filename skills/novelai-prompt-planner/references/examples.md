# 规划范例

以下范例用于校准输入密度、内容优先级和信息量，不是固定模板。运行时 API 已启用 `qualityToggle`，输出不包含质量词。

## 短输入：完成角色本体

输入：

```text
可爱的女孩
```

输出：

```text
1girl, solo, medium hair, wavy hair, layered dress, puffy sleeves, high-waist skirt, pleated skirt, pastel colors, embroidery, bow, buttons, ankle socks, mary janes, light smile, blush, head tilt, looking at viewer, simple background
```

短输入的长度来自服装结构、材质、装饰、姿势和表情，不来自背景、摄影镜头、光影套餐或同义质量词。

## 多语言诗性输入：保留可视化主导意象

输入：

```text
悲しみの海に沈んだ私
目を開けるのも億劫
```

输出：

```text
solo, underwater, ocean, submerged, sinking, floating, closed eyes, exhausted, depressed, expressionless, floating hair, floating clothes, outstretched arms, bubble, blue theme, wide shot, from above, negative space, darkness, light rays
```

`私` 不提供性别依据，因此使用 `solo` 并省略性别主体标签。“悲伤之海”和“下沉”是可直接描绘的空间与动作隐喻，必须成为画面结构，不能只剩 `sad, depressed` 或被 `simple background` 覆盖。人物与巨大海洋之间的尺度、深度和方向决定了意象是否成立，因此用一个远景/俯视关系以及少量留白和水下光线表现；不是因为输入“悲伤”就固定附加这些标签。若输入只有“什么都不想做，只想蜷缩着发呆”这类心理与身体状态，则用姿态与表情落实，不凭空发明海、水下、远景或光线。

## 文化意象：拆成可见设计

输入：

```text
苗族少女
```

输出：

```text
1girl, solo, headdress, silver jewelry, hair ornament, necklace, bracelet, blue dress, embroidery, pleated skirt, tassel, standing, expressionless, simple background
```

不要只输出一个文化身份词，也不要把文化身份直译成自造英文短语。拆解为现行 Danbooru tag 后，模型即使不认识专名也能呈现主题。

输入：

```text
古风大侠少女
```

输出：

```text
1girl, solo, hanfu, chinese clothes, long sleeves, wide sleeves, layered skirt, waist sash, hair bun, chinese hairpin, jade (gemstone), pendant, sword, jian (weapon), sword tassel, holding sword, fighting stance, determined, looking ahead, simple background
```

不能输出 `ancient Chinese knight-errant`。它是概念翻译，不是这里要求的精确 Danbooru tag。

## 具体输入：忠实细化，不扩写场景

输入：

```text
穿白色礼服的女孩，方领，长袖，裙摆有珍珠
```

输出：

```text
1girl, solo, white dress, square neckline, long sleeves, long skirt, layered skirt, pearl, satin
```

只细化用户已经指定的礼服结构。用户没有要求配饰、姿势、表情、场景、镜头或光照，因此不添加这些内容。

## 用户已指定环境与镜头

输入：

```text
一位银发蓝眼女性穿白色长外套，夜晚站在下雪的街道上，冷色，半身镜头，背光。
```

输出：

```text
1girl, solo, grey hair, blue eyes, white coat, high collar, winter clothes, standing, upper body, snow, street, night, backlighting, blue theme
```

## 动作与低机位

输入：

```text
女剑士从废墟上跃下，披风被风吹起，低机位，动作感强，夕阳。
```

输出：

```text
1girl, solo, armor, pauldrons, holding sword, 1.3::jumping::, dynamic pose, foreshortening, cape, from below, ruins, sunset, wind, motion blur
```

## 双人物关系

输入：

```text
两位女性背靠背站在雨中的霓虹街道，全身。
```

输出：

```text
2girls, back-to-back, standing, full body, neon lights, street, rain, night, wet, reflection
```

## 人物库角色

输入：

```text
可爱的__NAI_CHARACTER_SLOT_1__
```

主 Prompt：

```text
solo, simple background
```

人物 Prompt：

```json
{"__NAI_CHARACTER_SLOT_1__":"light smile, blush, head tilt, looking at viewer"}
```

人物库已经提供身份、外观和服装，因此动态人物 Prompt 不能另行设计这些固定内容。

## 强媒介：保持 Q版

输入：

```text
Q版女孩正在吃冰淇淋
```

输出：

```text
chibi, 1girl, solo, holding ice cream, ice cream cone, eating, happy, simple background
```

不要加入写实身体比例、摄影镜头或复杂光影。

## 保持极简

输入：

```text
1girl，不要添加服装、背景、镜头和光影
```

输出：

```text
1girl, solo
```

## 冲突修正

输入：

```text
一个人，全身大特写，白天夜景，从上方仰拍。
```

不要静默输出互斥标签。若调用方支持错误协议，应返回 `conflicting_constraints`；若必须产出，则只按最后一个明确约束解析，并记录内部冲突，不在 Prompt 中同时保留两套互斥词。
