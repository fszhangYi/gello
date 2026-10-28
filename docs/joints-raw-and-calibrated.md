# Gello 原始角与标定角说明

本文整理上位机「原始 / 标定 (rad)」面板中各字段含义，以及原始角的计算方式。实现位于核心库 `hik-sensors`（`sensors`）的 `kind=gello` 驱动，本仓库上位机只做展示与 dry_run 合成。

## 1. 面板字段含义

界面区块标题一般为「原始 / 标定 (rad)」，单位均为**弧度**。

| 字段 | 含义 |
|------|------|
| `joint_ids` | Dynamixel 舵机 ID，常见为 `1..7`（6 轴臂 + 1 路夹爪/第七关节） |
| `joints_rad_raw` | **原始角**：编码器读数换算成弧度后、**未做仿射** 的值 |
| `joints_rad` | **标定角**：给跟随 / 可视化 / 导出用的关节角 |
| `joint_offsets` | 各轴零位偏置（来自 YAML，如 `configs/sensors_gello.yaml`） |
| `joint_signs` | 各轴方向：`+1` 同向，`-1` 反向 |

### 标定换算

与 sensors-dcs / hik-sensors 一致：

\[
q = (q_{\mathrm{raw}} - \mathrm{offsets}) \times \mathrm{signs}
\]

示例（第 1 轴）：若 `joints_rad_raw[0] ≈ 1.6887`、`joint_offsets[0] ≈ 1.5708`、`joint_signs[0] = 1`，则

\[
q_0 \approx (1.6887 - 1.5708) \times 1 \approx 0.1179
\]

即面板中的 `joints_rad[0]`。

`offsets` 中常见 \(\pi\)、\(2\pi\)、\(3\pi/2\) 等，用于把舵机机械零位对齐到控制/示教约定零位；`signs` 中若某轴为 `-1`，表示该轴编码器增大方向与标定角相反。

### 使用建议

| 目的 | 看哪个字段 |
|------|------------|
| 调试总线 / 舵机读数 | `joints_rad_raw`（及驱动返回的 `joints_raw_ticks`） |
| 跟臂、画条、对齐 | `joints_rad`（对外 `GET /joints`、`GET /gripper` 也取标定角） |
| 改标定 | UI 下发 / 同步 YAML 的 `joint_offsets` / `joint_signs`，或直接改配置文件 |

---

## 2. 原始角是怎么算出来的？

原始角 `joints_rad_raw` 在 `sensors` 的 Gello 驱动中计算，**不经过** offsets / signs。

源码：`sensors/src/sensors/drivers/gello/leader.py` → `GelloLeaderSensor.read()`。

### 2.1 真机路径

1. Dynamixel Protocol 2.0 **GroupSyncRead** 读取各舵机 **Present Position**（地址 `132`，长度 4 字节）→ 得到整数 `ticks`。
2. 将 tick 换算为弧度：

\[
q_{\mathrm{raw}} = \frac{\mathrm{ticks}}{2048}\cdot\pi
\]

对应实现：

```python
rad = np.array(ticks, dtype=float) / 2048.0 * np.pi
# ...
calibrated = (rad - offsets) * signs
return {
    "joints_raw_ticks": ticks,
    "joints_rad": calibrated.tolist(),
    "joints_rad_raw": rad.tolist(),
    ...
}
```

含义简述：Dynamixel 位置一圈按 **4096 tick** 计，半圈 **2048 tick** \(=\pi\) rad，因此使用 `/2048 * π`。

| 量 | 公式 |
|----|------|
| 原始角 `joints_rad_raw` | \(\mathrm{ticks}/2048\cdot\pi\)（仅 tick→弧度） |
| 标定角 `joints_rad` | \((q_{\mathrm{raw}} - \mathrm{offsets})\times\mathrm{signs}\) |

### 2.2 dry_run（无硬件）路径

驱动不读串口时，返回的 `joints_rad` / `joints_rad_raw` 为空。本仓库 `gello` 上位机（`gello_app.service`）会：

1. 合成一串标定角（正弦演示）；
2. **反算**原始角，以便 UI 仍能对比仿射前后：

\[
q_{\mathrm{raw}} = \frac{q}{\mathrm{signs}} + \mathrm{offsets}
\]

因此 dry_run 下的「原始角」**不是**编码器读数，而是为展示仿射关系而反推的值。

---

## 3. 相关文件

| 路径 | 作用 |
|------|------|
| `sensors/src/sensors/drivers/gello/leader.py` | 真机读 tick、算 raw / calibrated |
| `gello/src/gello_app/affine.py` | 仿射正/逆变换（与 DCS `GelloAgent` 同语义） |
| `gello/src/gello_app/service.py` | 采样环；dry_run 合成与反算 |
| `gello/configs/sensors_gello.yaml` | `joint_offsets` / `joint_signs` / 串口等 |
