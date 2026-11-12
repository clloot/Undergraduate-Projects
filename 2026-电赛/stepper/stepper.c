#include "stepper.h"
#include "ti_msp_dl_config.h"
#include <ti/driverlib/driverlib.h>

// 四相八拍励磁序列（正转顺序：A, AB, B, BC, C, CD, D, DA）
// 位掩码：bit0=IN1, bit1=IN2, bit2=IN3, bit3=IN4
static const uint8_t step_seq[8] = {
    0x01, // IN1
    0x03, // IN1+IN2
    0x02, // IN2
    0x06, // IN2+IN3
    0x04, // IN3
    0x0C, // IN3+IN4
    0x08, // IN4
    0x09  // IN4+IN1
};

static uint16_t step_delay_us = 2000; // 默认 500 步/秒

// 写入四个引脚电平（先清除全部，再设置需要的位）
static void write_step(uint8_t mask) {
    // 先清除所有引脚
    DL_GPIO_clearPins(STEPPER_IN1_PORT, 
                      STEPPER_IN1_IN1_PIN | STEPPER_IN1_IN2_PIN | 
                      STEPPER_IN1_IN3_PIN | STEPPER_IN1_IN4_PIN);
    // 根据掩码设置引脚
    if (mask & 0x01) DL_GPIO_setPins(STEPPER_IN1_PORT, STEPPER_IN1_IN1_PIN);
    if (mask & 0x02) DL_GPIO_setPins(STEPPER_IN1_PORT, STEPPER_IN1_IN2_PIN);
    if (mask & 0x04) DL_GPIO_setPins(STEPPER_IN1_PORT, STEPPER_IN1_IN3_PIN);
    if (mask & 0x08) DL_GPIO_setPins(STEPPER_IN1_PORT, STEPPER_IN1_IN4_PIN);
}

void Stepper_Init(void) {
    // GPIO 已在 SysConfig 配置为输出，此处只需停止电机
    Stepper_Stop();
}

void Stepper_SetSpeed(uint16_t steps_per_sec) {
    if (steps_per_sec < 20) steps_per_sec = 20;
    if (steps_per_sec > 3000) steps_per_sec = 3000;
    step_delay_us = 1000000 / steps_per_sec;
}

// 单步执行（方向：true=正转，false=反转）
static void step_once(bool forward) {
    static uint8_t index = 0; // 当前相位索引
    if (forward) {
        index = (index + 1) & 0x07;
    } else {
        index = (index + 7) & 0x07; // 相当于 index-1
    }
    write_step(step_seq[index]);
}

void Stepper_StepForward(uint32_t steps) {
    for (uint32_t i = 0; i < steps; i++) {
        step_once(true);
        delay_us(step_delay_us);
    }
    Stepper_Stop(); // 完成后退磁
}

void Stepper_StepBackward(uint32_t steps) {
    for (uint32_t i = 0; i < steps; i++) {
        step_once(false);
        delay_us(step_delay_us);
    }
    Stepper_Stop();
}

void Stepper_Stop(void) {
    write_step(0x00); // 所有引脚拉低，释放线圈
}