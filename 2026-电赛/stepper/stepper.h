#ifndef __STEPPER_H
#define __STEPPER_H

#include "sys.h"
#include <stdint.h>
#include <stdbool.h>

// 初始化步进电机 GPIO（需在 SysConfig 中配置为输出）
void Stepper_Init(void);

// 设置转速（步/秒），建议 100~2000，默认 500
void Stepper_SetSpeed(uint16_t steps_per_sec);

// 正转指定步数（阻塞执行，完成后自动停止）
void Stepper_StepForward(uint32_t steps);

// 反转指定步数（阻塞执行，完成后自动停止）
void Stepper_StepBackward(uint32_t steps);

// 立即停止（释放所有线圈）
void Stepper_Stop(void);

#endif