from libs.PipeLine import PipeLine, ScopedTiming
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
import os, sys, gc, time, random, utime
import ujson
from media.media import *
from time import *
import nncase_runtime as nn
import ulab.numpy as np
import image
import aidemo

# ---------- 新增导入 ----------
from machine import FPIOA, Pin, UART
# -----------------------------

# 自定义YOLO检测类，继承自AIBase基类
class YOLOv12App(AIBase):
    def __init__(self, kmodel_path, model_input_size, anchors, confidence_threshold=0.5, nms_threshold=0.2, rgb888p_size=[224,224], display_size=[1920,1080], debug_mode=0):
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.class_id = ["cat", "chicken", "cow", "dog", "fox", "goat", "horse", "person", "racoon", "skunk"]
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.anchors = anchors
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            top, bottom, left, right = self.get_padding_param()
            print("padding: {} {} {} {}".format(top, bottom, left, right))
            self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [104, 117, 123])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1,3,ai2d_input_size[1],ai2d_input_size[0]],[1,3,self.model_input_size[1],self.model_input_size[0]])

    def postprocess(self, results):
        counter = 0
        det_res = []
        with ScopedTiming("postprocess", self.debug_mode > 0):
            for i in range(2100):
                result = results[0][0][:, i]
                max_score = max(result[4:])
                if max_score > self.confidence_threshold:
                    x = result[0] * max(self.rgb888p_size) / max(self.model_input_size)
                    y = result[1] * max(self.rgb888p_size) / max(self.model_input_size)
                    w = result[2] * max(self.rgb888p_size) / max(self.model_input_size)
                    h = result[3] * max(self.rgb888p_size) / max(self.model_input_size)
                    det_res.append([x, y, w, h, list(result[4:]).index(max_score), max_score])
            det_res.sort(key=lambda x:x[-1], reverse=True)
            det_res_single = []
            added_class = []
            for result in det_res:
                if not result[-2] in added_class:
                    added_class.append(result[-2])
                    det_res_single.append(result)
        return det_res_single

    def draw_result(self, pl, dets):
        with ScopedTiming("display_draw", self.debug_mode > 0):
            if dets:
                pl.osd_img.clear()
                for det in dets:
                    x, y, w, h = map(lambda x: int(round(x, 0)), det[:4])
                    x = x * self.display_size[0] // self.rgb888p_size[0]
                    y = y * self.display_size[1] // self.rgb888p_size[1]
                    w = w * self.display_size[0] // self.rgb888p_size[0]
                    h = h * self.display_size[1] // self.rgb888p_size[1]
                    pl.osd_img.draw_rectangle(x - w//2, y - h // 2, w, h, color=(255, 0, 255, 0), thickness=2)
                    pl.osd_img.draw_string_advanced(x - w//2, y - h // 2, 80, "{} {}".format(self.class_id[det[-2]], round(det[-1], 2)), color=(255, 0, 255, 0))
            else:
                pl.osd_img.clear()

    def get_padding_param(self):
        dst_w = self.model_input_size[0]
        dst_h = self.model_input_size[1]
        ratio_w = dst_w / self.rgb888p_size[0]
        ratio_h = dst_h / self.rgb888p_size[1]
        ratio = min(ratio_w, ratio_h)
        new_w = int(ratio * self.rgb888p_size[0])
        new_h = int(ratio * self.rgb888p_size[1])
        dw = (dst_w - new_w) / 2
        dh = (dst_h - new_h) / 2
        top = int(round(0))
        bottom = int(round(dh * 2 + 0.1))
        left = int(round(0))
        right = int(round(dw * 2 - 0.1))
        return top, bottom, left, right

if __name__ == "__main__":
    display_mode = "lcd"
    rgb888p_size = [1920, 1080]
    if display_mode == "hdmi":
        display_size = [1920, 1080]
    else:
        display_size = [800, 480]

    kmodel_path = "/sdcard/best.kmodel"
    confidence_threshold = 0.7
    nms_threshold = 0.2
    anchors = None

    pl = PipeLine(rgb888p_size=rgb888p_size, display_size=display_size, display_mode=display_mode)
    pl.create()

#    fpioa = FPIOA()
#    fpioa.set_function(53, FPIOA.GPIO53)
#    fpioa.set_function(11, FPIOA.UART2_TXD)
#    fpioa.set_function(12, FPIOA.UART2_RXD)
#    key = Pin(53, Pin.IN, Pin.PULL_DOWN)
#    uart2 = UART(UART.UART2, 115200)              # 串口2，波特率115200


    yolo_det = YOLOv12App(kmodel_path, model_input_size=[320, 320], anchors=anchors,
                          confidence_threshold=confidence_threshold, nms_threshold=nms_threshold,
                          rgb888p_size=rgb888p_size, display_size=display_size, debug_mode=0)
    yolo_det.config_preprocess()

    try:
        while True:
            os.exitpoint()
            with ScopedTiming("total", 1):
                img = pl.get_frame()
                res = yolo_det.run(img)
                yolo_det.draw_result(pl, res)
                pl.show_image()
                gc.collect()

#            if key.value() == 1:                  # 按键被按下
#                sleep_ms(100)               # 简单去抖
#                uart2.write("B")                 # 发送字符 'A'
#                print("目标状态:")                # 保留原串口例程的打印


    except Exception as e:
        print(e)
    finally:
        yolo_det.deinit()
        pl.destroy()
        # ---------- 新增：释放串口资源 ----------
        uart2.deinit()
        # ---------------------------------------
