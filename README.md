# gello

从 **sensors-dcs** 拆出的 **Gello（Dynamixel leader）单测上位机**：复用核心库 `hik-sensors`（`./sensors` 软链），提供独立的 **Web** 与 **PyQt** 界面，不拉起整套采集/机械臂链路。

## 布局

```text
gello/
  sensors -> ../sensors     # hik-sensors 软链（Windows 可用目录联接）
  configs/
    default.yaml            # 采样 Hz / Web 端口
    sensors_gello.yaml      # kind=gello 设备与仿射
  src/gello_app/
    service.py              # SensorManager + 采样环
    web/                    # FastAPI + 静态页
    qt/                     # PyQt5 桌面壳
```

## 安装

```powershell
cd C:\Users\user\Desktop\autodl-tmp
.\.venv\Scripts\Activate.ps1   # 若 ExecutionPolicy 拦截见启动与密码.txt
pip install -e ./gello
# 真机再装：
pip install -e "./gello[hardware]"
# 或：pip install -e "./sensors[dynamixel,serial]"
```

确保 `gello/sensors` 指向仓库 `sensors`（bootstrap 已建联接）。

## 启动

```powershell
cd C:\Users\user\Desktop\autodl-tmp\gello

# Web（默认 http://127.0.0.1:7020/）
gello web -c configs/default.yaml

# PyQt 桌面（可在窗口内再启 Web / 打开浏览器）
gello desktop -c configs/default.yaml

# 仅 probe / 列串口
gello probe -c configs/default.yaml
gello ports -c configs/default.yaml
```

真机：

1. 编辑 `configs/sensors_gello.yaml`：`dry_run: false`，`endpoint` 改为本机 COM（如 `COM10`）  
2. `configs/default.yaml` 同步 `dry_run: false`，或启动时加 `--no-dry-run`

## 能力

| 功能 | 说明 |
|------|------|
| 连接 / 断开 | `SensorManager` 打开 `kind=gello` |
| dry_run 合成关节 | 无硬件时可看条与仿射 |
| 标定关节 | `q=(q_raw-offsets)*signs`（与 DCS 一致） |
| Probe | 串口 / FTDI / dynamixel_sdk 检查 |
| 测速 | `probe_max_read_hz`（需已连接） |
| 对外流式 API | `GET /data`（SSE）、`GET /joints`、`GET /gripper`（均含 `step`） |

不做：录制、夹爪同步、机械臂摇操（仍在 sensors-dcs）。

## 对外数据接口

接口说明见 [`data-export/tcp-api-interface.md`](data-export/tcp-api-interface.md)。设计见 [`docs/streaming-endpoints-design.md`](docs/streaming-endpoints-design.md)。关节语义见 [`docs/joints-raw-and-calibrated.md`](docs/joints-raw-and-calibrated.md)。

| 路径 | 说明 |
|------|------|
| `GET /data` | SSE 推送面板同构 JSON（`panel` + `ts`/`step`）；`?hz=20`、`?full=1` |
| `GET /joints` | 瞬时 JSON：`joints = joints_rad[0:6]`（标定角）+ `step`；`?stream=1` 可 SSE |
| `GET /gripper` | 瞬时 JSON：`gripper = joints_rad[-1]`（标定角）+ `step`；`?stream=1` 可 SSE |

`step` = 采样环序列号（内部 `seq`）；每次 `POST /api/connect` 从 0 重新计数。控制台仍用 `/api/*`。

```powershell
# 先在 UI 或 API 连接 dry_run，再：
curl.exe -N "http://127.0.0.1:7020/data?hz=5"
curl.exe "http://127.0.0.1:7020/joints"
curl.exe "http://127.0.0.1:7020/gripper"
```

## 端口

默认 Web **7020**（避开 view 6008 / dcs 7011 / embody 6006）。
