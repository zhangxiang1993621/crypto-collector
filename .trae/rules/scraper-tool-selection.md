# 爬虫工具选择规范

## 规则说明

在编写需要浏览器自动化的爬虫时，优先使用 **Playwright**，如果 Playwright 无法通过目标网站的反爬检测，则降级使用 **CloakBrowser**。

## 决策流程

```
开始
  │
  ▼
用 Playwright 访问目标网站
  │
  ├── 成功获取数据（HTTP 200 + 有效内容）
  │     └── ▶ 使用 Playwright 编写爬虫代码
  │
  └── 失败（超时 / 403 / 反爬拦截 / 无有效数据）
        └── ▶ 改用 CloakBrowser 编写爬虫代码
```

## 具体步骤

### 第一步：Playwright 探路

```python
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
browser = p.chromium.launch(headless=True)
page = browser.new_page()
page.goto("目标URL", timeout=20000)

# 检查：
# 1. 是否超时（>20s 无响应 → 失败）
# 2. HTTP 状态码是否正常（200 → 检查内容；403/429 → 失败）
# 3. 内容是否包含有效数据
```

### 第二步：判断结果

| Playwright 结果 | 决策 |
|-----------------|------|
| 能正常访问并获取有效数据 | **使用 Playwright** |
| 超时 / 403 / 429 / 反爬拦截 / 无有效数据 | **改用 CloakBrowser** |

### 第三步：编码

- 如果选了 Playwright：`from playwright.sync_api import sync_playwright`
- 如果选了 CloakBrowser：`from cloakbrowser import launch`

API 几乎相同（CloakBrowser 兼容 Playwright API），只需改 import 即可：

```python
# Playwright 版本
# from playwright.sync_api import sync_playwright
# pw = sync_playwright().start()
# browser = pw.chromium.launch(headless=True)

# CloakBrowser 版本（仅需改 import + 启动方式）
from cloakbrowser import launch
browser = launch(headless=True)
page = browser.new_page()
# 后续 page.xxx() API 完全一致
```

## 示例：本项目中的实际应用

`us_stock_scraper/us_stock_scraper.py` 在开发过程中：
1. 先尝试 Playwright 访问 `query1.finance.yahoo.com` → 超时
2. 再尝试 CloakBrowser 访问同一 URL → 成功（返回完整 JSON 数据）
3. 最终代码使用 CloakBrowser

## 依赖

两个库均已纳入 `requirements.txt`：
- `playwright>=1.40.0` — 首选
- `cloakbrowser>=0.3.0` — 备选
