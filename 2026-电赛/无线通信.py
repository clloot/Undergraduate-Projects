from machine import Pin
import time

# ==================== 引脚配置 ====================
PIN_CE = 48
PIN_CSN = 49
PIN_SCK = 26
PIN_MOSI = 40
PIN_MISO = 41
PIN_KEY = 53
LED_GREEN = 20   # 绿色 LED（低电平点亮）

# ==================== 软件 SPI ====================
class SoftSPI:
    def __init__(self, sck, mosi, miso):
        self.sck = Pin(sck, Pin.OUT, value=0)
        self.mosi = Pin(mosi, Pin.OUT, value=0)
        self.miso = Pin(miso, Pin.IN)

    def _transfer_byte(self, tx_byte):
        rx_byte = 0
        for i in range(7, -1, -1):
            self.sck.value(0)
            self.mosi.value((tx_byte >> i) & 1)
            self.sck.value(1)
            if self.miso.value():
                rx_byte |= (1 << i)
        self.sck.value(0)
        return rx_byte

    def write(self, data):
        if isinstance(data, bytes):
            for b in data:
                self._transfer_byte(b)
        else:
            self._transfer_byte(data)

    def read(self, nbytes):
        return bytes([self._transfer_byte(0xFF) for _ in range(nbytes)])

    def write_read(self, data, nbytes):
        if isinstance(data, bytes):
            for b in data:
                self._transfer_byte(b)
        else:
            self._transfer_byte(data)
        return self.read(nbytes)

# ==================== nRF24L01 驱动 ====================
CONFIG = 0x00
EN_AA = 0x01
EN_RXADDR = 0x02
SETUP_AW = 0x03
SETUP_RETR = 0x04
RF_CH = 0x05
RF_SETUP = 0x06
STATUS = 0x07
RX_ADDR_P0 = 0x0A
TX_ADDR = 0x10
RX_PW_P0 = 0x11
FIFO_STATUS = 0x17

class NRF24L01:
    def __init__(self, spi, csn, ce, channel=100, payload_size=32):
        self.spi = spi
        self.csn = Pin(csn, Pin.OUT, value=1)
        self.ce = Pin(ce, Pin.OUT, value=0)
        self.payload_size = payload_size
        self.channel = channel
        self._init_nrf()

    def _write_reg(self, reg, value):
        self.csn.value(0)
        self.spi.write(bytes([reg & 0x1F, value]))
        self.csn.value(1)

    def _read_reg(self, reg):
        self.csn.value(0)
        self.spi.write(bytes([(reg & 0x1F) | 0x20]))
        res = self.spi.read(1)
        self.csn.value(1)
        return res[0]

    def _write_buf(self, reg, buf):
        self.csn.value(0)
        self.spi.write(bytes([reg & 0x1F]))
        self.spi.write(buf)
        self.csn.value(1)

    def _read_buf(self, reg, length):
        self.csn.value(0)
        self.spi.write(bytes([(reg & 0x1F) | 0x20]))
        data = self.spi.read(length)
        self.csn.value(1)
        return data

    def _init_nrf(self):
        self.ce.value(0)
        self._write_reg(EN_AA, 0x01)
        self._write_reg(EN_RXADDR, 0x01)
        self._write_reg(SETUP_AW, 0x03)
        self._write_reg(SETUP_RETR, 0x0F)  # 重发15次，延迟250us
        self._write_reg(RF_CH, self.channel)
        self._write_reg(RF_SETUP, 0x06)    # 1Mbps, 0dBm
        addr = b'\xE7\xE7\xE7\xE7\xE7'
        self._write_buf(RX_ADDR_P0, addr)
        self._write_buf(TX_ADDR, addr)
        self._write_reg(RX_PW_P0, self.payload_size)
        self._write_reg(STATUS, 0x70)
        time.sleep_ms(2)

    def set_rx_mode(self):
        self.ce.value(0)
        self._write_reg(CONFIG, 0x0F)
        self.ce.value(1)
        time.sleep_ms(2)

    def set_tx_mode(self):
        self.ce.value(0)
        time.sleep_ms(2)
        self._write_reg(CONFIG, 0x0E)
        time.sleep_ms(2)

    def send(self, data):
        if len(data) > self.payload_size:
            data = data[:self.payload_size]
        elif len(data) < self.payload_size:
            data = data + b'\x00' * (self.payload_size - len(data))

        self.set_tx_mode()
        self._write_reg(STATUS, 0x70)

        self.csn.value(0)
        self.spi.write(bytes([0xA0]))
        self.spi.write(data)
        self.csn.value(1)

        self.ce.value(1)
        time.sleep_us(15)
        self.ce.value(0)

        timeout = 100
        while timeout > 0:
            status = self._read_reg(STATUS)
            if status & 0x20:      # TX_DS
                self._write_reg(STATUS, 0x70)
                self.set_rx_mode()
                return True
            if status & 0x10:      # MAX_RT
                self._write_reg(STATUS, 0x70)
                self.set_rx_mode()
                return False
            time.sleep_ms(1)
            timeout -= 1
        self.set_rx_mode()
        return False

    def receive(self):
        fifo_status = self._read_reg(FIFO_STATUS)
        if fifo_status & 0x01:    # RX_EMPTY
            return False, None
        self.csn.value(0)
        self.spi.write(bytes([0x61]))
        data = self.spi.read(self.payload_size)
        self.csn.value(1)
        self._write_reg(STATUS, 0x40)
        return True, data

# ==================== 主程序 ====================
def main():
    # 初始化软件 SPI
    spi = SoftSPI(PIN_SCK, PIN_MOSI, PIN_MISO)
    nrf = NRF24L01(spi, PIN_CSN, PIN_CE, channel=100, payload_size=32)

    key = Pin(PIN_KEY, Pin.IN, Pin.PULL_DOWN)
    led = Pin(LED_GREEN, Pin.OUT, value=1)

    nrf.set_rx_mode()
    print("nRF24L01 初始化完成，当前为接收模式")
    print("按下按键（GPIO53）发送 'hello'，LED闪烁表示发送成功，常亮表示失败")

    last_key = 0
    while True:
        # ---- 按键检测 ----
        cur_key = key.value()
        if cur_key == 1 and last_key == 0:
            print("按键按下，发送 'hello' ...")
            ok = nrf.send(b'hello')
            if ok:
                print("✅ 发送成功")
                for _ in range(2):
                    led.value(0)
                    time.sleep_ms(100)
                    led.value(1)
                    time.sleep_ms(100)
            else:
                print("❌ 发送失败（没有收到对方应答，请确保接收端已通电并处于接收模式）")
                led.value(0)
                time.sleep_ms(500)
                led.value(1)
        last_key = cur_key

        # ---- 接收数据（仅当有数据时打印） ----
        success, raw = nrf.receive()
        if success and raw is not None:
            # 去除末尾的空字节（因为 payload 固定长度，会补 0x00）
            trimmed = raw.rstrip(b'\x00')
            if trimmed:
                try:
                    msg = trimmed.decode('utf-8')
                    print("📩 收到:", msg)
                except UnicodeDecodeError:
                    # 非文本数据，以十六进制显示
                    print("📩 收到(hex):", raw.hex())
            # 如果 trimmed 为空，说明全是 0x00，可能是无效数据，忽略不打印

        time.sleep_ms(10)

if __name__ == "__main__":
    main()
