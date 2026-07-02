# 外贸客户管理工具（International Trade Tools）

基于微信数据解析的 AI 辅助外贸助手，帮助外贸业务员自动处理客户沟通，提取需求，推进 TODO。

## 核心能力

1. **微信聊天记录准实时解析**（可开关 / 自动 / 手动）
   - 文字消息：直接提取
   - 语音消息：SILK → WAV → 西语/普通话转文字
   - 图片消息：解密 .dat 文件
2. **AI 客户需求分析**：转写文本 → DeepSeek 提取需求要点 → 结构化输出
3. **TODO 推进提醒**：汇总已办/待办事项，按天（或小时）提醒推进
4. **西语 + 普通话双语**：支持各国西语口音（西班牙/墨西哥/阿根廷/哥伦比亚等）

## 快速开始

### 方式一：打包为桌面应用（推荐，开箱即用）

```bash
git clone https://github.com/hustlianxu/international-trade-tools.git
cd international-trade-tools
```

**macOS**（在 Mac 上执行）：
```bash
bash build_mac.sh
# 生成 dist/外贸助手.app，双击运行
```

**Windows**（在 Windows 上执行）：
```
双击 build_windows.bat
# 生成 dist\TradeTools\TradeTools.exe，双击运行
```

> 详细步骤见 [docs/05-客户使用手册.md](docs/05-客户使用手册.md)

### 方式二：开发模式直接运行

```bash
pip install -r requirements.txt
python src/gui_app.py          # 启动 GUI
# 或命令行:
python src/main.py --mode realtime    # 准实时监听
python src/main.py --reminder         # 生成待办提醒
```

## 配置

首次运行会在用户目录自动创建配置文件：

| 系统 | 路径 |
|---|---|
| Windows | `%APPDATA%\trade-tools\config.yaml` |
| macOS | `~/Library/Application Support/trade-tools/config.yaml` |

也可在 GUI 的「配置」页直接编辑。需填入：
1. **微信 db_storage 路径**（微信数据目录）
2. **DeepSeek API Key**（必须，月费<1元，[获取](https://platform.deepseek.com/)）
3. **ASR 引擎 Key**（按引擎选一个）

## 成本（月费 < 100 元，实测 1-12 元）

| 方案 | ASR 引擎 | LLM | 月费（500分钟） | 适用场景 |
|---|---|---|---|---|
| A（推荐 Mac） | 本地 MLX Whisper medium | DeepSeek | **≈1 元** | M3 主力机，重视隐私 |
| B（推荐 Win） | 火山豆包 ASR 2.0 | DeepSeek | **≈8 元** | 跨 Mac/Win，开箱即用 |
| C | gpt-4o-mini-transcribe | DeepSeek | **≈12 元** | 最高准确率 |

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│  GUI 桌面应用（Tkinter，跨平台开箱即用）              │
│  配置页 / 准实时监听 / 语音转写 / 待办事项            │
├─────────────────────────────────────────────────────┤
│  ASR 层：本地 MLX Whisper (M3) / 火山豆包 (跨平台)   │
│  LLM 层：DeepSeek V4-Flash（月费 < 1 元）            │
├─────────────────────────────────────────────────────┤
│  微信解析层：fork ylytdeng/wechat-decrypt（二开）    │
│  WAL 监听 + 增量解密 + 游标持久化（延迟 ~100ms）     │
├─────────────────────────────────────────────────────┤
│  密钥层：4.0.x 内存扫描 / 4.1.x wx_key DLL 注入      │
├─────────────────────────────────────────────────────┤
│  PC 微信 4.x（外贸业务员日常工作环境）               │
└─────────────────────────────────────────────────────┘
```

## 目录结构

```
international-trade-tools/
├── docs/                          # 设计文档与使用手册
│   ├── 01-系统架构设计.md
│   ├── 02-语音识别方案选型.md
│   ├── 03-微信数据解析方案.md
│   ├── 04-LLM需求分析设计.md
│   └── 05-客户使用手册.md         # ← 用户必读
├── src/
│   ├── gui_app.py                 # GUI 主应用（Tkinter）
│   ├── main.py                    # CLI 入口
│   ├── paths.py                   # 跨平台路径管理
│   ├── processor.py               # 消息处理（语音→文字→分析→TODO）
│   ├── config/                    # 配置模板
│   ├── wechat_parser/             # 微信解析（解密/监听/提取/SILK解码）
│   ├── asr/                       # 语音识别（MLX/火山/OpenAI）
│   ├── llm/                       # DeepSeek 需求分析
│   ├── reminder/                  # TODO 管理与提醒
│   └── storage/                   # SQLite 存储
├── tests/
│   └── test_core.py               # 核心逻辑单元测试（19项）
├── trade-tools.spec               # PyInstaller 打包配置
├── build_mac.sh                   # macOS 一键打包
├── build_windows.bat              # Windows 一键打包
└── requirements.txt
```

## 测试

```bash
python tests/test_core.py
# 19 项测试覆盖：SILK解码/路径管理/存储CRUD/TODO管理/DeepSeek分析/配置加载
```

## ⚠️ 法律与合规

- 仅可解析**本人或已获书面授权的员工**的微信数据
- 微信数据解析工具存在被腾讯 DMCA/律师函下架风险，建议内部 fork 备份，不公开传播
- 微信 4.1.x 内存扫描已失效，需 DLL 注入方案，有封号风险，建议小号测试
- 详见 [docs/05-客户使用手册.md](docs/05-客户使用手册.md) 第 8 节
