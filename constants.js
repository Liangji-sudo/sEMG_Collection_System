// constants.js - 定义全局字符串数组常量
// 使用 Object.freeze 冻结数组，防止被修改（浅冻结，数组元素若为对象需额外处理）
export const discrete_gesture_prompt_name = Object.freeze([
  'thumb_up',
  'thumb_down',
  'thumb_left',
  'thumb_right',
  'other'
]);

// 也可定义多个常量数组
export const collection_task_name = Object.freeze([
  'discrete_gesture',
  'continual_gesture_1',
  'continual_gesture_2'
]);