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

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│  外贸助手业务层（本仓库）                            │
│  客户识别 / 订单提取 / AI 摘要 / TODO 提醒           │
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

## 成本（月费 < 100 元，实测仅需 1-12 元）

| 方案 | ASR 引擎 | LLM | 月费（500分钟） | 适用场景 |
|---|---|---|---|---|
| A（推荐） | 本地 MLX Whisper medium | DeepSeek | **≈1 元** | M3 主力机，重视隐私 |
| B | 火山豆包 ASR 2.0 | DeepSeek | **≈8 元** | 跨 Mac/Win，开箱即用 |
| C | gpt-4o-mini-transcribe | DeepSeek | **≈12 元** | 最高准确率 |

详见 [docs/02-语音识别方案选型.md](docs/02-语音识别方案选型.md)

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 复制配置
cp src/config/config.example.yaml src/config/config.yaml
# 编辑 config.yaml 填入 DeepSeek API Key、微信路径等

# 启动（准实时模式）
python src/main.py --mode realtime

# 手动同步一次
python src/main.py --mode manual

# 仅转写某条语音
python src/main.py --transcribe <silk文件路径>
```

## 目录结构

```
international-trade-tools/
├── docs/                       # 设计文档
│   ├── 01-系统架构设计.md
│   ├── 02-语音识别方案选型.md
│   ├── 03-微信数据解析方案.md
│   └── 04-LLM需求分析设计.md
├── src/
│   ├── config/                 # 配置
│   │   └── config.example.yaml
│   ├── wechat_parser/          # 微信解析（基于 wechat-decrypt 二开）
│   │   ├── decryptor.py        # 数据库解密
│   │   ├── monitor.py          # 准实时监听
│   │   ├── message_extractor.py# 消息提取
│   │   └── silk_decoder.py     # 语音 SILK→WAV
│   ├── asr/                    # 语音识别
│   │   ├── base.py             # ASR 抽象接口
│   │   ├── mlx_whisper_asr.py  # 本地 MLX Whisper（M3）
│   │   └── volcengine_asr.py   # 火山豆包（云端）
│   ├── llm/                    # 大模型需求分析
│   │   └── deepseek_analyzer.py# DeepSeek 客户需求提取
│   ├── reminder/               # TODO 提醒
│   │   └── todo_manager.py     # 已办/待办/提醒
│   ├── storage/                # 存储
│   │   └── store.py            # SQLite 游标持久化
│   └── main.py                 # 入口
├── tests/
└── requirements.txt
```

## ⚠️ 法律与合规

- 仅可解析**本人或已获书面授权的员工**的微信数据
- 微信数据解析工具存在被腾讯 DMCA/律师函下架风险（PyWxDump、wechat-backup 已下架），建议内部 fork 备份，不公开传播
- 微信 4.1.x 内存扫描已失效，需 DLL 注入方案，有封号风险，建议小号测试
- 详见 [docs/03-微信数据解析方案.md](docs/03-微信数据解析方案.md) 风险提示章节
