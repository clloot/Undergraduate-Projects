import time
import os
import sys
from media.sensor import *
from media.display import *
from media.media import *
from machine import FPIOA, Pin, UART

sensor = None
try:
    # 串口和按键
    fpioa = FPIOA()
    fpioa.set_function(53, FPIOA.GPIO53)
    fpioa.set_function(11, FPIOA.UART2_TXD)
    fpioa.set_function(12, FPIOA.UART2_RXD)
    key = Pin(53, Pin.IN, Pin.PULL_DOWN)
    uart2 = UART(UART.UART2, 115200)

    # 传感器保持 800x480
    sensor = Sensor(width=800, height=480)
    sensor.reset()
    sensor.set_framesize(width=800, height=480)
    sensor.set_pixformat(Sensor.RGB565)

    Display.init(Display.ST7701, width=800, height=480, to_ide=True)
    MediaManager.init()
    sensor.run()
    clock = time.clock()

    # ROI 区域（中央 400x240）
    roi_x, roi_y, roi_w, roi_h = 200, 120, 400, 240

    def line_intersection(line1, line2):
        x1, y1 = line1[0]
        x2, y2 = line1[1]
        x3, y3 = line2[0]
        x4, y4 = line2[1]
        denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
        if denom == 0:
            return None
        ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / denom
        ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / denom
        if 0 <= ua <= 1 and 0 <= ub <= 1:
            x = x1 + ua * (x2 - x1)
            y = y1 + ua * (y2 - y1)
            return (x, y)
        return None

    frame_cnt = 0
    while True:
        clock.tick()
        os.exitpoint()
        img = sensor.snapshot(chn=CAM_CHN_ID_0)

        # 裁剪 ROI（传入元组）
        roi_img = img.crop((roi_x, roi_y, roi_w, roi_h))
        gray = roi_img.to_grayscale(copy=False)
        binary = gray.binary([(118, 255)])
        rects = binary.find_rects(threshold=150000)

        if rects:
            for rect in rects:
                corners_roi = rect.corners()
                # 映射到原图坐标
                corners_orig = [(x + roi_x, y + roi_y) for (x, y) in corners_roi]

                # 绘制边框
                for i in range(4):
                    x1, y1 = corners_orig[i]
                    x2, y2 = corners_orig[(i+1)%4]
                    img.draw_line(x1, y1, x2, y2, color=(0, 255, 0), thickness=2)

                # 计算中心
                diag1 = (corners_orig[0], corners_orig[2])
                diag2 = (corners_orig[1], corners_orig[3])
                center = line_intersection(diag1, diag2)
                if center is not None:
                    cx, cy = center
                    frame_cnt += 1
                    if frame_cnt % 5 == 0:
                        # 画面中心 (400, 240)
                        if cx < 380:
                            uart2.write(bytes([1]))
                        elif cx > 420:
                            uart2.write(bytes([2]))
                        else:
                            uart2.write(bytes([0]))
                        if cy < 220:
                            uart2.write(bytes([3]))
                        elif cy > 260:
                            uart2.write(bytes([4]))
                        else:
                            uart2.write(bytes([0]))
                        if abs(cx-400) <= 20 and abs(cy-240) <= 20:
                            uart2.write(bytes([5]))
                        time.sleep_ms(20)

        img.compressed_for_ide()
        Display.show_image(img, x=0, y=0)

except KeyboardInterrupt as e:
    print("用户停止: ", e)
except BaseException as e:
    print(f"异常: {e}")
finally:
    if sensor: sensor.stop()
    Display.deinit()
    MediaManager.deinit()
    uart2.deinit()
