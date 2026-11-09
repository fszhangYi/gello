# Gello TCP 数据接口

绑定 `127.0.0.1:7020`。传输是 HTTP over TCP。下面四个短路径不要登录。`/api/*` 是控制台，不在本文展开。

采样环每成功产出一帧，内部 `seq` 加 1。对外字段名是 `step`，等于当时的 `seq`。同一次快照上，`/data`、`/joints`、`/gripper` 的 `step` 相同。`POST /api/connect` 之后从 0 再计，下一帧为 1。断开后 `step` 停在最后一帧。还没有采样时 `step` 为 0。

JSON 为 ASCII。没有样本时 HTTP 200，`ok` 为 false。

## GET /data

持续推送，`Content-Type: text/event-stream`。事件名 `gello`。

| 查询 | 含义 |
|---|---|
| `hz` | 最高推送频率，默认 20，范围 1–200，且不超过采样频率（默认 50） |
| `full` | `1` 时多带控制台 `status`。默认 `0` |

`step` 没变不重复推。约 15 秒一条 `: ping`，注释行没有 `step`。

```json
{"ts":0.0,"step":1,"connected":true,"dry_run":false,"synth":false,"panel":{"joint_ids":[1,2,3,4,5,6,7],"joints_rad":[],"joints_rad_raw":[],"joint_offsets":[],"joint_signs":[]}}
```

`panel.joints_rad` 是仿射后的标定角。`joints_rad_raw` 是原始角。

## GET /joints

默认一次 JSON。`?stream=1` 改为上面的 SSE，查询 `hz` 与 `/data` 相同。

`joints` 是 `joints_rad` 的前 6 个，单位 rad。长度不足 6 时 `ok` 为 false，`joints` 为 null，`error` 为 `need_6_joints` 或 `no_sample`。

```json
{"ok":true,"ts":0.0,"step":1,"connected":true,"unit":"rad","source":"joints_rad[0:6]","joints":[0.0,0.0,0.0,0.0,0.0,0.0]}
```

## GET /gripper

默认一次 JSON。`?stream=1` 改为 SSE。

`gripper` 是 `joints_rad` 的最后一个数。慧灵行程映射下单位是 mm。没有样本时 `ok` 为 false，`gripper` 为 null，`error` 为 `no_sample`。

```json
{"ok":true,"ts":0.0,"step":1,"connected":true,"unit":"mm","source":"joints_rad[-1]","gripper":0.0}
```

## 示例

```bash
curl -N "http://127.0.0.1:7020/data?hz=5"
curl "http://127.0.0.1:7020/joints"
curl "http://127.0.0.1:7020/gripper"
```
