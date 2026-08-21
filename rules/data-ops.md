---
last_updated: 2026-08-20
status: active
domain: etf-quant
abstract: "数据源/缓存/行情获取/批量请求规范"
globs: "**/*.py"
version: 1.0
changelog:
  - v1.0 | 2026-08-20 | feat | 沉淀腾讯数据源与缓存规范
---

# ETF 数据操作规范

## 一、数据源

| 用途 | 接口 | 说明 |
|------|------|------|
| 历史 K 线（前复权） | 腾讯 `web.ifzq.gtimg.cn/.../fqkline/get` | OHLCV 6 字段，缓存 `ic_cache/ohlcv_<code>.pkl` |
| 实时行情（单只） | 腾讯 `qt.gtimg.cn/q=sh513100` | 字段 [3]现价 [4]昨收；`get_realtime_price()` |
| 实时行情（批量） | 腾讯 `qt.gtimg.cn/q=sh513100,sz159819,...` | **批量 42 只单次请求**；`batch_realtime()` |
| 指数行情 | 新浪/腾讯（etf_quant_strategy.fetch_index_data） | 日报头部 |

## 二、缓存与数据质量

1. 缓存目录：`E:\autotest\autotest-script-devops\etf_scorer\ic_cache\`（final_strategy.py 引用）
2. 前复权为准（`qfq_*.pkl` 旧格式、`ohlcv_*.pkl` 新格式，注意区分字段）
3. 数据不足规则：打分需 60 日预热、bull_align 需 300+ 日；不足直接跳过，**不填默认值**
4. 行情拿不到显示 "—" 兜底，不阻塞主流程

## 三、批量行情

- 打分总表 42 只标的使用 `batch_realtime()` 单次请求（腾讯支持逗号分隔），**禁止**逐只 requests 拖慢日报
- 涨跌口径：`(现价/昨收 - 1) * 100`，与今日定投清单同一口径

## 四、代码约定

- 沪深代码映射：`"5"/"6"` 开头 → sh，其余 → sz
- 腾讯返回行格式：`v_sh513100="..."`，解析 `split("=")[0].split("_")[-1]` 取 symbol
- 编码：`r.encoding = "gbk"`；UA 必须带（腾讯/新浪都校验）

## 五、数据体检（新拉数据必跑）

1. 检查项：NaN 比例 >0.5%、日涨跌 >25%（A 股 ETF 跌停上限 10%，超限即疑似坏数据）、索引重复
2. 已知修正：512690（酒）2020-02-03 单日 -25.1% 系腾讯坏数据，已修正为 -10%（跌停假设）并同步 open/high/low
3. 数据停在旧日期（>7 天前）的标的需要重拉（fetch_full 按年分批）
