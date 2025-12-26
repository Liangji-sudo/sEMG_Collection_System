/**
 * data-generator.js - 模拟数据生成模块
 * 
 * 生成更加随机、真实的模拟数据：
 * - EMG: 100Hz生成，每次18组数据（模拟2kHz采样，加倍）
 * - IMU: 100Hz生成，每次1组数据
 */

(function() {
    'use strict';

    // ==================== 配置参数 ====================
    const GENERATOR_CONFIG = {
        EMG_CHANNELS: 16,
        EMG_POINTS_PER_PACKET: 18,  // 每个数据包的点数（从9加倍到18）
        IMU_AXES: 3,
        IMU_POINTS_PER_PACKET: 1,
        
        // EMG参数
        EMG_BASE_AMPLITUDE: 80,
        EMG_NOISE_LEVEL: 30,
        EMG_BURST_PROBABILITY: 0.02,
        EMG_BURST_AMPLITUDE: 150,
        
        // IMU参数
        ACC_RANGE: 2,
        GYR_RANGE: 250,
        MAG_RANGE: 50,
        IMU_DRIFT_RATE: 0.01,
        IMU_PULSE_PROBABILITY: 0.005
    };

    // ==================== 工具函数 ====================
    
    function gaussianRandom(mean = 0, std = 1) {
        const u1 = Math.random();
        const u2 = Math.random();
        const z0 = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
        return z0 * std + mean;
    }

    class PinkNoiseGenerator {
        constructor(numRows = 16) {
            this.numRows = numRows;
            this.maxKey = (1 << numRows) - 1;
            this.key = 0;
            this.rows = new Array(numRows).fill(0);
            this.runningSum = 0;
            
            for (let i = 0; i < numRows; i++) {
                this.rows[i] = Math.random() * 2 - 1;
                this.runningSum += this.rows[i];
            }
        }

        next() {
            this.key++;
            if (this.key > this.maxKey) this.key = 0;
            
            let lastKey = this.key - 1;
            let diff = this.key ^ lastKey;
            
            for (let i = 0; i < this.numRows; i++) {
                if (diff & (1 << i)) {
                    this.runningSum -= this.rows[i];
                    this.rows[i] = Math.random() * 2 - 1;
                    this.runningSum += this.rows[i];
                    break;
                }
            }
            
            return this.runningSum / this.numRows;
        }
    }

    // ==================== EMG数据生成器 ====================
    class EMGDataGenerator {
        constructor() {
            this.channels = GENERATOR_CONFIG.EMG_CHANNELS;
            this.pointsPerPacket = GENERATOR_CONFIG.EMG_POINTS_PER_PACKET;
            
            this.channelStates = [];
            for (let i = 0; i < this.channels; i++) {
                this.channelStates.push({
                    pinkNoise: new PinkNoiseGenerator(12),
                    envelope: 0.5 + Math.random() * 0.5,
                    envelopeTarget: 0.5 + Math.random() * 0.5,
                    envelopeSpeed: 0.01 + Math.random() * 0.02,
                    phase: Math.random() * Math.PI * 2,
                    freq: 20 + Math.random() * 60,
                    freqDrift: 0,
                    burstCounter: 0,
                    burstAmplitude: 0
                });
            }
            
            this.time = 0;
            this.dt = 1 / 1800;
        }

        generatePacket() {
            const packet = [];
            
            for (let ch = 0; ch < this.channels; ch++) {
                const channelData = [];
                const state = this.channelStates[ch];
                
                for (let i = 0; i < this.pointsPerPacket; i++) {
                    const t = this.time + i * this.dt;
                    let value = 0;
                    
                    value += state.pinkNoise.next() * GENERATOR_CONFIG.EMG_NOISE_LEVEL;
                    
                    const envelopeDiff = state.envelopeTarget - state.envelope;
                    state.envelope += envelopeDiff * state.envelopeSpeed;
                    
                    if (Math.random() < 0.01) {
                        state.envelopeTarget = 0.2 + Math.random() * 0.8;
                    }
                    
                    const phaseJitter = gaussianRandom(0, 0.1);
                    value += Math.sin(2 * Math.PI * state.freq * t + state.phase + phaseJitter) 
                             * GENERATOR_CONFIG.EMG_BASE_AMPLITUDE * state.envelope;
                    
                    value += Math.sin(2 * Math.PI * state.freq * 2 * t + state.phase * 1.5) 
                             * GENERATOR_CONFIG.EMG_BASE_AMPLITUDE * 0.3 * state.envelope * Math.random();
                    value += Math.sin(2 * Math.PI * state.freq * 0.5 * t + state.phase * 0.7) 
                             * GENERATOR_CONFIG.EMG_BASE_AMPLITUDE * 0.4 * state.envelope * Math.random();
                    
                    state.freqDrift += gaussianRandom(0, 0.5);
                    state.freqDrift *= 0.95;
                    state.freq = Math.max(15, Math.min(100, state.freq + state.freqDrift * 0.1));
                    
                    if (state.burstCounter > 0) {
                        value += state.burstAmplitude * Math.exp(-state.burstCounter * 0.3) 
                                 * Math.sin(state.burstCounter * 2);
                        state.burstCounter++;
                        if (state.burstCounter > 10) state.burstCounter = 0;
                    } else if (Math.random() < GENERATOR_CONFIG.EMG_BURST_PROBABILITY) {
                        state.burstCounter = 1;
                        state.burstAmplitude = (Math.random() * 0.5 + 0.5) * GENERATOR_CONFIG.EMG_BURST_AMPLITUDE 
                                               * (Math.random() > 0.5 ? 1 : -1);
                    }
                    
                    value += gaussianRandom(0, GENERATOR_CONFIG.EMG_NOISE_LEVEL * 0.5);
                    
                    channelData.push(value);
                }
                
                packet.push(channelData);
            }
            
            this.time += this.pointsPerPacket * this.dt;
            return packet;
        }

        reset() {
            this.time = 0;
            for (let state of this.channelStates) {
                state.envelope = 0.5 + Math.random() * 0.5;
                state.envelopeTarget = 0.5 + Math.random() * 0.5;
                state.phase = Math.random() * Math.PI * 2;
                state.freq = 20 + Math.random() * 60;
                state.burstCounter = 0;
            }
        }
    }

    // ==================== IMU数据生成器 ====================
    class IMUDataGenerator {
        constructor() {
            this.time = 0;
            this.dt = 1 / 100;
            
            this.accState = {
                values: [0, 0, 1],
                velocities: [0, 0, 0],
                targets: [0, 0, 1]
            };
            
            this.gyrState = {
                values: [0, 0, 0],
                velocities: [0, 0, 0],
                pulseCounters: [0, 0, 0],
                pulseAmplitudes: [0, 0, 0]
            };
            
            this.magState = {
                values: [25, 0, -40],
                drifts: [0, 0, 0]
            };
        }

        generateAccPacket() {
            const packet = [];
            const state = this.accState;
            const range = GENERATOR_CONFIG.ACC_RANGE;
            
            for (let axis = 0; axis < 3; axis++) {
                if (Math.random() < 0.02) {
                    if (axis === 2) {
                        state.targets[axis] = 0.8 + Math.random() * 0.4;
                    } else {
                        state.targets[axis] = gaussianRandom(0, range * 0.3);
                    }
                }
                
                const diff = state.targets[axis] - state.values[axis];
                state.velocities[axis] += diff * 0.05;
                state.velocities[axis] *= 0.9;
                state.values[axis] += state.velocities[axis];
                
                if (Math.random() < GENERATOR_CONFIG.IMU_PULSE_PROBABILITY) {
                    state.values[axis] += gaussianRandom(0, range * 0.5);
                }
                
                const noise = gaussianRandom(0, 0.05);
                
                packet.push([state.values[axis] + noise]);
            }
            
            return packet;
        }

        generateGyrPacket() {
            const packet = [];
            const state = this.gyrState;
            const range = GENERATOR_CONFIG.GYR_RANGE;
            
            for (let axis = 0; axis < 3; axis++) {
                let value = state.values[axis];
                
                value += gaussianRandom(0, range * GENERATOR_CONFIG.IMU_DRIFT_RATE);
                value *= 0.98;
                
                if (state.pulseCounters[axis] > 0) {
                    value += state.pulseAmplitudes[axis] * Math.exp(-state.pulseCounters[axis] * 0.1);
                    state.pulseCounters[axis]++;
                    if (state.pulseCounters[axis] > 30) state.pulseCounters[axis] = 0;
                } else if (Math.random() < GENERATOR_CONFIG.IMU_PULSE_PROBABILITY * 2) {
                    state.pulseCounters[axis] = 1;
                    state.pulseAmplitudes[axis] = gaussianRandom(0, range * 0.8);
                }
                
                state.values[axis] = value;
                const noise = gaussianRandom(0, range * 0.02);
                
                packet.push([value + noise]);
            }
            
            return packet;
        }

        generateMagPacket() {
            const packet = [];
            const state = this.magState;
            const range = GENERATOR_CONFIG.MAG_RANGE;
            
            for (let axis = 0; axis < 3; axis++) {
                state.drifts[axis] += gaussianRandom(0, 0.1);
                state.drifts[axis] *= 0.99;
                
                let value = state.values[axis] + state.drifts[axis];
                
                if (Math.random() < 0.01) {
                    value += gaussianRandom(0, range * 0.2);
                }
                
                const noise = gaussianRandom(0, range * 0.02);
                
                packet.push([value + noise]);
            }
            
            return packet;
        }

        reset() {
            this.time = 0;
            this.accState.values = [0, 0, 1];
            this.accState.velocities = [0, 0, 0];
            this.accState.targets = [0, 0, 1];
            this.gyrState.values = [0, 0, 0];
            this.gyrState.pulseCounters = [0, 0, 0];
            this.magState.drifts = [0, 0, 0];
        }
    }

    // ==================== 组合数据生成器 ====================
    class DataGenerator {
        constructor() {
            this.emg = new EMGDataGenerator();
            this.imu = new IMUDataGenerator();
        }

        generateEMGPacket() {
            return this.emg.generatePacket();
        }

        generateIMUAccPacket() {
            return this.imu.generateAccPacket();
        }

        generateIMUGyrPacket() {
            return this.imu.generateGyrPacket();
        }

        generateIMUMagPacket() {
            return this.imu.generateMagPacket();
        }

        reset() {
            this.emg.reset();
            this.imu.reset();
        }
    }

    // ==================== 导出到全局 ====================
    window.EMGDataGenerator = EMGDataGenerator;
    window.IMUDataGenerator = IMUDataGenerator;
    window.DataGenerator = DataGenerator;
    window.GENERATOR_CONFIG = GENERATOR_CONFIG;

})();
