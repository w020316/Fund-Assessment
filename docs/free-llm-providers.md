# 免费大模型 API 接入清单（QuantFlow Pro）

> 核实日期：2026-10-27。免费政策随时会变，**上线前以本文「核实来源」列的官方页面为准**。
> 表中「官方确认」= 官方文档/定价页直接写明；「第三方」= 仅第三方文章提及，未在官方页面上复核。

## 一、怎么用（三步）

1. 到对应平台注册，创建 API Key（部分平台需实名）。
2. 在本机 `.env` 或 Render 控制台（Environment）里填对应环境变量，例如：
   ```
   ZHIPU_API_KEY=xxxx
   QIANFAN_API_KEY=xxxx
   GROQ_API_KEY=xxxx
   ```
   支持多 Key 轮换：`XXX_API_KEYS=key1,key2,key3` 优先于 `XXX_API_KEY`。
3. 重启服务后查看状态，或做一次连通性自检：
   ```bash
   curl -s https://fund-assessment.onrender.com/api/health/llm | jq '.free_providers'
   curl -s -X POST "https://fund-assessment.onrender.com/api/health/llm/probe?provider=zhipu_glm" \
        -H "Authorization: Bearer $ADMIN_TOKEN" | jq
   ```
   `POST /api/health/llm/probe` 需要管理员令牌；不带 `provider` 参数会依次探测全部已配置 Provider。

**没配 Key 的 Provider 不会加载**，既不影响现有调用链，也不会报错。

## 二、国内（中国大陆）

| Provider | 环境变量 | 默认模型 | 免费额度 | 核实 | 核实来源 |
| --- | --- | --- | --- | --- | --- |
| 智谱 GLM | `ZHIPU_API_KEY` | `glm-4.7-flash` | GLM-4.7-Flash 输入/输出/缓存全免费，200K 上下文；另有 `glm-4.5-flash`(128K)、`glm-4.6v-flash`(128K，多模态) 同属免费 | 官方确认 | https://docs.bigmodel.cn/cn/guide/start/model-overview |
| 硅基流动 SiliconFlow | `SILICONFLOW_API_KEY` | `Qwen/Qwen2-7B-Instruct` | 7 个模型免费：`Qwen/Qwen2-7B-Instruct`、`Qwen/Qwen2-1.5B-Instruct`、`Qwen/Qwen1.5-7B-Chat`、`THUDM/glm-4-9b-chat`、`THUDM/chatglm3-6b`、`internlm/internlm2_5-7b-chat`、`mistralai/Mistral-7B-Instruct-v0.2`（均 32K），需实名；新用户另赠 ¥16 | 官方确认 | https://docs.siliconflow.com/quickstart/models |
| 讯飞星火 Lite | `SPARK_API_KEY` | `lite` | Lite 免费；OpenAI 兼容地址 `https://spark-api-open.xf-yun.com/v1`，`Authorization: Bearer <APIPassword>` | 官方确认 | https://www.xfyun.cn/doc/spark/HTTP调用文档.html |
| 百度千帆 | `QIANFAN_API_KEY` | `ernie-speed-128k` | ERNIE-Speed / ERNIE-Lite 永久免费，免费档 QPS≈1；OpenAI 兼容地址 `https://qianfan.baidubce.com/v2`，`Authorization: Bearer <API Key>`；新用户另有每模型 100 万 tokens（3 个月） | 官方确认（模型 ID 与地址）+ 第三方（永久免费表述） | https://cloud.baidu.com/doc/qianfan/s/rmh4stp0j |
| 阿里云百炼 | `DASHSCOPE_API_KEY` | `qwen-turbo` | 新用户每模型约 100 万 tokens、90 天，**不是永久免费** | 官方确认 | https://help.aliyun.com/zh/model-studio/new-free-quota |

## 三、海外

| Provider | 环境变量 | 默认模型 | 免费额度 | 核实 | 核实来源 |
| --- | --- | --- | --- | --- | --- |
| OpenRouter | `OPENROUTER_API_KEY` | `google/gemma-4-31b-it:free` | 免费模型 20 RPM；账户余额 <10 credits 时 50 请求/日，≥10 credits 时 1000 请求/日。**免费模型 ID 会轮换**，可设 `OPENROUTER_MODEL=openrouter/free` 自动路由到当日免费模型 | 官方确认 | https://openrouter.ai/docs/api-reference/limits · 免费模型清单 `curl https://openrouter.ai/api/v1/models` 过滤 pricing=0 |
| Groq | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | 免费档约 30 RPM / 1000 请求/日（随模型不同） | 第三方（官方限流页对抓取返回 403） | https://console.groq.com/docs/rate-limits |
| Cerebras | `CEREBRAS_API_KEY` | `gpt-oss-120b` | 免费档 5 RPM、100 万 tokens/日 | 官方确认 | https://inference-docs.cerebras.ai/support/rate-limits |
| Google Gemini | `GEMINI_API_KEY` | `gemini-2.0-flash` | 有 Free Tier，但**逐模型限额已不再公开**，需登录 AI Studio 查看；第三方实测 2.5 Flash 约 20 请求/日量级 | 官方确认有免费档 / 限额待核实 | https://ai.google.dev/gemini-api/docs/pricing |
| Agnes | `AGNES_API_KEY` | `agnes-2.0-flash` | 平台自称全模态永久免费（本项目已在用） | 第三方 | https://apihub.agnes-ai.com |

## 四、待核实（未接入）

以下平台常被列为「免费」，但本轮**没有在官方页面复核到明确条款**，因此没有写进代码：

- Mistral La Plateforme 免费档：官方 pricing/docs 页面多次抓取失败。
- 腾讯混元 `hunyuan-lite`：官方文档只提到并发限制与 TokenHub 购买，未确认免费。
- 火山方舟（豆包）：新用户赠送额度有时限，未确认长期免费模型。
- NVIDIA NIM / GitHub Models：需对应账号体系，额度条款未复核。

## 五、默认优先级（数值越小越先被调用）

```
zhipu_glm(-1, 用户指定优先) > ttapi(0) > gemini(2) > anthropic(3) > ollama(10)
> cloudflare_ai(65) > deepseek(70) > openrouter(75) > cerebras(78)
> groq(80) > qianfan_ernie(82) > aliyun_qwen(85) > spark_lite(88)
> agnes(90) > siliconflow(95)
```

智谱免费模型排在最前；注意 `ZHIPU_MODEL` 若在部署平台被显式设置，会覆盖代码默认值。
其余免费 Provider 排在付费/自建之后，只有在前序 Provider 全部不可用（未配置、熔断、限流超时）时才会被用到。

## 六、限流与熔断保护

- 每个 Provider 有独立令牌桶：智谱 30 RPM、硅基流动 1000 RPM、星火 30 RPM、千帆 60 RPM（对应 QPS 1）、Groq 30 RPM、Cerebras 5 RPM、OpenRouter 20 RPM。
- 连续 3 次失败熔断 30 秒后自动半开；`GET /api/health/llm` 可看 `circuit_open` / `fail_count`。
- `chat()` 命中响应缓存时不消耗任何额度（`GET /api/health/llm` 的 `response_cache` 字段）。

### 429 的语义（2026-10-27 修正）

智谱把限流分成两类：**1302 = 本账户并发超限**，**1305 = 平台服务过载**（与单个账户行为无关）。两者都返回 HTTP 429。官方建议 1305 时「拉开重试间隔，避免立即高频重试」（https://docs.bigmodel.cn/cn/api/rate-limit）。

因此 `chat()` 对 429 的处理是：**不原地重试，立即切换下一个 Provider**；仍计入熔断计数（防持续打满上游）。非 429 的瞬时错误（ConnectionError / 5xx）保持 1s→2s 指数退避重试。

> 线上实测（2026-10-27 12:26）：智谱免费档高峰期 429 时，旧逻辑 3 次原地重试全部失败，用户请求被拖到 **37.5s** 才降级；修正后降级耗时回到「两个 Provider 各一次请求」的量级。**429 不代表 Key 无效**——同一把 Key 在低峰期探测 `ok=true`。

