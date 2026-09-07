# 合作方鉴定结果前端接入说明

本目录是一份不依赖 iframe、不依赖 React/Vue、不需要构建工具的报告渲染模板。合作方后端取得报告 JSON 后，可在自己的 HTML 页面中直接渲染。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `index.html` | 最小可运行页面 |
| `styles.css` | 报告卡片样式，可按合作方品牌修改 |
| `report-renderer.js` | 将报告 JSON 渲染成 HTML |
| `sample-report.json` | 可直接预览的脱敏示例数据 |
| `reference/current-system-index.html` | 当前系统完整页面，仅用作视觉和交互参考 |

## 推荐接入流程

1. 合作方后端调用 `POST /api/v1/audits`，逐张上传 `photos` 和同顺序的 `photosIds`。
2. 提交响应中保存 `task_id` 与 `photo_mappings`。
3. 轮询 `GET /api/v1/audits/{task_id}`，或等待 `callback_url` 收到 `task.succeeded`。
4. 合作方后端请求 `GET /api/v1/audits/{task_id}/report`。
5. 合作方后端把该 JSON 返回给自己的前端，由 `report-renderer.js` 渲染。

> API Key、Webhook Secret 等长期凭证只能保存在合作方后端，不得写入 HTML 或浏览器 JavaScript。

## photosIds 对应关系

上传时重复提交同名字段，顺序必须一致：

```text
第 1 个 photos ↔ 第 1 个 photosIds
第 2 个 photos ↔ 第 2 个 photosIds
```

```bash
curl -X POST 'http://cd124615069w.vicp.fun:25740/api/v1/audits' \
  -F 'manifest_image=@manifest.jpg' \
  -F 'photos=@photo-a.jpg' \
  -F 'photosIds=COMPANY_PHOTO_001' \
  -F 'photos=@photo-b.jpg' \
  -F 'photosIds=COMPANY_PHOTO_002'
```

报告中每张照片同时包含：

```json
{
  "photosId": "COMPANY_PHOTO_001",
  "photo_id": "ph_aabbcc",
  "filename": "photo-a.jpg",
  "photo_url": "/api/v1/audits/aud_xxx/photos/ph_aabbcc/image"
}
```

- `photosId`：合作方上传时提供的业务 ID，用它回关合作方自己的照片记录。
- `photo_id`：鉴定系统生成的内部 ID，用于调用照片接口。

## 方式一：前端请求合作方自己的报告接口

将 `index.html` 中的配置改为：

```html
<script>
window.AUDIT_PAGE_CONFIG = {
  reportUrl: '/company-api/audit-tasks/AUD_123/report',
  imageBaseUrl: 'http://cd124615069w.vicp.fun:25740'
};
</script>
```

`reportUrl` 应指向合作方自己的同域后端接口。

## 方式二：后端渲染 HTML 时直接注入 JSON

```html
<script>
window.AUDIT_REPORT = /* 后端在这里输出序列化后的报告 JSON */;
window.AUDIT_PAGE_CONFIG = {
  imageBaseUrl: 'http://cd124615069w.vicp.fun:25740'
};
</script>
```

注入 JSON 时必须使用框架或模板引擎提供的 JSON 序列化方法，不要手工拼接字符串。

## 方式三：已有前端页面中调用渲染器

```html
<link rel="stylesheet" href="/assets/audit-report/styles.css">
<div id="audit-report"></div>
<script src="/assets/audit-report/report-renderer.js"></script>
<script>
fetch('/company-api/audit-tasks/AUD_123/report')
  .then(function (response) { return response.json(); })
  .then(function (report) {
    window.AuditReportRenderer.render(
      document.getElementById('audit-report'),
      report,
      { imageBaseUrl: 'http://cd124615069w.vicp.fun:25740' }
    );
  });
</script>
```

## 图片地址处理

API 返回的 `photo_url` 可能是相对路径。`imageBaseUrl` 用于把它转换成完整地址。

如果合作方网站使用 HTTPS，浏览器可能拦截直接访问 HTTP 图片的请求。生产环境建议由合作方后端代理图片，并把报告中的 `photo_url` 改写为合作方自己的 HTTPS 地址。

## 本地预览

不要直接双击 `index.html`，否则浏览器可能禁止读取示例 JSON。在本目录执行：

```bash
python -m http.server 8088
```

然后打开 `http://127.0.0.1:8088/`。

## 样式修改

颜色、宽度和字体都集中在 `styles.css` 顶部的 CSS 变量中。详细区域默认全部展开，判定结果位于每个项目卡片右上角。
