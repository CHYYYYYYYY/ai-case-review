# iframe 嵌入鉴定结果接口

## 1. 接入流程

1. 合作方后端调用 `POST /api/v1/audits` 提交任务，保存返回的 `task_id`。
2. 合作方后端携带长期 `EMBED_API_KEY` 调用 `POST /api/v1/embed-tokens`。
3. 服务返回仅绑定该任务、默认 30 分钟有效的 `embed_url`。
4. 合作方前端把 `embed_url` 设置为 iframe 的 `src`。
5. 嵌入页自动轮询任务状态；完成后显示总体结论、逐项结果、鉴定依据和证据照片。

长期 API Key 只能保存在合作方后端，不能写入网页、App 或 iframe。`embed_url` 中的短期 Token 只能读取一个任务，不能修改、删除、取消任务，也不能读取其他任务。

## 2. 服务端配置

```dotenv
EMBED_API_KEY=<合作方后端长期凭证，建议至少 32 个随机字节>
EMBED_TOKEN_SECRET=<Token 签名密钥，至少 32 个字符且与 API Key 不同>
EMBED_PUBLIC_BASE_URL=http://cd124615069w.vicp.fun:25740
```

当前按需求不限制允许嵌入的域名。上线正式公网环境时建议启用 HTTPS，避免短期 Token 在传输过程中泄露。

## 3. 创建嵌入链接

**POST** `/api/v1/embed-tokens`

请求头：

```http
Content-Type: application/json
X-API-Key: <EMBED_API_KEY>
```

请求体：

```json
{
  "task_id": "aud_cfd69905f97f44b49053",
  "expires_in_seconds": 1800
}
```

`expires_in_seconds` 可省略，默认 `1800` 秒；允许范围为 `60` 至 `3600` 秒。

响应示例：

```json
{
  "task_id": "aud_cfd69905f97f44b49053",
  "embed_url": "http://cd124615069w.vicp.fun:25740/embed/audits/aud_cfd69905f97f44b49053#token=e1.xxxxx.yyyyy",
  "expires_at": "2026-09-03T11:30:00+00:00",
  "expires_in_seconds": 1800,
  "scope": "audit:read",
  "read_only": true
}
```

Token 放在 URL 的 fragment（`#token=`）中，浏览器不会把它发送到 HTTP 访问日志或 Referer。页面读取后会立即从地址栏移除，并只保存在当前标签页的 `sessionStorage`。

## 4. 前端嵌入

```html
<iframe
  src="服务端返回的 embed_url"
  title="集装箱维修稽核结果"
  style="width:100%;min-height:900px;border:0"
  loading="lazy"
></iframe>
```

页面为只读，支持查看证据照片和打印报告。进行中的任务每 4 秒自动刷新一次，不需要合作方前端自行轮询。

## 5. 错误码

| HTTP 状态 | detail | 说明 |
|---|---|---|
| 401 | `unauthorized` | 长期 API Key 缺失或错误 |
| 401 | `embed_token_required` | 嵌入数据请求未携带短期 Token |
| 401 | `invalid_token` | Token 格式或签名无效 |
| 401 | `token_expired` | Token 已过期，需要重新创建链接 |
| 403 | `task_access_denied` | Token 与请求的任务不一致 |
| 404 | `task_not_found` | 任务不存在 |
| 503 | `embed_api_key_not_configured` | 服务端尚未配置签发凭证 |
| 503 | `embed_token_secret_not_configured` | 服务端尚未配置签名密钥 |

## 6. cURL 示例

```bash
curl -X POST 'http://cd124615069w.vicp.fun:25740/api/v1/embed-tokens' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <EMBED_API_KEY>' \
  -d '{"task_id":"aud_cfd69905f97f44b49053"}'
```

历史记录不会被自动清空，网页端和公共 API 均不提供删除任务功能；如确需删除，必须由运维人员在后台人工处理。
