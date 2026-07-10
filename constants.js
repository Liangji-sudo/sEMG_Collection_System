// constants.js - 定义全局字符串数组常量

// 也可定义多个常量数组
export const collection_task_name = Object.freeze({
  discrete_gesture: '\u79bb\u6563\u624b\u52bf',
  continual_gesture_1: '\u8fde\u7eed\u624b\u52bf1',
  continual_gesture_2: '\u8fde\u7eed\u624b\u52bf2',
  continual_gesture_3: '\u8fde\u7eed\u624b\u52bf3'
});


// 使用 Object.freeze 冻结数组，防止被修改（浅冻结，数组元素若为对象需额外处理）
export const discrete_gesture_prompt_name = Object.freeze([
  'thumb_up',
  'thumb_down',
  'thumb_left',
  'thumb_right',
  'null'
]);

export const discrete_gesture_stage_name = Object.freeze([
  'pinch_release_practice',
  'pinch_release_static_palm_up_with_taps',
  'pinch_release_static_hand_in_lap_with_taps',
  'pinch_release_static_palm_out_with_taps',
  'pinch_release_static_vertical_arm_with_taps',
  'pinch_release_dynamic_vertical_arm_translation',
  'thumb_swipes_practice',
  'thumb_swipes_static_arm_in_lap',
  'thumb_swipes_static_arm_front',
  'thumb_swipes_static_vertical',
  'pinch_release_thumb_swipes_practice',
  'pinch_release_thumb_swipes_static_vertical_arm',
  'pinch_release_thumb_swipes_static_arm_in_front',
  'null',
  'null',
  'null'
]);
