# AstrBot NovelAI Opus API 插件

插件通过 NovelAI 官方图片 API 和 Persistent API Token 生成图片，不需要 Chrome、Playwright、浏览器 Cookie 或账号密码。

Windows 默认从插件数据目录的 `novelai_pat.dpapi` 读取由当前用户 DPAPI 加密的 PAT；Linux、容器和远程服务器通过 `NOVELAI_API_TOKEN` 环境变量提供 PAT。PAT 不得提交到 Git、日志或聊天。

## 安装与凭据

将 [`yzxzc/astrbot_plugin_novelai`](https://github.com/yzxzc/astrbot_plugin_novelai) 克隆或解压到 AstrBot 的 `data/plugins/astrbot_plugin_novelai`，安装依赖后重启 AstrBot。AstrBot WebUI 安装插件时会自动读取 `requirements.txt`。

Windows 安装完成后，在 AstrBot 根目录执行以下命令。脚本使用无回显输入，并把 PAT 通过当前 Windows 用户的 DPAPI 加密后写入插件数据目录：

```powershell
python data/plugins/astrbot_plugin_novelai/scripts/configure_pat.py
```

如果插件不位于标准目录，可显式指定 AstrBot 数据目录：

```powershell
python scripts/configure_pat.py --astrbot-data-dir D:\AstrBot\data
```

Linux、systemd 和容器不使用 DPAPI。请通过服务环境或密钥管理功能传入变量，并确保变量实际进入 AstrBot 进程：

```bash
export NOVELAI_API_TOKEN='pst-your-token'
astrbot run
```

Docker Compose 示例：

```yaml
services:
  astrbot:
    environment:
      NOVELAI_API_TOKEN: ${NOVELAI_API_TOKEN}
```

不要把真实 PAT 写进 `compose.yaml`、`.env`、插件配置、Issue 或日志。发布仓库已通过 `.gitignore` 排除常见凭据和运行数据，但部署者仍应使用平台提供的 Secret 管理能力。

自然语言 Prompt 规划还需要在 AstrBot 中配置一个可用的 LLM Provider，并把 `prompt_planner_provider_id` 改成该 Provider 的实际 ID。没有 DeepSeek Provider 时，可关闭 `prompt_planner_enabled`，现成 NovelAI 标签 Prompt 仍可直接生成。

严格标签校验默认开启。管理员安装后先执行 `/nai 更新词库`，插件会下载一次约 3.3 MB 的每日 Danbooru 标签快照并转换成插件数据目录内的 SQLite；之后每次生成只查本地数据库，不会逐请求访问 Danbooru。需要让 QQ 附图参与 Prompt 规划时，再执行 `/nai 更新Tagger` 显式下载约 470 MB 的 WD SwinV2 ONNX 模型。Tagger 在 CPU 上按需加载并复用，不会加载本机 Qwen 或 CLIP。

图片反推模型固定到 Apache-2.0 的 [`SmilingWolf/wd-swinv2-tagger-v3`](https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3) 发布版本；它支持 general、character 与 rating 分类，插件只保留 general/character 结果并再次通过本地 Danbooru SQLite 过滤。模型文件属于上游项目，不会随本插件仓库分发。

Tagger 默认使用 `0.30` 的 general 阈值和 `0.85` 的 character 阈值。ONNX Runtime 固定为单会话、顺序执行、4 个算子线程和 1 个图间线程，以避免自动线程池在长期 Bot 进程中产生延迟尖峰；`requirements.txt` 会安装 Pillow、NumPy 与 ONNX Runtime，但模型仍需管理员执行 `/nai 更新Tagger` 后单独下载。

仓库内的 [`prompt-planner`](prompt-planner) 是同一套 Prompt Skill 的独立 CLI/HTTP 版本。它可以使用单独的 DeepSeek API Key 运行，不依赖 AstrBot、NapCat、QQ 或 NovelAI 凭据；当前插件尚未改为强制依赖该服务。

## 权限

- 私聊 `/nai`、`/nai_status` 只允许 `allowed_sender_ids` 中的 QQ。
- 群聊默认关闭。开启 `allow_group=true` 后，还必须把群号加入 `allowed_group_ids`。
- 白名单群内所有成员都可执行普通 `/nai`、`/nai_status` 指令，也可以维护和使用本群共享画师串。
- 群白名单不会提升成员权限；AstrBot 管理员专属指令仍会单独拒绝普通成员。
- `bug_report_admin_ids` 默认留空；需要 Bug 反馈私聊通知时，应由部署者自行配置接收者。

## 指令

```text
/nai help
/nai_status
/nai 生成 一位银发蓝眼的成年女性站在雪夜街道，半身，冷色背光
/nai 生成 把参考图改成手机自拍风格  # 同条消息附图，或回复一张图
/nai 重抽
/nai bug反馈 <问题描述>
/nai 切换画师串 <串名称>|默认|原生
/nai 添加画师串 柔和线稿 artist:example, soft lineart
/nai 画师串
/nai 查看画师串 柔和线稿
/nai 负面
/nai 负面 lowres, extra fingers
/nai 负面 清空
/nai 创建人物 霜音 1girl, silver hair, blue eyes, long ponytail, black coat --负面 extra fingers, bad hands
/nai 确认
/nai 人物
/nai 人物 霜音
/nai 切换大小 自动
/nai 切换大小 竖图
/nai 切换大小 横图
/nai 切换大小 方图
/nai 自定义大小 768x1024
/nai 更新词库              # 管理员
/nai 更新Tagger            # 管理员，首次约 470 MB
```

画师串库按群保存并保存在插件数据目录的 `artist_strings.json`，同群成员可以添加、查看、覆盖和使用同一个库。当前画师串按“群号 + QQ 号”绑定，因此成员之间、不同群之间都不会互相切换。未选择时自动使用全局默认的 `千代noob` 快照；`/nai 切换画师串 默认` 恢复该全局默认，`/nai 切换画师串 原生` 才是不添加画师串。生成尺寸仍按 QQ 号保存。

`/nai 生成` 会自动区分输入类型：自然语言描述由独立的 `deepseek/deepseek-v4-flash` 规划为 NovelAI V4.5 Prompt；已经包含标签列表、画师字段、权重语法、下划线标签或 `1girl` 等特征的纯文本 Prompt 会跳过 DeepSeek 并原样直通。规划调用不带群聊历史，并严格返回 JSON；插件会拒绝无效 JSON、画师字段、多角色编辑器字段、自然语言伪 tag，以及本地 SQLite 中不存在、作品数过低或属于画师分类的标签。规划器不会要求用户补充年龄或擅自添加 adult。规划完成后，插件才把当前群画师串或全局默认画师串作为前缀拼接；显式选择原生画风时不添加前缀。

`/nai 生成 <要求>` 可以在同一条 QQ 消息附一张图，也可以回复一张图。插件只接受一张参考图，先由本地 WD SwinV2 Tagger 反推出 Danbooru tags，再经本地词库过滤；自然语言要求会让 DeepSeek 以“用户修改优先、Tagger 结果为可见证据”的方式重新规划。若用户提供的是现成标签 Prompt，则参考图 tags 会作为前缀直接合并，原标签部分不交给 DeepSeek。图片不会上传给 DeepSeek，也不会作为 Img2Img、Vibe 或 Precise Reference 发送给 NovelAI。

当“画师/画家”是画面人物而不是风格名称时，插件会校验并补齐绘画动作、画笔、画布和画架等职业视觉锚点，避免模型只输出 `painter` 或 `beret`。用户明确要求不作画、空手或不带画具时不会强制补齐。

规划器使用内容优先的分级扩写：简短输入优先补全主体、服装结构、材质、配色、装饰、动作和表情；具体输入以精确保留为主。镜头、背景和复杂光影只在用户提出或动作成立需要时加入，避免自动场景套餐稀释角色设计。人物库 Prompt 仍由占位符保护，不会被 DeepSeek 改写；补全只发生在人物的本次画面表现上。API 已启用 NovelAI Quality Tags，因此规划器不重复生成质量词。

Q版/chibi 等强视觉模式使用精简扩写。Q版请求会确定性保留现行标签 `chibi`，移除旧别名 `super deformed`，并抑制自动产生的写实比例与摄影标签，避免普通补全逻辑稀释 Q版造型。

`/nai 重抽` 按“群号 + QQ 号”读取上一次成功提交给 NovelAI 的完整 Prompt，跳过 DeepSeek 并原样重新生成。记录包含当时已经拼接的画师串和人物 Prompt，失败请求不会覆盖记录；尺寸使用该用户执行重抽时的当前设置。

`/nai bug反馈 <问题描述>` 会生成形如 `NAI-000001` 的反馈编号，先把提交时间、群号、QQ 和问题描述持久化到插件数据目录的 `bug_reports.json`，再私聊通知 `bug_report_admin_ids`。私聊通知失败不会丢失本地记录。

`/nai 负面` 查看当前 QQ 在当前群的基础负面提示词，`/nai 负面 <内容>` 设置，`/nai 负面 清空` 恢复空白。负面提示词按“QQ + 群/私聊”隔离，不会发送给 DeepSeek，也不会影响同群其他成员。

人物库保存在插件数据目录的 `characters.json`，按群共享。`/nai 创建人物 <角色名> <Prompt> [--负面 <内容>]` 会新建人物或发起同名覆盖确认，角色名必须是无空格的 2–40 个字符；人物 Prompt 不要求任何年龄、性别或人数标记，建议只写稳定身份、外观与服装，不写动作、场景、画师或质量词。可选的 `--负面` 内容会写入 NovelAI V4 对应人物的负面 caption。`/nai 人物` 列出名称，`/nai 人物 <角色名>` 查看完整正面和负面 Prompt。

创建新人物会立即保存。重复提交同名人物时不会立刻覆盖，而是暂存本次新 Prompt，并提示在 60 秒内发送 `/nai 确认`；确认按“群号 + QQ 号”隔离，只能由发起者在原群完成。超时、AstrBot 重启或发起者提交新的创建请求后，旧确认状态失效。确认前若该人物已被其他成员修改，本次确认也会失效，避免覆盖更新后的内容。

生成描述命中已保存角色名时，插件先把名字替换为受保护的 `__NAI_CHARACTER_SLOT_数字__`。DeepSeek 只能围绕槽位规划动作、互动、表情和姿势，并必须把每个槽位原样作为 `character_prompts` 的键返回一次；插件校验后才把人物库 Prompt 与本次动态 Prompt 合并为 NovelAI V4 原生 character captions。人物库内容不会发送给 DeepSeek，也不会被翻译、删减或重新加权。默认单次最多命中 4 个不同人物。

规划模型的规范位于 `skills/novelai-prompt-planner`。`prompt_planner_enabled=false` 可恢复原样提交；`prompt_planner_provider_id` 可切换独立规划 Provider。当前默认使用 Flash，以降低 QQ 指令等待时间。即使 NovelAI API 生成是 0 Anlas，DeepSeek Prompt 规划仍按 DeepSeek API 计费。

`/nai_status` 是隐藏诊断指令，不显示在普通 `/nai help` 中；管理员执行帮助时会在私聊管理员列表中看到它。状态会立即显示本地生成中、等待和总队列数，以及 Prompt 规划 Provider、NovelAI 绘图模型、当前画风和负面提示词；订阅查询不占用生成队列。

专用绘图 Bot 建议关闭 AstrBot `provider_settings.enable` 和空白 @ 等待回复。插件通过 `context.llm_generate()` 直接调用配置的 Prompt 规划 Provider，因此关闭默认聊天链路不会关闭 DeepSeek Prompt 规划。`/nai生成`、`/naihelp` 等缺少分隔空格的格式会被插件拦截并返回一行用法提示，不会落入默认聊天模型。

大小预设对应 NovelAI NORMAL Portrait `832x1216`、Landscape `1216x832` 和 Square `1024x1024`。新用户默认使用自动模式：多人互动、宽场景、追逐或车辆等使用横图，头像、图标、贴纸或 Q 版等紧凑主体使用方图，其余回退竖图；同一请求始终只映射到这三个受保护预设。`/nai 切换大小 竖图|横图|方图` 和 `/nai 自定义大小` 会锁定尺寸，`/nai 切换大小 自动` 恢复自动。自定义宽高必须是 64 的倍数、分别位于 64 到 2048 之间，且总像素不得超过 `1024x1024`。不同 QQ 用户不会互相沿用尺寸。

所有成员执行 `/nai help` 时都会在当前会话显示普通指令。管理员还会额外通过 QQ 私聊收到仅管理员指令。

API 请求固定使用 NAI Diffusion V4.5 Full、Euler Ancestral、Guidance 5、Quality Tags、单张 PNG 和全新随机 Seed。默认 Steps 为 23，Undesired Content 留空，由各 QQ 用户按会话自行设置。发送前必须确认账号是有效 Opus，并强制总像素不超过 1,048,576、Steps 不超过 28、生成数量为 1，且请求不携带任何底图、参考图、Vibe 或 Img2Img 图像数据。

NovelAI API 响应中出现多张图片时，插件按实际像素面积选择最大的候选，并要求最终图片尺寸与请求尺寸一致。无完整主图时会报告错误，不会发送缩略图。

所有生成请求通过单实例队列静默依次执行。Prompt 规划和 API 生成共用同一队列，后发请求不会因为规划响应更快而插队；同一时刻只会有一个 NovelAI 请求。429 默认固定等待 5 秒后重试，最多重试 8 次，不做指数递增。等待期间它继续占用队首，其他本地请求不会插队；超时、5xx 和其他未知错误不会自动重试，以免服务端已生成但客户端重复提交。成功生成只回复图片，规划、认证、网络、HTTP 或图片解析失败时只回复错误信息。

生成结果保存在 `data/plugin_data/astrbot_plugin_novelai/outputs`，Windows PAT 保存在同目录的 `novelai_pat.dpapi`。DPAPI 文件绑定当前 Windows 用户，不能直接复制到其他机器；远程部署应重新配置 `NOVELAI_API_TOKEN`。请勿复制、分享或提交任何 PAT。

群成员使用你的 NovelAI 账号可能涉及第三方使用限制。群功能保持默认关闭，启用前建议核对当前 NovelAI 条款或向官方支持确认。
