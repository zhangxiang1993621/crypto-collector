# 项目文档索引

本目录是 Crypto Collector 的项目文档。根目录 [`README.md`](../README.md) 提供定位与快速开始，本目录提供深入说明。

## 阅读顺序

1. [`architecture.md`](architecture.md) —— 先了解系统分层、数据流与公共代码模式。
2. [`data-model.md`](data-model.md) —— 再了解数据表结构、直连方式与去重/upsert 语义。
3. [`scrapers.md`](scrapers.md) —— 按域查阅每个爬虫的入口、数据源、参数。
4. [`scheduling.md`](scheduling.md) —— 了解两种调度方式与配置同步机制。
5. [`operations.md`](operations.md) —— 部署、环境变量、运维脚本与故障排查。
6. [`conventions.md`](conventions.md) —— 新增/修改爬虫时必须遵守的规范。

## 文档职责与维护约定

修改代码时，请同步更新对应文档：

| 改动内容 | 需同步更新的文档 |
|---|---|
| 新增/删除爬虫、修改入口命令或参数 | [`scrapers.md`](scrapers.md)、根 `README.md` 摘要表 |
| 新增/修改数据表、字段、去重键 | [`data-model.md`](data-model.md) |
| 修改 `.github/workflows/scheduler.yml` 的 job/cron | [`scheduling.md`](scheduling.md) |
| 新增环境变量 | [`operations.md`](operations.md)、`.env.example` |
| 新增运维脚本（`tools/`） | [`operations.md`](operations.md) |
| 修改公共代码模式、发帖规范 | [`architecture.md`](architecture.md)、[`conventions.md`](conventions.md) |

## 写作约定

- 文档使用简体中文，技术名词（表名、字段、命令、库名）保留英文。
- 命令、路径、cron 均以代码/配置为准，不写未经核实的推测。
- **禁止在文档中写入真实密钥**；环境变量一律使用占位符。

## 历史文档

- [`superpowers/plans/2026-06-21-indonesia-exchange-scrapers.md`](superpowers/plans/2026-06-21-indonesia-exchange-scrapers.md) —— 印尼 6 家交易所爬虫的历史实施计划（已完成，保留作参考）。
- [`audit-2026-09-11.md`](audit-2026-09-11.md) —— 全仓库代码审计报告（6 领域并行只读审计，按 P0/P1/P2 分级）。
