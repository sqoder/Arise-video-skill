# Fable Editorial + Google Flow 个人视频工作流指南（去 AI 味升级版）

本工作流已全面升级为 **暖纸编辑排版风（Zero-Blue Warm Editorial）**，提供 **双渲染引擎**，彻底剔除冷蓝/霓虹紫与假夜景大楼等常见「AI 味」视觉模板：

1. **引擎 1 · 口播实拍 + 极简编排合成（`fable_studio.py`）**：Google Flow (Veo) 暖钨丝光实拍底片 + Python/Pillow/OpenCV 弹簧物理动效、暖棉纸抠像贴纸、建筑风 3 轨时间轴转场 + FFmpeg 118 BPM 闪避混音。
2. **引擎 2 · 纯浏览器矢量宣传片（`web_stage/`）**：基于 **Playwright + Headless Chrome** 渲染 HTML5/CSS3/SVG 舞台（`stage_template.html` + `render_web_stage.py`），原生输出 Double-Bezel 双层圆角卡片、原生 `⌘⇧⌥` 键帽、CSS 精密字距与 `0.0%` 冷蓝像素占比，适合纯产品/UI 介绍片。

## 四大去 AI 味设计铁律（默认强制生效）

1. **Zero-Blue Rule（零冷蓝铁律）**：统一采用暖棉纸 `#FAF9F5` / `#F3F1EA`、暖碳黑 `#141413` / `#1C1B19`、陶土橘 `#D97757`、鼠尾草绿 `#788C5D` 与 `1px` 细线 `#DFDCD2`；Google Flow 提示词同步切换为暖钨丝主光 + 柔和琥珀轮廓光，禁用蓝紫霓虹光。
2. **建筑风 NLE 时间轴 B-Roll**：用 `1px` `#DFDCD2` 标尺网格、Double-Bezel 监看视口与 3 轨时间轴（`01 / VIDEO TRIM`、`02 / ZOOM · Z` 贝塞尔曲线、`03 / VOICE 48K`）替代假夜空大楼与霓虹高速公路。
3. **双渲染引擎并行**：口播类新闻用 `fable_studio.py`，纯软件/UI 发布片直接基于 `web_stage/stage_template.html` 与 `web_stage/render_web_stage.py` 定制舞台逐帧捕获。
4. **阻尼弹簧与量化质检**：关键弹窗/贴纸改用 `spring_ease(t, stiffness, damping)` 阻尼弹簧曲线；`verify` 自动统计图形卡片节拍（`#3 / #6 / #7`）的冷蓝像素占比 `ui_beat_cold_blue_pct`，配合 9 宫格预览图验片。

## 目录结构

- `project.json`：口播工作流唯一配置文件（品牌标识、画幅 `16:9` / `9:16`、数字人形象、敏感词自动替换规则、3~5 段口播脚本与动效文案）。
- `fable_studio.py`：口播合成引擎 CLI（支持 `init`、`flow-plan`、`render`、`verify`）。
- `web_stage/`：Playwright 浏览器矢量舞台模板（`stage_template.html`、`render_web_stage.py`）。
- `一键渲染成片.command`：macOS 双击一键脚本，自动生成 `flow_tasks.json`、合成成片与 9 宫格验收图并打开视频。
- `assets/`：默认主角形象锚点（`default_dark_anchor.png`、`default_green_anchor.png`），或放你自己的参考照片。
- `clips/`：从 Google Flow 下载的原始实拍片段（`seg_01.mp4`、`seg_02.mp4`、`seg_03.mp4` ...）。
- `output/`：导出的 `flow_tasks.json`、最终成片 `《今日AI大事件》成片.mp4`、9 宫格检查图 `verify_grid.jpg`。

---

## 三种常用用法（直接跟 Antigravity 说即可）

### 用法 1：一句话出片
> “用 fable-video-workflow 做一期《今日AI大事件》，重点讲 Claude 新功能、Gemini 更新和国内开源模型。”

Agent 会自动搜集最新资讯、写好分镜与合规口播词、调用 Google Flow 生成片段，并渲染成片。

### 用法 2：自带口播文案出片
> “用我的文案跑一遍 Fable 视频工作流：第一段……第二段……第三段……”

Agent 会直接将你的文案填入 `project.json` 并完成渲染。

### 用法 3：长文精读讲解（支持任意 Markdown 文章 / 1~20 分钟不限章节）
> “把这篇文章做成讲解视频：……”（或直接提供 `.md` 文件路径）

1. 自动转文章 Markdown 为多章节配置（含导读、原文划重点、架构流程图、代码逐行聚焦、Bento 对比、暖碳黑总结 6 大模板）：
   ```bash
   PY=${PYTHON_BIN:-python3}
   DIR="${STUDIO:-.}"

   $PY "$DIR/explainer_studio.py" from-markdown -i "$DIR/sample_article.md" -o "$DIR/explainer_project.json"
   $PY "$DIR/explainer_studio.py" render -p "$DIR/explainer_project.json" -o "$DIR/output/《文章精读讲解》成片.mp4"
   $PY "$DIR/explainer_studio.py" verify -v "$DIR/output/《文章精读讲解》成片.mp4" -o "$DIR/output/verify_explainer_grid.jpg"
   ```
2. 默认采用 `edge-tts` 中文神经语音合成（断网自动降级到 macOS 原生 `Tingting`），每章按音频精确秒数锁定渲染帧数，零音画漂移。

### 用法 4：换用你自己的照片 / 竖屏 `9:16` / 任意 N 段口播扩长版
1. 把你的正面半身照片放进 `assets/my_photo.jpg`。
2. 在 `project.json` 中设置：
   - `"aspect_ratio": "9:16"`（宽度 `1080`、高度 `1920`），或 `"16:9"`（`1920x1080`）。
   - `"segment_count"` 支持任意 `>= 2` 段（命令行：`init --segments 6 --aspect 9:16`）。
   - `"persona.mode": "custom"`，并填写 `"dark_anchor_path": "assets/my_photo.jpg"`。
3. 运行 `flow-plan` 拿到防拦截提示词去 Flow 生成 `clips/seg_01.mp4 ...`，或直接让 Antigravity 帮你跑完。

---

## 命令行常用操作

> 注意：请使用预装了 `cv2`/`numpy`/`PIL` 的 Python 3.10+ 环境执行脚本。

```bash
PY=${PYTHON_BIN:-python3}
DIR="${STUDIO:-.}"

# 1. 初始化一套 9:16 竖屏 4 段模板到新配置
$PY "$DIR/fable_studio.py" init -o "$DIR/project_9x16.json" --aspect 9:16 --segments 4

# 2. 从 project.json 生成已做风险规避的 Google Flow 提示词清单
$PY "$DIR/fable_studio.py" flow-plan -p "$DIR/project.json" -o "$DIR/output/flow_tasks.json"

# 3. 合成成片（含实拍抠像、人后排版、3D镜头/时间轴、磨砂打字卡片、118 BPM 混音）
$PY "$DIR/fable_studio.py" render -p "$DIR/project.json" -o "$DIR/output/《今日AI大事件》成片.mp4"

# 4. 校验成片并输出 9 宫格节拍检查图
$PY "$DIR/fable_studio.py" verify -v "$DIR/output/《今日AI大事件》成片.mp4" -o "$DIR/output/verify_grid.jpg"
```

---

## Google Flow 避坑与风控规则

1. **角色与台词脱敏**：Google Flow 对真实媒体记者名义（`新闻记者`）、股票市场术语（`IPO`、`上市`、`股票`）及部分英文商业品牌词容易触发审核。`flow-plan` 会自动把 `spoken_text` 里的敏感词替换成自然谐音/泛化中文（如 `OpenAI -> 智能对话平台`、`Claude -> 克劳德`），**仅用于让 Flow 说出口播音频与口型**；画面中的真实英文/中文视觉标题仍按你在动效字段写的原文高清渲染，两全其美。
2. **严禁 Flow 内嵌字幕**：所有提示词默认附带 `STRICT NO ON-SCREEN TEXT`，保证后期 Python 矢量排版与人后穿梭文字干净利落。
3. **人物一致性**：每段都在 Flow 里附加同一个参考形象素材（`default_dark_anchor.png` / `default_green_anchor.png` 或你的自拍照片），切勿跨段裸跑。
