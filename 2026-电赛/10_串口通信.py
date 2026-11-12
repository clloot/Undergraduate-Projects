# 搜索学不会电磁场看教程
# 第十课，有时候，光靠一个k230是无法完成电赛题目的，比如说我们上次控制步进电机就是一卡一卡的
# 很多时候，k230需要配合其他开发板或者模块使用，或者需要把数据发给电脑
# 现在，我们一起学习一下最简单的通信方式，UART串口通信

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
    fpioa.help()
    fpioa.set_function(53, FPIOA.GPIO53)
    fpioa.set_function(11, FPIOA.UART2_TXD)
    fpioa.set_function(12, FPIOA.UART2_RXD)

    key = Pin(53, Pin.IN, Pin.PULL_DOWN)
    uart2 = UART(UART.UART2, 115200)


    sensor = Sensor(width=640, height=640)
    sensor.reset()

    # 鼠标悬停在函数上可以查看允许接收的参数
    sensor.set_framesize(width=640, height=640)
    sensor.set_pixformat(Sensor.RGB565)

    Display.init(Display.ST7701,to_ide=True)
    # 初始化媒体管理器
    MediaManager.init()
    # 启动 sensor
    sensor.run()
    clock = time.clock()

    while True:
        clock.tick()
        os.exitpoint()
        img = sensor.snapshot(chn=CAM_CHN_ID_0)
        if key.value() == 1:
            time.sleep_ms(500)
            # 发送一个字符串
            uart2.write("hello k230\n")

            # 发送一系列数字，直接发数，而不是转成字符串形式
            send_lst = [0x00, 0x03, 0x77, 0x88]
            uart2.write(bytes(send_lst))
        info = uart2.read()
        if not info == None:
            print("recieved 字节流: {}".format(info))
            print("recieved ascii码流: {}".format(info.decode()))
            print("recieved ascii码值: {}".format(list(info)))


        # 矩形识别，可以用来找矩形的四个角的坐标
#        img_rect = img.to_grayscale(copy=True)
#        img_rect = img_rect.binary([(82, 212)])
#        rects = img_rect.find_rects(threshold=10000)

#        if not rects == None:
#            for rect in rects:
#                corner = rect.corners()
#                img.draw_line(corner[0][0], corner[0][1], corner[1][0], corner[1][1], color=(0, 255, 0), thickness=5)
#                img.draw_line(corner[2][0], corner[2][1], corner[1][0], corner[1][1], color=(0, 255, 0), thickness=5)
#                img.draw_line(corner[2][0], corner[2][1], corner[3][0], corner[3][1], color=(0, 255, 0), thickness=5)
#                img.draw_line(corner[0][0], corner[0][1], corner[3][0], corner[3][1], color=(0, 255, 0), thickness=5)


        img.draw_string_advanced(50, 50, 80, "fps: {}".format(clock.fps()), color=(255, 0, 0))
        img.midpoint_pool(2, 2)
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
    time.sleep_ms(100)
    MediaManager.deinit()
    uart2.deinit()
