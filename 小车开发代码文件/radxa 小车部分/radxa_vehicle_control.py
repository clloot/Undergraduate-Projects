#!/usr/bin/env python3
"""
Radxa ZERO 3 车辆控制程序

硬件映射（物理针脚）：
    TB6612 PWMA -> 16 -> PWM8_M0 -> /sys/class/pwm/pwmchip0/pwm0
    TB6612 AIN1 -> 11 -> gpiochip3 line 1
    TB6612 AIN2 -> 13 -> gpiochip3 line 2
    舵机信号    -> 18 -> PWM9_M0 -> /sys/class/pwm/pwmchip1/pwm0

默认是键盘模式，便于先测试电机和舵机；按 M 切换到 ESP32 BLE 模式。

键盘：
    F 前进       R 倒车
    W 加速       S 减速
    A 左转       D 右转
    C 回中       空格/X 急停
    M 切换键盘/ESP32模式
    H 帮助       Q 停止并退出

注意：本程序需要访问 /sys/class/pwm 和 /dev/gpiochip，建议使用 sudo 运行。
"""

import asyncio
import os
import sys
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path


# ---------------- BLE 设置 ----------------
DEVICE_NAME = "ESP32-S3-ADC"
CHARACTERISTIC_UUID = "87654321-4321-4321-4321-cba987654321"
SCAN_TIMEOUT_SECONDS = 20.0
RECONNECT_DELAY_SECONDS = 3.0
DATA_TIMEOUT_SECONDS = 2.0

# 摄像头网页程序通过这个状态文件判断显示前摄还是后摄。
# 内容：1=前进，0=空挡/停止，-1=倒车。
CAMERA_DIRECTION_FILE = Path("/tmp/radxa_vehicle_direction")


# ---------------- Radxa GPIO/PWM 设置 ----------------
GPIO_CHIP = "/dev/gpiochip3"
AIN1_LINE = 1       # 物理针脚11，GPIO3_A1
AIN2_LINE = 2       # 物理针脚13，GPIO3_A2
MOTOR_PWM_CHIP = "/sys/class/pwm/pwmchip0"  # 物理针脚16，PWM8_M0
SERVO_PWM_CHIP = "/sys/class/pwm/pwmchip1"  # 物理针脚18，PWM9_M0
PWM_CHANNEL = 0


# ---------------- 控制参数 ----------------
MAX_SPEED = 1.0
# 挂入前进或倒车挡、且没有踩油门/刹车时的怠速。
IDLE_SPEED = 0.20
KEYBOARD_SPEED_STEP = 0.10
KEYBOARD_ANGLE_STEP = 10.0
MOTOR_CHANGE_RATE_PER_SECOND = 0.80
CONTROL_INTERVAL_SECONDS = 0.02

SERVO_MIN_ANGLE = -90.0
SERVO_MAX_ANGLE = 90.0
SERVO_MIN_PULSE_NS = 600_000       # 0.6ms
SERVO_CENTER_PULSE_NS = 1_500_000  # 1.5ms
SERVO_MAX_PULSE_NS = 2_400_000     # 2.4ms

MAX_PEDAL_VOLTAGE = 2.0
PEDAL_DEAD_ZONE_VOLTAGE = 0.10
STEERING_LEFT_VOLTAGE = 0.20
STEERING_CENTER_VOLTAGE = 1.56
STEERING_RIGHT_VOLTAGE = 3.00
STEERING_CENTER_DEAD_ZONE = 0.10
STEERING_REVERSED = False


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def move_towards(current: float, target: float, maximum_change: float) -> float:
    if current < target:
        return min(current + maximum_change, target)
    if current > target:
        return max(current - maximum_change, target)
    return current


class SysfsPwm:
    """通过 Linux sysfs 控制一个硬件 PWM 通道。"""

    def __init__(self, chip_path: str, period_ns: int):
        self.chip = Path(chip_path)
        self.channel = self.chip / f"pwm{PWM_CHANNEL}"
        self.period_ns = period_ns
        self.polarity = "normal"
        self.current_duty_ns = 0
        self.enabled = False

        if not self.chip.exists():
            raise RuntimeError(f"PWM设备不存在：{self.chip}")

        if not self.channel.exists():
            try:
                (self.chip / "export").write_text(str(PWM_CHANNEL))
            except PermissionError as error:
                raise RuntimeError(
                    f"无法导出 {self.channel}，请使用 sudo 运行程序"
                ) from error

            for _ in range(50):
                if self.channel.exists():
                    break
                time.sleep(0.01)

        if not self.channel.exists():
            raise RuntimeError(f"无法创建 PWM 通道：{self.channel}")

        # Rockchip 驱动在 period=0 时，写 enable=0 也会返回 EINVAL。
        # 只有当前已经启用时才先关闭；然后设置 period 和 duty。
        try:
            enabled = int((self.channel / "enable").read_text().strip() or "0")
        except (OSError, ValueError):
            enabled = 0
        if enabled:
            self._write("enable", 0)
        # 读取设备树/驱动实际提供的极性。Radxa Rockchip 驱动通常不允许
        # 运行时修改 polarity，因此这里不能强行写入 normal。
        try:
            current_polarity = (self.channel / "polarity").read_text().strip()
        except OSError:
            current_polarity = "normal"
        if current_polarity in ("normal", "inversed"):
            self.polarity = current_polarity
        self._write("period", period_ns)
        self._write("duty_cycle", 0)

    def _write(self, name: str, value: int | str) -> None:
        try:
            # enable/period/duty_cycle 使用整数，polarity 使用字符串。
            text = str(value) if name == "polarity" else str(int(value))
            (self.channel / name).write_text(text)
        except PermissionError as error:
            raise RuntimeError(
                f"无法写入 {self.channel / name}，请使用 sudo 运行程序"
            ) from error

    def set_duty_ns(self, duty_ns: int, keep_enabled: bool = False) -> None:
        duty_ns = int(clamp(duty_ns, 0, self.period_ns))
        # 数值没有变化时不重复写 sysfs，避免舵机不断重启 PWM 而抖动。
        if duty_ns == self.current_duty_ns and (
            duty_ns == 0 or self.enabled
        ):
            return

        if duty_ns <= 0 and not keep_enabled:
            if self.enabled:
                self._write("enable", 0)
                self.enabled = False
            if self.current_duty_ns != 0:
                self._write("duty_cycle", 0)
            self.current_duty_ns = 0
            return

        if not self.enabled:
            self._write("duty_cycle", duty_ns)
            self._write("enable", 1)
            self.enabled = True
        elif duty_ns != self.current_duty_ns:
            # 大多数内核允许 PWM 运行时修改 duty；若驱动拒绝，回退到
            # 关闭、修改、重新开启的方式。
            try:
                self._write("duty_cycle", duty_ns)
            except OSError:
                self._write("enable", 0)
                self.enabled = False
                self._write("duty_cycle", duty_ns)
                self._write("enable", 1)
                self.enabled = True
        self.current_duty_ns = duty_ns

    def set_duty_ratio(self, ratio: float, keep_enabled: bool = False) -> None:
        self.set_duty_ns(
            round(clamp(ratio, 0.0, 1.0) * self.period_ns),
            keep_enabled=keep_enabled,
        )

    def disable(self) -> None:
        if self.channel.exists():
            try:
                try:
                    enabled = int((self.channel / "enable").read_text().strip() or "0")
                except (OSError, ValueError):
                    enabled = 0
                if enabled:
                    self._write("enable", 0)
                self.enabled = False
                self._write("duty_cycle", 0)
                self.current_duty_ns = 0
            except (PermissionError, OSError):
                pass


class RadxaGpio:
    """兼容 libgpiod v1/v2 的两个数字输出。"""

    def __init__(self):
        try:
            import gpiod
        except ImportError as error:
            raise RuntimeError(
                "缺少 gpiod，请执行：sudo apt install -y python3-gpiod"
            ) from error

        self.gpiod = gpiod
        self.v2 = hasattr(gpiod, "request_lines")

        if self.v2:
            from gpiod.line import Direction, Value

            self.Direction = Direction
            self.Value = Value
            settings = gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.INACTIVE,
            )
            self.request = gpiod.request_lines(
                GPIO_CHIP,
                consumer="radxa-vehicle-control",
                config={AIN1_LINE: settings, AIN2_LINE: settings},
            )
        else:
            self.chip = gpiod.Chip(GPIO_CHIP)
            self.lines = self.chip.get_lines([AIN1_LINE, AIN2_LINE])
            self.lines.request(
                consumer="radxa-vehicle-control",
                type=gpiod.LINE_REQ_DIR_OUT,
                default_vals=[0, 0],
            )

    def set_direction(self, direction: int) -> None:
        if direction == 1:
            values = (1, 0)
        elif direction == -1:
            values = (0, 1)
        else:
            values = (0, 0)

        if self.v2:
            self.request.set_values({
                AIN1_LINE: self.Value.ACTIVE if values[0] else self.Value.INACTIVE,
                AIN2_LINE: self.Value.ACTIVE if values[1] else self.Value.INACTIVE,
            })
        else:
            self.lines.set_values(list(values))

    def close(self) -> None:
        self.set_direction(0)
        if self.v2:
            self.request.release()
        else:
            self.lines.release()
            self.chip.close()


class RadxaHardware:
    """电机和舵机的统一硬件接口。"""

    def __init__(self):
        self.gpio = RadxaGpio()
        self.motor_pwm = SysfsPwm(MOTOR_PWM_CHIP, 1_000_000)  # 1kHz
        self.servo_pwm = SysfsPwm(SERVO_PWM_CHIP, 20_000_000)  # 50Hz
        self.set_motor(0, 0.0)
        self.set_servo_angle(0.0)

    def set_motor(self, direction: int, speed: float) -> None:
        speed = clamp(speed, 0.0, 1.0)
        if direction == 0 or speed <= 0.001:
            self.gpio.set_direction(0)
            self.motor_pwm.set_duty_ratio(0.0)
            return

        self.gpio.set_direction(direction)
        # TB6612 的 PWMA 需要高电平有效。如果设备树把 PWM 配成
        # inversed，则 sysfs duty 越大，实际高电平时间反而越短。
        pwm_ratio = speed
        if self.motor_pwm.polarity == "inversed":
            pwm_ratio = 1.0 - speed
        # 即使反相后 duty=0，也必须保持 PWM enabled，才能输出 100% 高电平。
        self.motor_pwm.set_duty_ratio(pwm_ratio, keep_enabled=True)

    def set_servo_angle(self, angle: float) -> None:
        angle = clamp(angle, SERVO_MIN_ANGLE, SERVO_MAX_ANGLE)
        ratio = (angle - SERVO_MIN_ANGLE) / (
            SERVO_MAX_ANGLE - SERVO_MIN_ANGLE
        )
        pulse_ns = SERVO_MIN_PULSE_NS + ratio * (
            SERVO_MAX_PULSE_NS - SERVO_MIN_PULSE_NS
        )
        if self.servo_pwm.polarity == "inversed":
            pulse_ns = self.servo_pwm.period_ns - pulse_ns
        self.servo_pwm.set_duty_ns(round(pulse_ns), keep_enabled=True)

    def close(self) -> None:
        self.gpio.set_direction(0)
        self.motor_pwm.disable()
        self.servo_pwm.disable()
        self.gpio.close()


@dataclass
class BleState:
    accelerator: float = 0.0
    brake: float = 0.0
    steering: float = STEERING_CENTER_VOLTAGE
    reverse: bool = False
    forward: bool = False
    direction: int = 0
    # 从一个行驶方向切换到相反方向时，先进入空挡；记录等待确认的方向。
    pending_direction: int = 0
    connected: bool = False
    last_data_time: float = 0.0


@dataclass
class KeyboardState:
    # 默认直接使用 ESP32，按 M 可切换为键盘测试模式。
    mode_keyboard: bool = False
    direction: int = 0
    speed: float = 0.0
    angle: float = 0.0


ble_state = BleState()
keyboard_state = KeyboardState()
previous_reverse = False
previous_forward = False
hardware = None
shutdown_event = None
last_published_camera_direction = None


def publish_camera_direction(direction: int, force: bool = False) -> None:
    """把当前挡位发布给独立运行的双摄像头网页程序。"""

    global last_published_camera_direction
    direction = direction if direction in (-1, 0, 1) else 0
    if not force and direction == last_published_camera_direction:
        return

    try:
        CAMERA_DIRECTION_FILE.write_text(str(direction), encoding="ascii")
        CAMERA_DIRECTION_FILE.chmod(0o644)
        last_published_camera_direction = direction
    except OSError as error:
        print(f"无法更新摄像头挡位状态：{error}")


def update_direction_latch(reverse: bool, forward: bool) -> None:
    global previous_reverse, previous_forward

    reverse_rising = reverse and not previous_reverse
    forward_rising = forward and not previous_forward

    if reverse and forward:
        # 两个方向同时有效属于冲突输入，立即进入空挡并取消待确认方向。
        ble_state.direction = 0
        ble_state.pending_direction = 0
    elif reverse_rising:
        request_direction(-1)
    elif forward_rising:
        request_direction(1)

    previous_reverse = reverse
    previous_forward = forward


def request_direction(requested: int) -> None:
    """处理一次挡位信号上升沿，切换时强制经过空挡确认。"""

    current = ble_state.direction

    if current == requested:
        # 已经在这个挡位，重复触发不改变状态。
        ble_state.pending_direction = 0
        return

    if current != 0:
        # 行驶中请求相反方向：第一次只进入空挡。
        ble_state.direction = 0
        ble_state.pending_direction = requested
        name = "倒车" if requested == -1 else "前进"
        print(f"ESP32挡位：空挡；再次触发{name}才会挂挡")
        return

    if ble_state.pending_direction in (0, requested):
        # 启动空挡可以直接挂挡；方向切换后的空挡要求再次确认同一方向。
        ble_state.direction = requested
        ble_state.pending_direction = 0
        name = "倒车" if requested == -1 else "前进"
        print(f"ESP32挡位：{name}（怠速 {IDLE_SPEED * 100:.0f}%）")
        return

    # 空挡中改选另一个方向：保持空挡，并等待再次触发新方向。
    ble_state.pending_direction = requested
    name = "倒车" if requested == -1 else "前进"
    print(f"ESP32挡位：保持空挡；再次触发{name}才会挂挡")


def steering_to_angle(voltage: float) -> float:
    voltage = clamp(
        voltage,
        STEERING_LEFT_VOLTAGE,
        STEERING_RIGHT_VOLTAGE,
    )

    if abs(voltage - STEERING_CENTER_VOLTAGE) <= STEERING_CENTER_DEAD_ZONE:
        return 0.0

    if voltage < STEERING_CENTER_VOLTAGE:
        ratio = (
            STEERING_CENTER_VOLTAGE - voltage
        ) / (
            STEERING_CENTER_VOLTAGE - STEERING_LEFT_VOLTAGE
        )
        angle = -90.0 * ratio
    else:
        ratio = (
            voltage - STEERING_CENTER_VOLTAGE
        ) / (
            STEERING_RIGHT_VOLTAGE - STEERING_CENTER_VOLTAGE
        )
        angle = 90.0 * ratio

    return -angle if STEERING_REVERSED else angle


def pedal_target_speed() -> tuple[float, bool]:
    accelerator = clamp(
        (ble_state.accelerator - PEDAL_DEAD_ZONE_VOLTAGE)
        / (MAX_PEDAL_VOLTAGE - PEDAL_DEAD_ZONE_VOLTAGE),
        0.0,
        1.0,
    )
    brake = clamp(
        (ble_state.brake - PEDAL_DEAD_ZONE_VOLTAGE)
        / (MAX_PEDAL_VOLTAGE - PEDAL_DEAD_ZONE_VOLTAGE),
        0.0,
        1.0,
    )

    if ble_state.brake > PEDAL_DEAD_ZONE_VOLTAGE:
        return IDLE_SPEED * (1.0 - brake), True
    if ble_state.accelerator > PEDAL_DEAD_ZONE_VOLTAGE:
        return IDLE_SPEED + accelerator * (MAX_SPEED - IDLE_SPEED), True
    return IDLE_SPEED, False


def handle_ble_data(_sender, data: bytearray) -> None:
    try:
        parts = data.decode("utf-8").strip().split(",")
        if len(parts) != 5:
            raise ValueError(f"expected 5 values, received {len(parts)}")

        ble_state.accelerator = clamp(float(parts[0]), 0.0, MAX_PEDAL_VOLTAGE)
        ble_state.brake = clamp(float(parts[1]), 0.0, MAX_PEDAL_VOLTAGE)
        ble_state.steering = float(parts[2])
        ble_state.reverse = bool(int(parts[3]))
        ble_state.forward = bool(int(parts[4]))
        update_direction_latch(ble_state.reverse, ble_state.forward)
        ble_state.last_data_time = time.monotonic()
    except (UnicodeDecodeError, ValueError) as error:
        print(f"无效 BLE 数据 {data!r}: {error}")


def print_help() -> None:
    print("\n键盘：F前进 R倒车 W加速 S减速 N/0停止挡 A左转 D右转 C回中 空格/X急停")
    print("M切换键盘/ESP32模式 H帮助 Q停止退出\n")


def print_keyboard_state() -> None:
    direction = {1: "前进", -1: "倒车", 0: "停止"}[keyboard_state.direction]
    print(
        f"模式:键盘 方向:{direction} "
        f"速度:{keyboard_state.speed * 100:.0f}% "
        f"角度:{keyboard_state.angle:+.0f}°"
    )


def handle_key(key: str) -> bool:
    key = key.lower()

    if key == "q":
        keyboard_state.direction = 0
        keyboard_state.speed = 0.0
        if shutdown_event is not None:
            shutdown_event.set()
        return False
    if key == "h":
        print_help()
        return True
    if key == "m":
        keyboard_state.mode_keyboard = not keyboard_state.mode_keyboard
        keyboard_state.direction = 0
        keyboard_state.speed = 0.0
        if keyboard_state.mode_keyboard:
            print("已切换到键盘模式")
            print_keyboard_state()
        else:
            print("已切换到 ESP32 BLE 模式，等待蓝牙数据")
        return True
    if not keyboard_state.mode_keyboard:
        return True

    if key == "f":
        keyboard_state.direction = 1
    elif key == "r":
        keyboard_state.direction = -1
    elif key in ("n", "0"):
        # 空挡/停止挡：同时清除方向和速度，防止松开方向键后仍保持驱动。
        keyboard_state.direction = 0
        keyboard_state.speed = 0.0
    elif key == "w":
        keyboard_state.speed = clamp(
            keyboard_state.speed + KEYBOARD_SPEED_STEP,
            0.0,
            MAX_SPEED,
        )
    elif key == "s":
        keyboard_state.speed = clamp(
            keyboard_state.speed - KEYBOARD_SPEED_STEP,
            0.0,
            MAX_SPEED,
        )
    elif key == "a":
        keyboard_state.angle = clamp(
            keyboard_state.angle - KEYBOARD_ANGLE_STEP,
            SERVO_MIN_ANGLE,
            SERVO_MAX_ANGLE,
        )
    elif key == "d":
        keyboard_state.angle = clamp(
            keyboard_state.angle + KEYBOARD_ANGLE_STEP,
            SERVO_MIN_ANGLE,
            SERVO_MAX_ANGLE,
        )
    elif key == "c":
        keyboard_state.angle = 0.0
    elif key in (" ", "x"):
        keyboard_state.direction = 0
        keyboard_state.speed = 0.0
    else:
        return True

    print_keyboard_state()
    return True


async def keyboard_loop() -> None:
    if not sys.stdin.isatty():
        print("没有交互式终端，键盘控制不可用。")
        await asyncio.Future()
        return

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def input_ready() -> None:
        try:
            data = os.read(fd, 32).decode("utf-8", errors="ignore")
            for key in data:
                queue.put_nowait(key)
        except OSError:
            pass

    try:
        tty.setcbreak(fd)
        loop.add_reader(fd, input_ready)
        print_help()
        if keyboard_state.mode_keyboard:
            print_keyboard_state()
        else:
            print("默认模式: ESP32 BLE；按 M 可切换到键盘测试模式")
        while not shutdown_event.is_set():
            key = await queue.get()
            if not handle_key(key):
                return
    finally:
        loop.remove_reader(fd)
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


async def control_loop() -> None:
    current_speed = 0.0
    current_direction = 0
    current_angle = 0.0
    last_report = None

    while not shutdown_event.is_set():
        now = time.monotonic()

        if keyboard_state.mode_keyboard:
            target_direction = keyboard_state.direction
            target_speed = keyboard_state.speed if target_direction else 0.0
            target_angle = keyboard_state.angle
            speed_rate = MOTOR_CHANGE_RATE_PER_SECOND
            status = "keyboard"
        else:
            fresh = (
                ble_state.connected
                and ble_state.last_data_time > 0
                and now - ble_state.last_data_time <= DATA_TIMEOUT_SECONDS
            )
            if fresh:
                target_direction = ble_state.direction
                target_speed, _ = pedal_target_speed()
                if target_direction == 0:
                    target_speed = 0.0
                target_angle = steering_to_angle(ble_state.steering)
                speed_rate = MOTOR_CHANGE_RATE_PER_SECOND
                status = "esp32"
            else:
                target_direction = 0
                target_speed = 0.0
                target_angle = 0.0
                speed_rate = MOTOR_CHANGE_RATE_PER_SECOND
                status = "failsafe"

        publish_camera_direction(target_direction)

        if target_direction != current_direction:
            current_speed = 0.0
            hardware.set_motor(0, 0.0)
            current_direction = target_direction

        current_speed = move_towards(
            current_speed,
            target_speed,
            speed_rate * CONTROL_INTERVAL_SECONDS,
        )
        current_angle = move_towards(
            current_angle,
            target_angle,
            180.0 * CONTROL_INTERVAL_SECONDS,
        )

        hardware.set_motor(current_direction, current_speed)
        hardware.set_servo_angle(current_angle)

        report = (status, target_direction, round(target_speed, 2), round(target_angle, 1))
        if report != last_report:
            print(
                f"状态:{status} 方向:{target_direction:+d} "
                f"速度:{current_speed * 100:.0f}% 角度:{current_angle:+.1f}°"
            )
            last_report = report

        await asyncio.sleep(CONTROL_INTERVAL_SECONDS)


async def ble_loop() -> None:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        print("未安装 bleak；键盘模式仍可用，ESP32模式不可用。")
        await asyncio.Future()
        return

    global previous_reverse, previous_forward

    while not shutdown_event.is_set():
        try:
            print(f"扫描 {DEVICE_NAME}...")
            device = await BleakScanner.find_device_by_name(
                DEVICE_NAME,
                timeout=SCAN_TIMEOUT_SECONDS,
            )
            if device is None:
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
                continue

            disconnected = asyncio.Event()
            event_loop = asyncio.get_running_loop()

            def on_disconnect(_client):
                ble_state.connected = False
                event_loop.call_soon_threadsafe(disconnected.set)

            async with BleakClient(
                device,
                disconnected_callback=on_disconnect,
            ) as client:
                ble_state.connected = True
                ble_state.last_data_time = 0.0
                ble_state.direction = 0
                ble_state.pending_direction = 0
                previous_reverse = False
                previous_forward = False
                print("ESP32 BLE 已连接")
                await client.start_notify(CHARACTERISTIC_UUID, handle_ble_data)
                await disconnected.wait()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"BLE错误：{error}")

        ble_state.connected = False
        ble_state.last_data_time = 0.0
        ble_state.direction = 0
        ble_state.pending_direction = 0
        previous_reverse = False
        previous_forward = False
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


async def main() -> None:
    global hardware, shutdown_event
    shutdown_event = asyncio.Event()
    publish_camera_direction(0, force=True)
    hardware = RadxaHardware()

    tasks = [
        asyncio.create_task(keyboard_loop()),
        asyncio.create_task(control_loop()),
        asyncio.create_task(ble_loop()),
    ]

    try:
        await shutdown_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        publish_camera_direction(0, force=True)
        hardware.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在安全停止")
    except Exception as error:
        print(f"启动失败：{error}")
