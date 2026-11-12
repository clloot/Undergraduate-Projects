import time
import os
import sys

from media.sensor import *
from media.display import *
from media.media import *
from time import ticks_ms
from machine import FPIOA
from machine import Pin
from machine import PWM
from machine import Timer
from machine import UART
import time


sensor = None

try:

    print("camera_test")
    fpioa = FPIOA()
    fpioa.set_function(53, FPIOA.GPIO53)
    fpioa.set_function(11, FPIOA.UART2_TXD)
    fpioa.set_function(12, FPIOA.UART2_RXD)

    key = Pin(53, Pin.IN, Pin.PULL_DOWN)
    uart2 = UART(UART.UART2, )

    sensor = Sensor(width=800, height=480)#图像获取大小
    sensor.reset()


    sensor.set_framesize(width=800, height=480)#图像呈现大小
    sensor.set_pixformat(Sensor.RGB565)

    Display.init(Display.ST7701, width=800, height=480, to_ide=True)#这里本来是初始化到屏幕上的
    # 初始化媒体管理器
    MediaManager.init()
    # 启动 sensor
    sensor.run()
    clock = time.clock()

    def line_intersection(line1, line2):
        """计算两条线段的交点"""
        x1, y1 = line1[0]
        x2, y2 = line1[1]
        x3, y3 = line2[0]
        x4, y4 = line2[1]

        # 计算分母
        denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)

        # 如果分母为0，说明两线段平行，没有交点
        if denom == 0:
            return 0

        # 计算交点
        ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / denom
        ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / denom

        # 检查交点是否在线段上
        if 0 <= ua <= 1 and 0 <= ub <= 1:
            x = x1 + ua * (x2 - x1)
            y = y1 + ua * (y2 - y1)
            return (x, y)

#    cut_roi = ( 600, 360, 360, 360) #裁切

    while True:
        clock.tick()
        os.exitpoint()
        img = sensor.snapshot(chn=CAM_CHN_ID_0)

        img_rect = img.to_grayscale(copy=True)
        img_rect = img_rect.binary([(118, 255)])
        rects = img_rect.find_rects(threshold=100000)

        if not rects == None:
            for rect in rects:
                corner = rect.corners()

                diag1 = (corner[0], corner[2])  # 第一条对角线
                diag2 = (corner[1], corner[3])  # 第二条对角线
                # 计算交点
                intersection = line_intersection(diag1, diag2)
                print(f"对角线交点坐标: ({intersection[0]:.1f}, {intersection[1]:.1f})")
                img.draw_line(corner[0][0], corner[0][1], corner[1][0], corner[1][1], color=(0, 255, 0), thickness=5)
                img.draw_line(corner[2][0], corner[2][1], corner[1][0], corner[1][1], color=(0, 255, 0), thickness=5)
                img.draw_line(corner[2][0], corner[2][1], corner[3][0], corner[3][1], color=(0, 255, 0), thickness=5)
                img.draw_line(corner[0][0], corner[0][1], corner[3][0], corner[3][1], color=(0, 255, 0), thickness=5)
                if intersection[0] is not 0:
                    if x_pt > 400:   #左
                        uart2.write(bytes([5]))
                        time.sleep_ms(100)
                        uart2.write(bytes([0]))


                    elif x_pt < 400:   #右
                        uart2.write(bytes([1]))
                        time.sleep_ms(100)
                        uart2.write(bytes([0]))

                             #上下
                    if y_pt > 240:    #上
                        uart2.write(bytes([2]))
                        time.sleep_ms(100)
                        uart2.write(bytes([0]))

                    elif y_pt < 240:   #下
                        uart2.write(bytes([3]))
                        time.sleep_ms(100)
                        uart2.write(bytes([0]))

                    if x_pt == 400 and y_pt == 240:  #中
                        uart2.write(bytes([4]))
                        time.sleep_ms(100)
                        uart2.write(bytes([0]))
                    else:
                        uart2.write(bytes([0]))


        img.draw_string_advanced(50, 50, 80, "fps: {}".format(clock.fps()), color=(255, 0, 0))
#        img.midpoint_pool(2, 2)
        img.compressed_for_ide()
        Display.show_image(img, x=(800-320)//2, y=(480-320)//2)

except KeyboardInterrupt as e:
    print("用户停止: ", e)
except BaseException as e:
    print(f"异常: {e}")
finally:
    if isinstance(sensor, Sensor):
        sensor.stop()
    Display.deinit()
    os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
#    time.sleep_ms(100)
    MediaManager.deinit()
    uart2.deinit()
