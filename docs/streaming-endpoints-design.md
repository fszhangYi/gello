# 设计：`/data` · `/pose` · `/gripper` 实时数据接口

状态：**已实现**（`gello_app/web/server.py`：`GET /data` SSE、`GET /joints`、`GET /gripper`；`step = snapshot.seq`，每次 `connect` 重置）  
范围：在现有 Gello Host（默认 `http://127.0.0.1:7020/`）上增加对外数据出口，供脚本 / 下游进程持续消费示教器同结构的关节数据。  
相关语义见 [`joints-raw-and-calibrated.md`](./joints-raw-and-calibrated.md)。  
注：原 `/pose` + 字段 `pose` 已更名为 **`/joints` + `joints`**（2026-10）。

---

## 1. 目标

| 路径 | 行为 | 数据内容 |
|------|------|----------|
| `GET /data` | **持续推送**（流式） | 与 Web 页「原始 / 标定 (rad)」一致的完整 JSON + **`step`** |
| `GET /joints` | 获取（默认可瞬时；可选流式） | **标定角** `joints_rad` 前 6 个（臂，rad；含 UI 下发的 offsets/signs）+ **`step`** |
| `GET /gripper` | 获取（默认可瞬时；可选流式） | **标定角** `joints_rad` 最后 1 个 + **`step`** |

约束：

- 不改动 `hik-sensors` 驱动语义；只读 `GelloService` 最新快照。
- 与现有 UI 轮询接口 `GET /api/status` **并存**（`/api/*` 留给控制台；根路径短名留给机器消费）。
- 无鉴权（与当前 Host 一致）；仅建议绑定 `127.0.0.1` 或受信局域网。
- **三个对外接口的每一帧/每一次响应都必须带整数字段 `step`（数据序列号）。**

---

## 2. 数据源

采样环（`GelloService._sample_once`）已维护最新快照，字段包括：

- `joint_ids`, `joints_rad`, `joints_rad_raw`, `joint_offsets`, `joint_signs`
- 以及 `ts`, `seq`, `connected`, `dry_run`, `synth`, `hz`, `port`, `error` 等状态

**面板同构载荷**（`/data` 的 `panel`）定义为：

```json
{
  "joint_ids": [1, 2, 3, 4, 5, 6, 7],
  "joints_rad": [/* 标定角，len=N */],
  "joints_rad_raw": [/* 原始角，len=N */],
  "joint_offsets": [/* ... */],
  "joint_signs": [/* ... */]
}
```

与截图一致；外层元数据必须含 `step` / `ts`（见 §2.1、§4）。

约定：`N = len(joints_rad)`；正常配置 `N ≥ 7`。若 `N < 7`：

- `/joints`：取 `joints_rad[:6]`，不足则短数组 + HTTP 200 但 `ok: false` / `error: "need_6_joints"`（见 §5）；**仍返回当时的 `step`**（若尚无采样则为 `0`）。
- `/gripper`：取 `joints_rad[-1]`；若 `N == 0` 则错误；**同样必须带 `step`**。

### 2.1 序列号 `step`（三接口共用）

| 项 | 约定 |
|----|------|
| 字段名 | **`step`**（对外唯一名称；不要再用 `seq` 出现在这三个接口的 JSON 里） |
| 类型 | 非负整数 `int` |
| 语义 | 采样环每成功产出一帧关节快照 +1；同一物理采样帧上，`/data`、`/pose`、`/gripper` 读到的 **`step` 必须相同** |
| 内部映射 | 等于 `GelloService` 快照里的 `seq`（实现时做字段别名：`step = snapshot.seq`） |
| 起始 | 进程内从 `0` 起；首次有效采样后为 `1, 2, 3, …`（未连接、无样本时响应里 `step: 0`） |
| 单调性 | 单次 `connect` 会话内单调递增；`disconnect` 后再 `connect` 可从 `0` 重新计数（实现可选：跨连接继续累加；**默认重置**，并在文档/README 写明） |
| 用途 | 下游对齐多话题、丢帧检测（`step` 跳号）、录包索引 |

**强制**：`/data` 每一 SSE 帧、`/pose` / `/gripper` 每一次瞬时或流式响应，顶层均含 `"step": <int>`。

---

## 3. 推送机制选型（`/data`）

「源源不断」需要**服务端推送**，候选：

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| **SSE** (`text/event-stream`) | 实现简单；`curl` / 浏览器 `EventSource` 即可；单向推送足够 | 仅 HTTP/1.1 单向；部分代理缓冲 | **首选** |
| WebSocket | 双向、低开销 | 客户端略重；本场景无上行需求 | 可选二期 |
| 客户端狂轮询 `GET` | 已有 `/api/status` | 非「推送」；延迟与负载差 | 不满足 `/data` 目标 |

**决定：`GET /data` 使用 Server-Sent Events (SSE)。**

推送节流：

- 默认按采样环节拍推送，但 **SSE 下限间隔** 可配置（建议默认 `50 ms` ≈ 20 Hz，避免浏览器/日志被 50 Hz 打满）。
- 仅当 **`step` 变化**时发送（相同快照不重复推），降低空转带宽。
- 查询参数（实现阶段）：
  - `?hz=20`：最大推送频率（Hz），夹到 `[1, sample_hz]`。
  - `?full=1`：事件体含完整 `status`（含 `hz`/`port` 等）；默认 `full=0` 只含面板五字段 + `ts` / **`step`**。

---

## 4. 接口设计

### 4.1 `GET /data` — 连续推送

- **Method / Path**：`GET /data`
- **Response**：`Content-Type: text/event-stream`
- **SSE 事件**：
  - `event: gello`（或省略，用默认 `message`）
  - `data: <单行 JSON>`

推荐帧 JSON（**必须含 `step`**）：

```json
{
  "ts": 1730000000.123,
  "step": 42,
  "connected": true,
  "dry_run": true,
  "synth": true,
  "panel": {
    "joint_ids": [1, 2, 3, 4, 5, 6, 7],
    "joints_rad": [],
    "joints_rad_raw": [],
    "joint_offsets": [],
    "joint_signs": []
  }
}
```

未连接时仍保持流不断开：周期性推送 `connected: false`、`step` 可为 `0` 或保持断开前最后一值（实现选一种并固定；**建议断开后停增、`step` 保留最后值直到再次 connect 重置**）、`panel` 字段为空数组，便于下游检测掉线。

**客户端示例（设计约定，非实现）：**

```bash
curl -N http://127.0.0.1:7020/data
```

```javascript
const es = new EventSource("http://127.0.0.1:7020/data");
es.onmessage = (ev) => {
  const frame = JSON.parse(ev.data);
  console.log(frame.step, frame.panel);
};
```

心跳：每隔 ≤15 s 发送 SSE comment 行 `: ping`，防止中间设备断连（comment 不含 `step`；业务帧才带 `step`）。

---

### 4.2 `GET /joints` — 臂六轴标定角

- **Method / Path**：`GET /joints`
- **默认**：一次性 JSON（最新快照），便于 `curl` / 低频拉取。
- **可选流式**：`GET /joints?stream=1` → 与 `/data` 相同的 SSE，但 `data` 仅为 joints 载荷（**仍含 `step`**）。
- **语义**：`joints = joints_rad[0:6]`（仿射后标定角）。UI「下发」offsets/signs 后，本接口与页面条形图同步变化。原始角仍在 `/data` / `/api/status` 的 `joints_rad_raw`。

瞬时响应示例：

```json
{
  "ok": true,
  "ts": 1730000000.123,
  "step": 42,
  "connected": true,
  "unit": "rad",
  "source": "joints_rad[0:6]",
  "joints": [0.12, -0.05, 0.31, -0.02, 0.08, -0.11]
}
```

失败（未采样 / 长度不足）——**仍返回 `step`**：

```json
{
  "ok": false,
  "error": "no_sample",
  "step": 0,
  "joints": null
}
```

HTTP：业务可判 `ok`；建议始终 **200**（便于流式统一），或未连接时 **503**（二选一，实现时定一种并写进 OpenAPI）。**推荐：HTTP 200 + `ok` 字段**，与现有 `/api/*` 风格接近。

---

### 4.3 `GET /gripper` — 第七轴标定角

- **Method / Path**：`GET /gripper`
- **默认**：瞬时 JSON；`?stream=1` 可选 SSE。
- **语义**：`gripper = joints_rad[-1]`（仿射后）。

```json
{
  "ok": true,
  "ts": 1730000000.123,
  "step": 42,
  "connected": true,
  "unit": "rad",
  "source": "joints_rad[-1]",
  "gripper": 0.036886770610441
}
```

失败示例：

```json
{
  "ok": false,
  "error": "no_sample",
  "step": 0,
  "gripper": null
}
```

说明：此处「gripper」指 Gello 配置里 **第 7 个 Dynamixel 通道的标定角**，不是 DH 夹爪 Modbus 位置；与 DCS 里 gello→夹爪映射是不同层概念。
---

## 5. 与现有 API 的关系

| 现有 | 用途 | 与新接口 |
|------|------|----------|
| `GET /` | 人机 UI | 不变；UI 可继续轮询 `/api/status`，或以后改订阅 `/data` |
| `GET /api/status` | 控制台完整状态 | 保留；`/data` 的 `panel` ⊂ status |
| `POST /api/connect` 等 | 控制 | 不变；流式接口只读 |

路径命名策略：

- 短路径 `/data` `/pose` `/gripper`：给外部程序「像传感器话题一样」用。
- 若需版本化，二期可加别名 `/v1/data` 等，本设计不强制。

---

## 6. 实现要点（供后续开发，本阶段不写代码）

1. **`server.py`**  
   - 增加三个路由。  
   - 统一辅助函数例如 `_public_step(snap) -> int`，三路由都经此写出 **`step`**（来自内部 `seq`）。  
   - SSE 用 FastAPI `StreamingResponse` + async generator；从 `service.latest` / `status()` 读快照。  
   - 注意：采样在后台线程，读快照已有锁；SSE 协程里 `await asyncio.sleep` 做节流；按 `step` 去重。

2. **并发**  
   - 允许多个 `/data` 订阅者；每连接独立 generator。  
   - 不在 service 内为 SSE 另开采样线程。

3. **CORS**  
   - 若浏览器跨源订阅，可加中间件 `allow_origins=["*"]`（仅本机工具时可关）。

4. **文档 / 冒烟**  
   - README 增加三段 `curl` 示例，并说明 `step`。  
   - 测试：同一快照下 `/data` 一帧与紧随的 `/pose`、`/gripper` 的 **`step` 相等**；pose 长度 6。

5. **不做**（本设计明确排除）  
   - 不在此接口二次改写仿射或把单位改成 deg（deg 已在 status 的 `joints_deg*`）。  
   - 不把 DH 夹爪传感器混进 `/gripper`。  
   - 对外 JSON **不暴露**字段名 `seq`（仅内部；对外一律 `step`）。

---

## 7. 验收标准

1. Host 已 `connect`（含 dry_run）时：  
   - `curl -N http://127.0.0.1:7020/data` 持续输出多帧，且 `panel` 含截图同结构五字段，**每帧含递增或跳号可解释的 `step`**。  
2. `GET /joints` 返回长度为 6 的 `joints`，数值等于当时 `joints_rad[0:6]`，**且含 `step`**。  
3. `GET /gripper` 的 `gripper` 等于当时 `joints_rad[-1]`，**且含 `step`**。  
4. 对同一时刻快照：`/joints.step == /gripper.step`，并与最近一帧 `/data.step` 一致（允许差一帧采样间隔内的竞态；测试里可先停采样或连读校验单调性）。  
5. UI「下发」offsets/signs 后，`/joints` / `/gripper` 随标定角变化；`joints_rad_raw` 不变。  
6. 断开连接后 `/data` 不崩；帧内 `connected: false`，响应仍含 `step`。  
7. 原有 `/` 与 `/api/status` 行为不变（`/api/status` 可继续用内部字段 `seq`；不强制改名）。

---

## 8. 开放问题（实现前可拍板）

| # | 问题 | 建议默认 |
|---|------|----------|
| Q1 | `/pose` `/gripper` 未连接时用 200+`ok:false` 还是 503？ | **200 + `ok:false`**（仍带 `step`） |
| Q2 | `/data` 是否默认带 `joints_deg`？ | **否**（保持与截图 panel 一致；`full=1` 再扩展） |
| Q3 | 是否同步提供 WebSocket `/ws/data`？ | **一期不做** |
| Q4 | `joint_ids` 不足 7 时 gripper 定义？ | **`joints_rad[-1]`**；文档声明依赖 YAML `joint_ids` 长度 |
| Q5 | `disconnect` 后再 `connect`，`step` 重置还是继续累加？ | **重置为 0 再递增** |

---

## 9. 文档索引

| 文档 | 内容 |
|------|------|
| 本文 | 流式 / 短路径 API 设计 |
| `joints-raw-and-calibrated.md` | raw / calibrated 语义与公式 |
| `../README.md` | 启动方式（实现后应链到本文） |
