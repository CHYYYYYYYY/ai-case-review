# 集装箱修箱清单 AI 稽核系统 — API 接口使用说明

> **版本：** v3.8-GG
> **更新日期：** 2026-08-13
> **流水线版本：** v3.8-GG（四级候选池 + 方向确认 + 关联优先 round-robin + 候选照片详细标签）

---

## 1. 服务概述

本系统提供**异步 REST API**，用于对「维修清单图片 + 修箱照片」进行 AI 稽核，输出每条维修项的验证结论。

| 项目 | 说明 |
|------|------|
| 服务名称 | 集装箱修箱清单 AI 稽核系统 |
| 协议 | HTTP |
| 数据格式 | JSON（文件上传使用 `multipart/form-data`） |
| 调用模式 | **异步任务**：提交后立即返回 `task_id`，后台执行 AI 流水线 |
| 获取结果 | **轮询状态** 或 **Webhook 回调** |
| 单次耗时 | 约 2~15 分钟（视照片数量及 vLLM QPS 而定） |

### 1.1 服务地址

| 用途 | 地址 |
|------|------|
| **公网访问** | `http://cd124615069w.vicp.fun:25740` |
| 内网地址 | `http://192.168.1.161:8000` |
| 在线接口文档（Swagger） | `http://192.168.1.161:8000/docs`（内网）/ `http://cd124615069w.vicp.fun:25740/docs`（公网） |
| 网页提交入口 | `http://192.168.1.161:8000/`（内网）/ `http://cd124615069w.vicp.fun:25740/`（公网） |
| 健康检查 | `http://192.168.1.161:8000/health`（内网）/ `http://cd124615069w.vicp.fun:25740/health`（公网） |

---

## 2. 快速开始

### 2.1 调用流程

```
┌─────────────┐     POST /api/v1/audits      ┌─────────────┐
│   调用方     │ ──────────────────────────► │   API 服务   │
│ (业务系统)  │ ◄────────────────────────── │  (立即返回)  │
└─────────────┘     202 + task_id            └──────┬──────┘
       │                                           │
       │                                           ▼
       │                                    ┌─────────────┐
       │                                    │ Celery Worker│
       │                                    │  P1→P6 流水线 │
       │                                    └──────┬──────┘
       │                                           │
       │  方式 A：轮询                              │
       │  GET /api/v1/audits/{task_id}             │
       │  每 5~10 秒查一次，直到 succeeded           │
       │                                           │
       │  方式 B：Webhook（提交时传 callback_url）    │
       │  ◄──── POST callback_url ──────────────────┘
       │
       ▼
  GET /api/v1/audits/{task_id}/report
  获取完整 JSON 稽核报告
```

### 2.2 三步完成一次稽核

1. **提交任务** → 拿到 `task_id`
2. **轮询状态** → 等待 `status` 变为 `succeeded`
3. **获取报告** → 调用 report 接口

---

## 3. 鉴权

当前环境已启用 API Key 鉴权。除健康检查、接口文档和网页入口等公开路径外，请求需携带：

```
X-API-Key: <your-api-key>
```

或：

```
Authorization: Bearer <your-api-key>
```

免全局 API Key 中间件路径：`/health`、`/docs`、`/redoc`、`/openapi.json`、`/`、`/static/*`、`/api/v1/session`，以及使用自身短期 Token 鉴权的嵌入页接口。

管理网页在首次打开时会要求输入 API Key。验证成功后，服务端签发 8 小时的 HttpOnly 会话 Cookie；原始 API Key 不写入网页文件或浏览器存储。外部系统仍应在其后端请求中使用 `X-API-Key`，不得把长期 Key 放入浏览器代码。

---

## 4. 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/v1/session` | 管理网页用 API Key 换取短时会话 |
| GET | `/api/v1/audits` | 历史任务列表（最新 50 条） |
| POST | `/api/v1/audits` | 提交稽核任务 |
| GET | `/api/v1/audits/{task_id}` | 查询任务状态与进度 |
| GET | `/api/v1/audits/{task_id}/report` | 获取稽核报告（含照片匹配详情） |
| PUT | `/api/v1/audits/{task_id}/items/{item_no}/manual-review` | 保存或更新人工复核结果 |
| POST | `/api/v1/audits/{task_id}/cancel` | 取消任务 |
| GET | `/api/v1/audits/{task_id}/photos/{photo_id}` | 照片中间结果（调试用） |
| GET | `/api/v1/audits/{task_id}/photos/{photo_id}/image` | 照片图片二进制（浏览器可直接显示） |
| POST | `/api/v1/embed-tokens` | 为指定任务签发只读嵌入链接 |

---

## 5. 接口详情

### 5.1 健康检查

**GET** `/health`

**响应示例：**

```json
{
  "status": "ok"
}
```

---

### 5.2 历史任务列表

**GET** `/api/v1/audits`

返回最近最多 50 条任务记录，按创建时间倒序排列。

**Query 参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| limit | int | 50 | 返回记录数上限 |

**响应示例：**

```json
[
  {
    "task_id": "aud_a1b2c3d4e5f678901234",
    "status": "succeeded",
    "current_stage": "p6",
    "container_number": "ABCD1234567",
    "final_recommendation": "PARTIAL",
    "verification_summary": {
      "verified": 3,
      "partial": 1,
      "missing": 1,
      "unsupported": 0,
      "overclaimed": 0
    },
    "created_at": "2026-08-13T10:30:00+08:00",
    "finished_at": "2026-08-13T10:35:00+08:00"
  }
]
```

---

### 5.3 提交稽核任务

**POST** `/api/v1/audits`

**Content-Type：** `multipart/form-data`
**响应码：** `202 Accepted`

#### 请求参数

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| manifest_image | file | 是 | 维修清单图片（1 张） |
| photos | file[] | 是 | 修箱照片，1~30 张 |
| photosIds | string[] | 合作方必填 | 客户业务照片 ID；重复提交，与 photos 顺序一一对应。旧客户端可暂不传 |
| callback_url | string | 否 | 任务成功后的回调 URL |
| metadata | string | 否 | 业务透传字段，JSON 字符串，原样返回 |

#### 限制

| 限制项 | 值 |
|--------|-----|
| 照片数量 | 1 ~ 30 张 |
| 单文件大小 | ≤ 10 MB |
| 支持格式 | `image/jpeg`、`image/png`、`image/webp` |

#### 响应示例

```json
{
  "task_id": "aud_a1b2c3d4e5f678901234",
  "status": "pending",
  "created_at": "2026-08-13T10:30:00+08:00",
  "photo_id_list": ["ph_abc123", "ph_def456", "ph_ghi789"],
  "photo_mappings": [
    {"photosId": "COMPANY_PHOTO_001", "photo_id": "ph_abc123", "seq": 1, "filename": "photo1.jpg"},
    {"photosId": "COMPANY_PHOTO_002", "photo_id": "ph_def456", "seq": 2, "filename": "photo2.jpg"},
    {"photosId": "COMPANY_PHOTO_003", "photo_id": "ph_ghi789", "seq": 3, "filename": "photo3.jpg"}
  ]
}
```

#### task_id 说明

- 由服务端自动生成，格式：`aud_` + 20 位随机字符
- 客户端无需传入，后续所有查询都使用此 ID
- 如需关联业务单号，通过 `metadata` 透传，例如：`{"order_id":"ORD-12345"}`

#### photo_id_list 说明（v3.8-GG 新增）

- 提交响应中返回照片 ID 列表，与上传顺序一一对应（seq 从 1 开始）
- 前端可用于建立 blob URL → photo_id 的映射关系，实现照片序号显示
- `photo_mappings` 是推荐使用的完整映射：`photosId` 为客户 ID，`photo_id` 为系统 ID
- 服务端校验 `photosIds` 与 `photos` 数量一致、每项非空、任务内不重复且不超过 128 字符

#### curl 示例

```bash
curl -X POST "http://cd124615069w.vicp.fun:25740/api/v1/audits" \
  -F "manifest_image=@manifest.jpg" \
  -F "photos=@photo1.jpg" \
  -F "photosIds=COMPANY_PHOTO_001" \
  -F "photos=@photo2.jpg" \
  -F "photosIds=COMPANY_PHOTO_002" \
  -F "callback_url=https://your-system.com/webhook/audit" \
  -F 'metadata={"order_id":"ORD-12345"}'
```

#### Python 示例

```python
import httpx

API_BASE = "http://cd124615069w.vicp.fun:25740"

with open("manifest.jpg", "rb") as mf, \
     open("photo1.jpg", "rb") as p1, \
     open("photo2.jpg", "rb") as p2:
    resp = httpx.post(
        f"{API_BASE}/api/v1/audits",
        files=[
            ("manifest_image", ("manifest.jpg", mf, "image/jpeg")),
            ("photos", ("photo1.jpg", p1, "image/jpeg")),
            ("photos", ("photo2.jpg", p2, "image/jpeg")),
        ],
        data=[
            ("photosIds", "COMPANY_PHOTO_001"),
            ("photosIds", "COMPANY_PHOTO_002"),
            ("metadata", '{"order_id":"ORD-12345"}'),
        ],
        timeout=30,
    )

task = resp.json()
task_id = task["task_id"]
print(f"任务已提交: {task_id}")
```

---

### 5.4 查询任务状态

**GET** `/api/v1/audits/{task_id}`

建议每 **5~10 秒** 轮询一次，直到 `status` 为终态。

#### 任务状态（status）

| 值 | 含义 | 是否终态 |
|----|------|----------|
| pending | 排队等待 | 否 |
| running | 执行中 | 否 |
| succeeded | 成功，可取报告 | 是 |
| failed | 失败 | 是 |
| cancelled | 已取消 | 是 |

#### 流水线阶段（current_stage）

任务执行中会依次经过以下 **10 个阶段**（v3.8-GG）：

| 阶段 | 说明 |
|------|------|
| p1_a | 铭牌识别（提取箱号） |
| p1_b | 清单 OCR（提取维修项） |
| p2_a | 照片索引（识别部件类型 + 场景富字段） |
| p2_b | 手写 Location 识别（含红色箱管批注） |
| p2_c | 货物识别 |
| p3 | 清单预处理（编码纠错 + MCO 硬规则 + 手写强匹配） |
| p4_a1 | 部件筛选（四级候选池 + 识别缓存） |
| p4_a2 | 方向确认 + 全局照片分配 |
| p5_b | 损伤核验 + 证据分级 |
| p6 | 报告编译 |

#### 响应示例

```json
{
  "task_id": "aud_a1b2c3d4e5f678901234",
  "status": "running",
  "current_stage": "p4_a2",
  "progress": {
    "stage": "8/10",
    "detail": "P4-A2 方向确认 + 照片分配"
  },
  "container_number": "ABCD1234567",
  "final_recommendation": null,
  "error": null,
  "cost": {
    "tokens": 8500,
    "cny": 0.0
  },
  "created_at": "2026-08-13T10:30:00+08:00",
  "updated_at": "2026-08-13T10:33:00+08:00",
  "finished_at": null,
  "metadata": {
    "order_id": "ORD-12345"
  },
  "photos": [
    {
      "photo_id": "ph_abc123",
      "seq": 1,
      "filename": "photo_01.jpg"
    },
    {
      "photo_id": "ph_def456",
      "seq": 2,
      "filename": "photo_02.jpg"
    }
  ]
}
```

#### Python 轮询示例

```python
import time
import httpx

API_BASE = "http://cd124615069w.vicp.fun:25740"
task_id = "aud_a1b2c3d4e5f678901234"

while True:
    resp = httpx.get(f"{API_BASE}/api/v1/audits/{task_id}", timeout=10)
    data = resp.json()
    status = data["status"]
    detail = data["progress"]["detail"]
    print(f"状态: {status} | {detail}")

    if status in ("succeeded", "failed", "cancelled"):
        break
    time.sleep(8)
```

---

### 5.5 获取稽核报告

**GET** `/api/v1/audits/{task_id}/report`

仅当 `status = succeeded` 时可调用，否则返回 `409 task_not_ready`。

#### 响应示例

```json
{
  "task_id": "aud_a1b2c3d4e5f678901234",
  "container_number": "ABCD1234567",
  "container_recognition": {
    "container_number": "ABCD1234567",
    "source": "photo",
    "confidence": 0.9,
    "source_photo": {
      "photosId": "COMPANY_PHOTO_006",
      "photo_id": "ph_source006",
      "seq": 6,
      "filename": "photo_06.jpg",
      "photo_url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_source006/image"
    }
  },
  "repair_move": 35.0,
  "final_recommendation": "PARTIAL",
  "verification_summary": {
    "verified": 3,
    "partial": 1,
    "missing": 1,
    "unsupported": 0,
    "overclaimed": 0
  },
  "total_list_items": 5,
  "item_verifications": [
    {
      "item_no": 1,
      "component": "PAA",
      "component_name": "面板",
      "component_suspicious": false,
      "is_paa": true,
      "paa_panel_face": "F",
      "location_code": "F",
      "damage_code": "BR",
      "damage_name": "破损",
      "damage_suspicious": false,
      "location_suspicious": false,
      "total": 100.0,
      "verification_status": "verified",
      "strong_match": false,
      "match_source": "none",
      "matched_photos": [],
      "core_photos": ["ph_abc123", "ph_def456", "ph_ghi789"],
      "core_photos_detail": [
        {
          "photo_id": "ph_abc123",
          "photo_name": "photo_13.jpg",
          "photo_url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_abc123/image",
          "component_match": true,
          "side_match": true,
          "direction_score": 0.85,
          "direction_label": "前端 · 右侧",
          "labels": ["部件匹配", "方向匹配"]
        },
        {
          "photo_id": "ph_def456",
          "photo_name": "photo_05.jpg",
          "component_match": true,
          "side_match": false,
          "direction_score": 0.60,
          "direction_label": "后端 · 左侧",
          "labels": ["部件匹配"]
        },
        {
          "photo_id": "ph_ghi789",
          "photo_name": "photo_01.jpg",
          "component_match": false,
          "side_match": false,
          "direction_score": 0.45,
          "direction_label": "",
          "labels": []
        }
      ],
      "photo_evidence": ["ph_abc123"],
      "reference_photos": ["ph_def456", "ph_ghi789"],
      "auditor_notes": "照片与清单描述一致",
      "direction": {
        "facing": "rear",
        "side": "right_side",
        "score": 0.85,
        "shot_type": "longitudinal",
        "source_photo": "ph_abc123"
      },
      "mco_verdict": "none"
    }
  ],
  "cargo_info": {
    "cargo_name": "钢材",
    "confidence": 0.85,
    "cargo_desc": "箱内可见钢材货物",
    "source_photos": ["ph_ghi789"]
  },
  "photo_id_list": ["ph_abc123", "ph_def456", "ph_ghi789"],
  "photo_mappings": [
    {
      "photosId": "COMPANY_PHOTO_001",
      "photo_id": "ph_abc123",
      "seq": 13,
      "filename": "photo_13.jpg",
      "photo_url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_abc123/image"
    }
  ],
  "photos": [
    {
      "photosId": "COMPANY_PHOTO_001",
      "photo_id": "ph_abc123",
      "seq": 13,
      "filename": "photo_13.jpg",
      "photo_url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_abc123/image"
    },
    {
      "photosId": "COMPANY_PHOTO_002",
      "photo_id": "ph_def456",
      "seq": 5,
      "filename": "photo_05.jpg",
      "photo_url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_def456/image"
    },
    {
      "photosId": "COMPANY_PHOTO_003",
      "photo_id": "ph_ghi789",
      "seq": 1,
      "filename": "photo_01.jpg",
      "photo_url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_ghi789/image"
    }
  ],
  "photo_name_map": {
    "ph_abc123": "photo_13.jpg",
    "ph_def456": "photo_05.jpg",
    "ph_ghi789": "photo_01.jpg"
  },
  "photo_url_map": {
    "ph_abc123": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_abc123/image",
    "ph_def456": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_def456/image",
    "ph_ghi789": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_ghi789/image"
  },
  "allocation_strategy": "v3.8-GG(四级候选池+方向确认+关联优先round-robin)",
  "pipeline_version": "v3.8-GG",
  "cost": {
    "total_tokens": 12500,
    "estimated_cost_cny": 0.0
  },
  "errors": []
}
```

#### 报告字段说明

##### item_verifications（单项验证）

| 字段 | 类型 | 说明 |
|------|------|------|
| item_no | int | 清单行号 |
| component | string | 部件编码（如 PAA、FPP） |
| component_name | string | 部件中文名 |
| component_suspicious | bool | 部件编码可疑（OCR 解析异常） |
| is_paa | bool | PAA（精密仪器）标记 |
| paa_panel_face | string | PAA 面板朝向（F/B） |
| location_code | string | 位置编码 |
| damage_code | string | 损伤编码 |
| damage_name | string | 损伤中文名 |
| damage_suspicious | bool | 损伤编码可疑 |
| location_suspicious | bool | 位置编码可疑 |
| total | number/null | 估价单当前 Item 的 Total 原始金额；空白或无法可靠识别时为 null |
| verification_status | string | 验证状态（见下表） |
| strong_match | bool | 手写强匹配命中（跳过 P4/P5） |
| match_source | string | 强匹配来源（exact/fuzzy/none） |
| matched_photos | string[] | 手写强匹配来源照片 |
| **core_photos** | string[] | 候选照片 ID 列表（向后兼容字段） |
| **core_photos_detail** | object[] | **v3.8-GG 新增** 每张照片的匹配详情（见下表） |
| photo_evidence | string[] | 证据照片（损伤分≥0.6） |
| reference_photos | string[] | 参考照片（损伤分<0.6） |
| auditor_notes | string | 稽核备注 |
| direction | object | 方向确认结果（面向/侧向/分值/拍摄类型/来源照片） |
| mco_verdict | string | MCO 硬规则判定 |

##### core_photos_detail（候选照片详情，v3.8-GG 新增）

| 字段 | 类型 | 说明 |
|------|------|------|
| photo_id | string | 照片唯一 ID |
| photosId | string/null | 客户上传时提供的业务照片 ID；旧任务可能为 null |
| photo_name | string | 原始文件名 |
| **photo_url** | string | **v3.8-GG 新增** 可直接访问的图片 URL（公网/内网均可用） |
| component_match | bool | 是否部件匹配 |
| side_match | bool | 是否方向匹配 |
| direction_score | float | 方向分（0.0~1.0） |
| direction_label | string | 方向中文标签（如"前端 · 右侧"，空字符串表示未知） |
| labels | string[] | 匹配标签数组（值为"部件匹配"或"方向匹配"） |

##### 报告顶层新增字段（v3.8-GG）

| 字段 | 类型 | 说明 |
|------|------|------|
| **photo_id_list** | string[] | 照片 ID 列表（按上传顺序），用于前后端照片序号对应 |
| **photo_mappings** | object[] | 客户 photosId 与系统 photo_id 的完整映射 |
| **photos** | object[] | 照片详情数组（photosId / photo_id / seq / filename / photo_url） |
| **container_recognition** | object | 箱号识别来源；若来自照片，source_photo 给出 photosId / photo_id / seq / filename / photo_url |
| **repair_move** | number/null | 估价单箱级 RepairMove（移箱费）原始金额；空白或无法可靠识别时为 null |
| photo_name_map | object | photo_id → 原始文件名映射 |
| photo_url_map | object | photo_id → API 代理图片 URL 映射 |

> `verified` 必须至少有一张本任务中真实存在的证据照片；无证据照片的 Item 返回 `missing`（页面显示“未通过”）。每条清单最多展示 **5 张**候选照片。

##### verification_status（单项验证状态）

| 值 | 含义 |
|----|------|
| verified | 照片证据充分，与清单一致 |
| partial | 有部分证据，但不完整 |
| missing | 缺少匹配照片 |
| unsupported | 不支持/规则判定不通过 |
| overclaimed | 过度索赔（清单无匹配项） |

##### direction（方向确认）

| 字段 | 类型 | 说明 |
|------|------|------|
| facing | string | 面向（front/rear/unknown） |
| side | string | 左右侧（left_side/right_side/unknown） |
| score | float | 方向分（0.85/0.60/0.0） |
| shot_type | string | 拍摄类型（lateral/longitudinal/doorway/exterior/unknown） |
| source_photo | string | 提供方向信息的来源照片 ID |

#### 最终结论（final_recommendation）

| 值 | 含义 |
|----|------|
| VERIFIED | 全部维修项均有充分照片证据 |
| PARTIAL | 部分维修项已验证 |
| MISSING | 多数维修项缺少证据 |

---

### 5.6 人工复核 AI 结果

**PUT** `/api/v1/audits/{task_id}/items/{item_no}/manual-review`

用于记录人工判断 AI 对某个维修项目的审核是否正确。同一任务的同一 `item_no` 只保留一条记录，再次提交会更新原记录，不会修改原始 AI 报告。

请求体：

```json
{
  "is_ai_correct": false,
  "reason": "照片中能够看到该位置的损伤，AI 未识别"
}
```

规则：

- `is_ai_correct=true` 时，`reason` 会被清空。
- `is_ai_correct=false` 时，`reason` 必填，最长 1000 个字符。
- 任务必须已经处理成功，且 `item_no` 必须存在于报告中。

响应：

```json
{
  "item_no": 2,
  "ai_verification_status": "missing",
  "is_ai_correct": false,
  "reason": "照片中能够看到该位置的损伤，AI 未识别",
  "created_at": "2026-09-08T09:00:00+08:00",
  "updated_at": "2026-09-08T09:06:00+08:00"
}
```

再次获取报告时，对应 `item_verifications[]` 会增加 `manual_review`；报告顶层也会返回 `manual_reviews` 数组。未复核的项目其 `manual_review` 为 `null`。

### 5.7 照片图片（浏览器可直接显示）

**GET** `/api/v1/audits/{task_id}/photos/{photo_id}/image`

返回照片的**原始二进制图片**（JPEG），可直接在浏览器地址栏粘贴访问，或用于 `<img src="...">` 标签。

**用途：** 绕过 MinIO 预签名 URL 过期问题和 `file://` 协议浏览器安全限制，前端可直接展示照片缩略图。

**响应：** `Content-Type: image/jpeg`，可直接作为图片 URL 使用。

---

### 5.8 照片中间结果（调试用）

**GET** `/api/v1/audits/{task_id}/photos/{photo_id}`

返回照片经过各流水线阶段解析出的结构化元数据。

**响应示例：**

```json
{
  "photo_id": "ph_abc123",
  "task_id": "aud_a1b2c3d4e5f678901234",
  "seq": 1,
  "original_filename": "photo_13.jpg",
  "url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/photos/ph_abc123/image",
  "object_key": "audits/20260813/xxx.jpg",
  "component_type": "panel",
  "is_on_truck": false,
  "is_interior": false,
  "likely_location": "side_panel",
  "has_damage": true,
  "is_plate_info": false,
  "handwritten_location": "B",
  "chinese_direction": "右",
  "is_door_open": false,
  "cargo_name": null,
  "stage_error": null
}
```

---

### 5.9 取消任务

**POST** `/api/v1/audits/{task_id}/cancel`

**响应码：** `202 Accepted`

仅 `pending` 或 `running` 状态可取消。

**响应示例：**

```json
{
  "task_id": "aud_a1b2c3d4e5f678901234",
  "status": "cancelled"
}
```

---

### 5.10 重试清单 OCR 失败任务

**POST** `/api/v1/audits/{task_id}/retry`

**响应码：** `202 Accepted`

目前只允许重试状态为 `failed` 且失败阶段为 `p1_b` 的任务。服务会从
P1-B 清单 OCR 继续执行，复用已经完成的 P1-A 箱号识别结果，不会重新扫描
全部修箱照片。

**响应示例：**

```json
{
  "task_id": "aud_a1b2c3d4e5f678901234",
  "status": "pending",
  "start_stage": "p1_b"
}
```

其他状态调用会返回 `409 Conflict`。任务历史阶段记录不会删除，新的 P1-B
诊断结果会追加保存，便于问题追溯。

---

### 5.11 Webhook 回调

提交任务时传入 `callback_url`，任务**成功**后服务端主动 POST 通知。

#### 回调请求

**Headers：**

```
Content-Type: application/json
X-Audit-Signature: sha256=<HMAC-SHA256 签名>   # 配置了 WEBHOOK_SECRET 时携带
```

**Body：**

```json
{
  "task_id": "aud_a1b2c3d4e5f678901234",
  "event": "task.succeeded",
  "status": "succeeded",
  "report_url": "/api/v1/audits/aud_a1b2c3d4e5f678901234/report"
}
```

> `report_url` 为相对路径，需拼接 Base URL 后调用获取完整报告。
> 回调失败会重试 3 次，间隔 10s / 60s / 300s。

#### 接收方示例（Python Flask）

```python
from flask import Flask, request
import httpx

app = Flask(__name__)
API_BASE = "http://cd124615069w.vicp.fun:25740"

@app.route("/webhook/audit", methods=["POST"])
def audit_callback():
    data = request.json
    task_id = data["task_id"]
    if data["status"] == "succeeded":
        report = httpx.get(f"{API_BASE}{data['report_url']}").json()
        print(f"稽核完成: {task_id}, 结论: {report['final_recommendation']}")
    return "", 200
```

---

## 6. 错误码

| HTTP 状态码 | detail | 说明 |
|-------------|--------|------|
| 400 | 至少上传 1 张修箱照片 | 照片为空 |
| 400 | 最多 30 张修箱照片 | 超出数量限制 |
| 400 | 不支持的文件类型: ... | 文件格式不对 |
| 400 | metadata 必须是合法 JSON | metadata 格式错误 |
| 400 | photosIds 数量必须与 photos 数量一致 | 客户照片 ID 未与上传照片逐项对应 |
| 400 | photosIds 不能为空 | 客户照片 ID 包含空值 |
| 400 | 同一任务中的 photosIds 不能重复 | 客户照片 ID 重复 |
| 400 | 清单图片超过 10MB | 文件过大 |
| 400 | 照片 N 超过 10MB | 单张照片过大 |
| 401 | unauthorized | API Key 鉴权失败 |
| 404 | task_not_found | 任务不存在 |
| 404 | report_not_found | 报告不存在 |
| 404 | photo_not_found | 照片不存在 |
| 404 | file_not_found | 照片文件不存在 |
| 409 | task_not_ready | 任务未完成，报告不可用 |
| 409 | task_already_{status} | 任务已结束，无法取消 |
| 429 | `AI_AUDIT_BUSY` | 待处理队列达到配置上限，未创建任务，请稍后重试 |
| 429 | `AI_AUDIT_DAILY_LIMIT_REACHED` | 当日接收任务数达到配置上限，未创建任务，请次日重试 |
| 503 | `AI_AUDIT_ADMISSION_UNAVAILABLE` | 限流状态服务暂时不可用，为保护算力拒绝接收 |

错误响应格式：

```json
{
  "detail": "task_not_found"
}
```

审核忙碌响应示例（响应头同时包含 `Retry-After`，单位为秒）：

```json
{
  "detail": {
    "code": "AI_AUDIT_BUSY",
    "message": "AI审核忙碌中，请稍后重试",
    "reason": "queue_full",
    "retryable": true,
    "limit": 10,
    "current": 10
  }
}
```

收到 HTTP 429 时服务端不会生成 `task_id`，也不会保存上传文件。排队上限按
“正在处理 + 排队等待”的未结束任务总数计算。调用方应将
箱体 AI 审核状态置为“审核忙碌中”，不要标记成“审核失败”。队列上限和每日
上限分别通过部署配置中的 `admission.max_pending_tasks`、
`admission.max_daily_tasks` 调整；每日统计按北京时间自然日重置。

---

## 7. 在线调试

### 方式一：Swagger 文档（推荐）

浏览器打开：`http://cd124615069w.vicp.fun:25740/docs`

1. 展开 `POST /api/v1/audits`
2. 点击 **Try it out**
3. 上传清单图和照片
4. 点击 **Execute**
5. 复制返回的 `task_id`，用 `GET` 接口查询状态和报告

### 方式二：网页提交

浏览器打开：`http://cd124615069w.vicp.fun:25740/`

通过页面上传文件提交任务（适合非技术人员试用）。

### 方式三：命令行冒烟测试

```bash
cd /home/jingouai/金小宝/audit-service
python scripts/smoke_test.py manifest.jpg photo1.jpg photo2.jpg
```

---

## 8. 服务运维

### 8.1 启动服务

```bash
cd /home/jingouai/金小宝/audit-service

# 启动基础设施（Docker）
docker-compose up -d postgres redis minio

# 启动 Python 服务（Celery worker/beat/uvicorn）
nohup bash scripts/start_api_conda.sh > logs/api.log 2>&1 &
nohup bash scripts/start_worker_conda.sh > logs/worker.log 2>&1 &
```

### 8.2 停止服务

```bash
# 停止 Python 服务
bash scripts/stop_audit.sh

# 停止 Docker 容器
docker-compose down
```

### 8.3 查看日志

```bash
tail -f logs/api.log      # API 日志
tail -f logs/worker.log   # Worker 日志
docker logs -f audit-postgres
docker logs -f audit-redis
docker logs -f audit-minio
```

### 8.4 查看服务状态

```bash
curl http://localhost:8000/health
docker ps
ps aux | grep -E "uvicorn|celery"
```

### 8.5 注意事项

- 服务使用 `nohup` 后台运行，退出 SSH 不会停止
- **服务器重启后需手动重新启动**
- 当前仅局域网可访问，外网需配置端口映射或反向代理

---

## 9. v3.8-GG 能力说明

### 已实现功能

- **P2-B** 手写 Location 识别 + 红色箱管批注（含中文方向词：左/右）
- **P3-C** 手写强匹配（精确/模糊纠正），无效标记自动过滤
- **P4-A1** 部件筛选（四级候选池 + LLM 识别缓存，跨 Item 复用）
- **P4-A2** 方向确认（AI 描述 + 代码层 facing/side/score 计算）
- **P4-GG** 关联优先 round-robin 互斥分配，每 Item 最多 5 张候选照片
- **P5-B** 损伤核验 + 证据分级（photo_evidence / reference_photos）
- **P6** 报告编译，含每张照片的匹配标签（部件匹配 / 方向匹配 / 方向分 / 方向标签）
- **10 阶段**完整流水线（p1_a → p1_b → p2_a → p2_b → p2_c → p3 → p4_a1 → p4_a2 → p5_b → p6）

### 报告新增字段（v3.8-GG）

- `core_photos_detail[]` — 每张候选照片的详细匹配信息（向前兼容 `core_photos[]`）
- `photo_id_list` — 照片 ID 列表（按上传顺序），便于前后端对应
- `photos[]` — 照片详情数组（photo_id / seq / filename / photo_url）
- `photo_name_map` — photo_id → 原始文件名映射
- `photo_url_map` — photo_id → API 代理图片 URL（可直接访问）
- `damage_suspicious`、`location_suspicious` — 可疑字段标记

### 性能提示

- P2-B + P4-A2 会增加 LLM 调用量，单任务预计 5~15 分钟（视照片数与 vLLM QPS 而定）
- 调试时可设 `llm.mock: true` 零成本跑通流水线逻辑
- 使用本地 vLLM 时，照片数量建议不超过 30 张

---

## 10. 联系方式

联调问题请联系开发同学，可提供：
- Swagger 在线文档地址
- 测试用清单图和修箱照片样例
- 联调支持
