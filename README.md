# 海口日用消费品价格监测 — Edge 实时数据方案

零服务器、零成本、7×24 跑的实时价格数据方案。

## 架构

```
┌──────────────────────┐
│  GitHub Actions      │  每天 UTC 0:00(北京时间 8:00)自动跑
│  (cron)              │  
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  crawler/fetch.py    │  抓海口菜篮子 + 农业农村部
│                      │  清洗数据 → data/prices.json
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  GitHub Repository   │  提交 prices.json
│  (haikou-market-pulse/data)
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  jsDelivr CDN        │  全球缓存, 永久免费
│  cdn.jsdelivr.net    │
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  前端 HTML           │  fetch 这个 URL 即可
│  (已部署到公网)      │
└──────────────────────┘
```

## 数据源

| 来源 | 内容 | 频率 |
|---|---|---|
| 海口市政府「菜篮子每日价格」 | 22种蔬菜零售/批发价、白猪/黑猪均价、4区均价 | 每日 |
| 农业农村部「农产品批发价格200指数」 | 牛肉/羊肉/鸡蛋/白条鸡/水果/水产等 | 每日(可后续接入) |
| 静态参考价 | 大米/食用油/牛奶/水/电/气/油价 | 写入 base 即可 |

## 5 步部署

### 1. 创建 GitHub 仓库

在 https://github.com/new 创建一个新仓库,名字建议:`haikou-market-pulse-data`

仓库名**很重要**,会影响 jsDelivr URL,后面会用。

### 2. 上传本目录所有文件

把整个 `edge-solution/` 目录推上去:

```bash
cd edge-solution
git init
git add .
git commit -m "init: 海口物价爬虫"
git branch -M main
git remote add origin https://github.com/<你的用户名>/haikou-market-pulse-data.git
git push -u origin main
```

### 3. 等 GitHub Actions 跑一次

进入仓库的 **Actions** 标签页,等 `抓取海口物价数据` workflow 跑完(约 1 分钟)。

跑完后:
- `data/prices.json` 应该被自动更新
- 这是 GitHub 的提交记录,可以看到

### 4. 验证 jsDelivr URL

浏览器打开:

```
https://cdn.jsdelivr.net/gh/<你的用户名>/haikou-market-pulse-data@main/data/prices.json
```

应该看到 JSON 数据。

> **注意**:jsDelivr 第一次缓存有 1-2 分钟延迟,失败的话等几分钟再试。

### 5. 修改前端 HTML 的 API URL

打开 `output/index.html`(也就是之前部署的那个),找到这一行:

```javascript
|| 'https://cdn.jsdelivr.net/gh/haikou-market-pulse/data@main/prices.json';
```

把 `haikou-market-pulse/data` 改成你的仓库路径(格式:`<用户名>/<仓库名>`)。

或者更简单——在 HTML 顶部加一行:

```html
<script>window.PRICE_API_URL = 'https://cdn.jsdelivr.net/gh/你的用户名/haikou-market-pulse-data@main/data/prices.json';</script>
```

然后重新部署这个 HTML。

## 验证

打开部署好的 HTML,顶部状态栏右侧应该显示 **`● 实时同步`**(绿色徽标)而不是"估算数据"。

主走势图会有真实的 30 天历史曲线(不是正弦波),地图上的 4 区价格是真实数据。

## 故障排查

| 问题 | 原因 | 解决 |
|---|---|---|
| 页面显示"估算数据" | jsDelivr URL 错或还没缓存 | 检查 URL,等几分钟 |
| 页面显示"缓存数据" | jsDelivr 临时挂了 | 不影响,会显示上次缓存 |
| 30 天历史 < 30 天 | 海口周末/节假日不发布 | 正常,30 天里通常有 19-22 个数据点 |
| Actions 跑失败 | 海口网站结构变化 | 重新抓一次 latest.html 调整爬虫 |

## 手动触发更新

不一定要等 8 点定时跑。进仓库的 Actions 页面 → 选 `抓取海口物价数据` → `Run workflow` → 点绿色按钮,立即跑一次。

## 进阶:扩展更多数据源

`crawler/fetch.py` 里有 `CATEGORIES` 数组,目前:
- `source: 'haikou'` — 从海口菜篮子拉
- `source: 'static'` — 用 base 静态值

要接入农业农村部数据,在 `fetch_haikou_history()` 后加一个 `fetch_moa_history()`,然后在 `build_items()` 里给对应品类用真实数据即可。

## 文件结构

```
edge-solution/
├── README.md                  # 本文件
├── .github/workflows/
│   └── crawl.yml              # GitHub Actions 配置
├── crawler/
│   └── fetch.py               # 爬虫主程序
└── data/
    └── prices.json            # 生成的数据(部署后会被 Actions 覆盖)
```

## License

仅供学习,爬取政府公开数据请遵守目标站 robots.txt 和相关法规。
