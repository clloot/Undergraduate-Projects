from machine import Pin, FPIOA
import time

# ==================== 你的引脚号 ====================
PIN_CE   = 48
PIN_CSN  = 49
PIN_SCK  = 26
PIN_MOSI = 40
PIN_MISO = 41
PIN_LED  = 20

# ==================== 1. 强制配置为 GPIO 功能 ====================
fpioa = FPIOA()
# 将所有引脚设为 GPIO 功能（对应 GPIO0~63 的编号就是功能号本身）
# 注意：K230 的 FPIOA 中，GPIO 功能号就是 GPIO 编号（例如 GPIO48 的功能号就是 48）
fpioa.set_function(PIN_CE,   fpioa.GPIO48)   # 实际是 fpioa.GPIO48，但如果有则直接写数字 48
fpioa.set_function(PIN_CSN,  fpioa.GPIO49)
fpioa.set_function(PIN_SCK,  fpioa.GPIO26)
fpioa.set_function(PIN_MOSI, fpioa.GPIO40)
fpioa.set_function(PIN_MISO, fpioa.GPIO41)
fpioa.set_function(PIN_LED,  fpioa.GPIO20)

print("FPIOA 配置完成，所有引脚已设为 GPIO 模式。")

# ==================== 2. 创建 Pin 对象并输出低电平 ====================
ce   = Pin(PIN_CE,   Pin.OUT, value=0)
csn  = Pin(PIN_CSN,  Pin.OUT, value=0)
sck  = Pin(PIN_SCK,  Pin.OUT, value=1)   # 固定高
mosi = Pin(PIN_MOSI, Pin.OUT, value=1)
miso = Pin(PIN_MISO, Pin.OUT, value=1)
led  = Pin(PIN_LED,  Pin.OUT, value=1)   # 高电平熄灭

print("初始状态：CE=0, CSN=0, 其余=1")
print("接下来 CE 和 CSN 将每隔 1 秒翻转一次，请用万用表观察变化。")
print("按板载按键（GPIO53）退出。")

key = Pin(53, Pin.IN, Pin.PULL_DOWN)
last_key = 0
toggle = False

while True:
    # 翻转 CE 和 CSN（同步翻转，便于观察）
    toggle = not toggle
    sck.value(1 if toggle else 0)
    print(f"CE/CSN 当前电平: {'高' if toggle else '低'} (应在 3.3V 和 0V 之间交替)")

    # 等待 1 秒或检测按键
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < 1000:
        cur = key.value()
        if cur == 1 and last_key == 0:
            print("按键按下，退出。")
            ce.value(0)
            csn.value(0)
            # 清理
            for p in (ce, csn, sck, mosi, miso, led):
                p.value(0)
            print("所有引脚已拉低。")
            import sys
            sys.exit()
        last_key = cur
        time.sleep_ms(10)
