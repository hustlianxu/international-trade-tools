# 04 - LLM 需求分析设计

## 一、选型：DeepSeek V4-Flash

| 维度 | DeepSeek V4-Flash | 对比 GPT-4o |
|---|---|---|
| 输入价格 | 1 元/百万 tokens | ~15 元/百万 |
| 输出价格 | 2 元/百万 tokens | ~60 元/百万 |
| 缓存命中 | 0.02 元/百万 | N/A |
| 西语能力 | 优秀（130+ 语种） | 优秀 |
| 月费(500条) | **< 1 元** | ~50 元 |

**结论**：DeepSeek 性价比碾压，西语业务理解能力足够，月费 < 1 元。

## 二、分析流程

```
微信消息（文字 + 语音转写文本）
    ↓ 拼接为对话格式 [时间] 角色: 内容
DeepSeek 分析（JSON 输出）
    ↓
解析 needs / done_items / todo_items / customer_mood
    ↓
写入 SQLite + 触发 TODO 更新
```

## 三、Prompt 设计

### 3.1 系统角色
```
你是一位严谨的外贸业务分析助手，只输出 JSON，不要任何解释。
```

### 3.2 分析 Prompt 要点

1. **需求分类体系**（西语外贸用语映射）：
   - inquiry（询价）← cotización, precios
   - quotation（报价）← cotización, presupuesto
   - sample（样品）← muestra
   - order（订单）← pedido, orden
   - logistics（物流）← envío, entrega, transporte
   - payment（付款）← pago, transferencia, LC
   - complaint（投诉）← queja, problema

2. **输出结构**：
   ```json
   {
     "language": "es/zh/en",
     "summary": "整体摘要（中文）",
     "needs": [{"category", "summary", "product", "quantity", "deadline", "urgency"}],
     "done_items": ["已完成的动作"],
     "todo_items": ["需跟进的待办"],
     "customer_mood": "客户情绪"
   }
   ```

3. **待办事项要求**：具体可执行，每条是一个明确动作（如"回复客户关于交期的疑问"、"准备形式发票"）

## 四、对话拼接格式

```
[2026-07-02 10:30] 客户: Hola, necesito la cotización para 5000 unidades del modelo A-100
[2026-07-02 10:32] 我方: Hola, te envío la cotización en un momento
[2026-07-02 10:35] 客户: [语音] ¿Cuál es el tiempo de entrega para Cancún?
```

- 语音消息前缀 `[语音]` 标记（已转写）
- 时间戳帮助 DeepSeek 理解对话节奏
- 角色用中文"客户"/"我方"（让 DeepSeek 输出中文分析）

## 五、TODO 提醒机制

### 5.1 优先级排序

```
高优先级(urgent) → 超期待办 → 普通待办
```

- `urgency=high`：客户明确要求紧急、有截止日期
- 超期：创建超过 `overdue_days`（默认 3 天）未完成

### 5.2 提醒格式

```
📋 待办提醒 (2026-07-02 09:00)
==================================================
🔴 超期待办（2 项）:
  ⚠ [Carlos Mexico] 回复关于交期的疑问
     (已过 4 天)

🔥 高优先级（1 项）:
  ★ [María Argentina] 准备形式发票 截止:2026-07-03

📝 待办（5 项）:
  · [Pedro Spain] 发送样品追踪号
  · [Ana Colombia] 确认 LC 条款

--------------------------------------------------
👥 按客户汇总:
  Carlos Mexico: 3 项待办
  María Argentina: 2 项待办
```

### 5.3 调度

- `granularity: daily`：每天 `daily_time`（默认 09:00）提醒
- `granularity: hourly`：每小时 `hourly_minute`（默认 :00）提醒
- 用 `apscheduler` 实现（后台调度）

## 六、成本核算

| 项 | 计算 | 月费 |
|---|---|---|
| 输入 | 500条 × 300 tokens = 15万 tokens | 0.15 元 |
| 输出 | 500条 × 200 tokens = 10万 tokens | 0.20 元 |
| **合计** | | **≈ 0.35 元** |

即使用量翻 10 倍（5000条/月），月费也仅 3.5 元，远低于预算。

## 七、扩展性

- **LLM 可切换**：DeepSeek 可替换为 Qwen/GLM（同 OpenAI 兼容接口），改 config 即可
- **多模态扩展**：如需分析图片（如客户发产品照片），可后续接入 GPT-4o vision
- **自定义需求分类**：在 prompt 中调整 category 体系，适配不同外贸品类
