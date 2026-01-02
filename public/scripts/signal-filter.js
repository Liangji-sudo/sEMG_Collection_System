/**
 * signal-filter.js - 前端实时信号滤波模块
 * 
 * 功能：
 * 1. 带通滤波 (20-450Hz) - 去除直流分量和高频噪声
 * 2. 工频陷波 (50Hz) - 去除电源干扰
 * 
 * 原理：
 * - 使用 IIR (无限脉冲响应) 滤波器
 * - 采用二阶节 (Biquad) 级联结构
 * - 保持滤波器状态实现实时流式处理
 * 
 * 参考：PyQt上位机的 signalfilter.py
 */

(function() {
    'use strict';

    /**
     * 二阶 IIR 滤波器 (Biquad)
     * 传递函数: H(z) = (b0 + b1*z^-1 + b2*z^-2) / (1 + a1*z^-1 + a2*z^-2)
     */
    class BiquadFilter {
        constructor(b0, b1, b2, a1, a2) {
            this.b0 = b0;
            this.b1 = b1;
            this.b2 = b2;
            this.a1 = a1;
            this.a2 = a2;
            
            // 滤波器状态 (Direct Form II Transposed)
            this.z1 = 0;
            this.z2 = 0;
        }

        /**
         * 处理单个采样点
         */
        process(input) {
            const output = this.b0 * input + this.z1;
            this.z1 = this.b1 * input - this.a1 * output + this.z2;
            this.z2 = this.b2 * input - this.a2 * output;
            return output;
        }

        /**
         * 重置滤波器状态
         */
        reset() {
            this.z1 = 0;
            this.z2 = 0;
        }
    }

    /**
     * 多通道实时滤波器
     */
    class SignalFilter {
        /**
         * @param {Object} options - 配置选项
         * @param {number} options.sampleRate - 采样率 (Hz)，默认 1000
         * @param {number} options.numChannels - 通道数，默认 16
         * @param {number} options.lowcut - 带通下限 (Hz)，默认 20
         * @param {number} options.highcut - 带通上限 (Hz)，默认 450
         * @param {boolean} options.enableBandpass - 启用带通滤波，默认 true
         * @param {boolean} options.enableNotch - 启用工频陷波，默认 true
         * @param {number} options.notchFreq - 工频频率 (Hz)，默认 50
         * @param {number} options.notchQ - 陷波器 Q 值，默认 15
         */
        constructor(options = {}) {
            this.sampleRate = options.sampleRate || 1000;
            this.numChannels = options.numChannels || 16;
            this.lowcut = options.lowcut || 20;
            this.highcut = options.highcut || 450;
            this.enableBandpass = options.enableBandpass !== false;
            this.enableNotch = options.enableNotch !== false;
            this.notchFreq = options.notchFreq || 50;
            this.notchQ = options.notchQ || 15;

            // 每个通道的滤波器组
            this.channelFilters = [];
            
            // 数据不连续标记
            this.isDiscontinuous = true;
            
            // 初始化滤波器
            this.initFilters();
        }

        /**
         * 初始化所有滤波器
         */
        initFilters() {
            this.channelFilters = [];
            
            for (let ch = 0; ch < this.numChannels; ch++) {
                const filters = {
                    // 带通滤波器 (4阶 = 2个二阶节)
                    bandpass: this.enableBandpass ? this.createBandpassFilters() : null,
                    // 工频陷波器 (可以级联多个，处理 50Hz, 100Hz, 150Hz...)
                    notch: this.enableNotch ? this.createNotchFilters() : null
                };
                this.channelFilters.push(filters);
            }
        }

        /**
         * 创建带通滤波器 (Butterworth 4阶)
         * 使用预计算的系数，适用于 fs=1000Hz, 20-450Hz
         */
        createBandpassFilters() {
            const fs = this.sampleRate;
            const lowcut = this.lowcut;
            const highcut = Math.min(this.highcut, fs / 2 - 1);

            // 归一化频率
            const wl = lowcut / (fs / 2);
            const wh = highcut / (fs / 2);

            // 使用 Butterworth 带通滤波器设计
            // 这里使用预计算的 2 阶带通滤波器系数
            // 对于 fs=1000, lowcut=20, highcut=450
            const filters = [];

            // 简化设计：使用一个高通 + 一个低通级联
            // 高通滤波器 (去除直流，截止频率 lowcut)
            const hp = this.designHighpass(lowcut, fs);
            filters.push(new BiquadFilter(hp.b0, hp.b1, hp.b2, hp.a1, hp.a2));

            // 低通滤波器 (去除高频噪声，截止频率 highcut)
            const lp = this.designLowpass(highcut, fs);
            filters.push(new BiquadFilter(lp.b0, lp.b1, lp.b2, lp.a1, lp.a2));

            return filters;
        }

        /**
         * 设计二阶 Butterworth 高通滤波器
         */
        designHighpass(cutoff, fs) {
            const w0 = 2 * Math.PI * cutoff / fs;
            const cosw0 = Math.cos(w0);
            const sinw0 = Math.sin(w0);
            const alpha = sinw0 / (2 * Math.sqrt(2)); // Q = sqrt(2)/2 for Butterworth

            const b0 = (1 + cosw0) / 2;
            const b1 = -(1 + cosw0);
            const b2 = (1 + cosw0) / 2;
            const a0 = 1 + alpha;
            const a1 = -2 * cosw0;
            const a2 = 1 - alpha;

            return {
                b0: b0 / a0,
                b1: b1 / a0,
                b2: b2 / a0,
                a1: a1 / a0,
                a2: a2 / a0
            };
        }

        /**
         * 设计二阶 Butterworth 低通滤波器
         */
        designLowpass(cutoff, fs) {
            const w0 = 2 * Math.PI * cutoff / fs;
            const cosw0 = Math.cos(w0);
            const sinw0 = Math.sin(w0);
            const alpha = sinw0 / (2 * Math.sqrt(2));

            const b0 = (1 - cosw0) / 2;
            const b1 = 1 - cosw0;
            const b2 = (1 - cosw0) / 2;
            const a0 = 1 + alpha;
            const a1 = -2 * cosw0;
            const a2 = 1 - alpha;

            return {
                b0: b0 / a0,
                b1: b1 / a0,
                b2: b2 / a0,
                a1: a1 / a0,
                a2: a2 / a0
            };
        }

        /**
         * 创建工频陷波滤波器
         * 陷波 50Hz 及其谐波 (100Hz, 150Hz, 200Hz...)
         */
        createNotchFilters() {
            const fs = this.sampleRate;
            const filters = [];
            
            // 陷波 50Hz 及其谐波，直到奈奎斯特频率
            for (let freq = this.notchFreq; freq < fs / 2; freq += this.notchFreq) {
                const notch = this.designNotch(freq, fs, this.notchQ);
                filters.push(new BiquadFilter(notch.b0, notch.b1, notch.b2, notch.a1, notch.a2));
            }
            
            return filters;
        }

        /**
         * 设计二阶陷波滤波器 (Notch Filter)
         */
        designNotch(freq, fs, Q) {
            const w0 = 2 * Math.PI * freq / fs;
            const cosw0 = Math.cos(w0);
            const sinw0 = Math.sin(w0);
            const alpha = sinw0 / (2 * Q);

            const b0 = 1;
            const b1 = -2 * cosw0;
            const b2 = 1;
            const a0 = 1 + alpha;
            const a1 = -2 * cosw0;
            const a2 = 1 - alpha;

            return {
                b0: b0 / a0,
                b1: b1 / a0,
                b2: b2 / a0,
                a1: a1 / a0,
                a2: a2 / a0
            };
        }

        /**
         * 重置所有滤波器状态
         */
        reset() {
            for (const chFilters of this.channelFilters) {
                if (chFilters.bandpass) {
                    chFilters.bandpass.forEach(f => f.reset());
                }
                if (chFilters.notch) {
                    chFilters.notch.forEach(f => f.reset());
                }
            }
            this.isDiscontinuous = true;
        }

        /**
         * 标记数据不连续（下次滤波时会重置状态）
         */
        markDiscontinuous() {
            this.isDiscontinuous = true;
        }

        /**
         * 处理单个采样点（单通道）
         * @param {number} channelIndex - 通道索引
         * @param {number} value - 输入值
         * @returns {number} 滤波后的值
         */
        processSample(channelIndex, value) {
            if (channelIndex >= this.numChannels) return value;
            
            const filters = this.channelFilters[channelIndex];
            let output = value;

            // 带通滤波
            if (filters.bandpass) {
                for (const filter of filters.bandpass) {
                    output = filter.process(output);
                }
            }

            // 工频陷波
            if (filters.notch) {
                for (const filter of filters.notch) {
                    output = filter.process(output);
                }
            }

            return output;
        }

        /**
         * 处理多通道数据块
         * @param {Array<Array<number>>} data - 输入数据 [[ch0_p1, ch0_p2, ...], [ch1_p1, ...], ...]
         * @returns {Array<Array<number>>} 滤波后的数据，格式相同
         */
        processBlock(data) {
            if (!data || data.length === 0) return data;

            // 如果数据不连续，重置滤波器状态
            if (this.isDiscontinuous) {
                this.reset();
                this.isDiscontinuous = false;
                
                // 对新数据应用淡入窗口减少冲击
                // （简化处理：跳过前几个点的淡入）
            }

            const numChannels = Math.min(data.length, this.numChannels);
            const result = [];

            for (let ch = 0; ch < numChannels; ch++) {
                const channelData = data[ch];
                if (!channelData) {
                    result.push([]);
                    continue;
                }

                const filteredChannel = [];
                for (let i = 0; i < channelData.length; i++) {
                    const filtered = this.processSample(ch, channelData[i]);
                    filteredChannel.push(filtered);
                }
                result.push(filteredChannel);
            }

            // 保留超出 numChannels 的数据（不滤波）
            for (let ch = numChannels; ch < data.length; ch++) {
                result.push(data[ch] ? [...data[ch]] : []);
            }

            return result;
        }

        /**
         * 更新采样率（需要重新初始化滤波器）
         */
        setSampleRate(fs) {
            if (fs !== this.sampleRate) {
                this.sampleRate = fs;
                this.initFilters();
            }
        }

        /**
         * 启用/禁用带通滤波
         */
        setEnableBandpass(enable) {
            if (enable !== this.enableBandpass) {
                this.enableBandpass = enable;
                this.initFilters();
            }
        }

        /**
         * 启用/禁用工频陷波
         */
        setEnableNotch(enable) {
            if (enable !== this.enableNotch) {
                this.enableNotch = enable;
                this.initFilters();
            }
        }

        /**
         * 设置带通滤波范围
         */
        setBandpassRange(lowcut, highcut) {
            this.lowcut = lowcut;
            this.highcut = highcut;
            this.initFilters();
        }
    }

    /**
     * 滤波器管理器 - 管理多个滤波器实例（EMG1, EMG2, IMU等）
     */
    class FilterManager {
        constructor() {
            this.filters = {};
        }

        /**
         * 创建 EMG 滤波器
         */
        createEMGFilter(name, options = {}) {
            const defaultOptions = {
                sampleRate: 1000,
                numChannels: 16,
                lowcut: 20,
                highcut: 450,
                enableBandpass: true,
                enableNotch: true,
                notchFreq: 50,
                notchQ: 15
            };
            this.filters[name] = new SignalFilter({ ...defaultOptions, ...options });
            return this.filters[name];
        }

        /**
         * 创建 IMU 滤波器（通常不需要滤波，但保留接口）
         */
        createIMUFilter(name, options = {}) {
            const defaultOptions = {
                sampleRate: 100,
                numChannels: 3,
                enableBandpass: false,  // IMU 通常不需要带通滤波
                enableNotch: false
            };
            this.filters[name] = new SignalFilter({ ...defaultOptions, ...options });
            return this.filters[name];
        }

        /**
         * 获取滤波器
         */
        get(name) {
            return this.filters[name];
        }

        /**
         * 处理数据
         */
        process(name, data) {
            const filter = this.filters[name];
            if (filter) {
                return filter.processBlock(data);
            }
            return data;
        }

        /**
         * 重置指定滤波器
         */
        reset(name) {
            const filter = this.filters[name];
            if (filter) {
                filter.reset();
            }
        }

        /**
         * 重置所有滤波器
         */
        resetAll() {
            Object.values(this.filters).forEach(f => f.reset());
        }
    }

    // ==================== 导出到全局 ====================
    window.SignalFilter = SignalFilter;
    window.FilterManager = FilterManager;
    window.BiquadFilter = BiquadFilter;

    console.log('[SignalFilter] 信号滤波模块已加载');

})();
