# 发布节奏与版本管理（2026-08-25 建立）

## 版本管理

- 版本号唯一权威来源：仓库根 `VERSION`（当前 3.3.0）
- 变更规则：策略规则/组合结构 → minor；数据/引擎修复/文档 → patch；推翻性结论 → major
- 每次升版本必须同步 `CHANGELOG.md` + `docs/04-最终方案-vX.Y.md`
- 已定稿的"研究记录"（未改变策略规则的验证结论）只进 CHANGELOG，不升版本

## 推送节奏（2026-08-25 起）

- **本地 commit**：随时提交（工作留痕，不 push）
- **GitHub push**：**每周一次**（默认周日 20:00，由 ZCode 定时任务执行）
- 紧急修复（生产脚本报错、数据事故）不受此限，可即时 push
- 推送范围：`E:/workspace/etf-quant`（main）+ `E:/dev-harness`（master）两个仓库
- 定时任务执行命令示例：
  ```
  cd E:/workspace/etf-quant && git add -A && git commit -m "weekly: $(date +%Y-%m-%d) 聚合提交" && git push origin main
  cd E:/dev-harness && git add -A && git commit -m "weekly: $(date +%Y-%m-%d) 聚合提交" && git push origin master
  ```
  （无改动时 commit 会报 nothing to commit，属正常，跳过即可）
