/**
 * AI Inference Service - ONNX model execution for Grid-Guardian policy
 */
import * as ort from 'onnxruntime-node';
import * as fs from 'fs';
import * as path from 'path';
import { config } from '../config/env';
import { logger } from '../utils/logger';

// Discrete action mapping (matches edge_inference.py)
const DISCRETE_ACTIONS: Record<number, { name: string; kw: number }> = {
  0: { name: 'charge_small', kw: 1.0 },
  1: { name: 'charge_large', kw: 3.0 },
  2: { name: 'idle', kw: 0.0 },
  3: { name: 'discharge_small', kw: -1.0 },
  4: { name: 'discharge_large', kw: -3.0 },
  5: { name: 'offer_sell', kw: -1.5 },
  6: { name: 'offer_hold', kw: 0.0 },
};

// Observation keys (18 dimensions)
const OBS_KEYS = [
  'soc_kwh',
  'soc_capacity_kwh',
  'pv_gen_kw',
  'load_kw',
  'net_kw',
  'battery_power_kw',
  'price_signal',
  'forecast_irradiance_1h',
  'forecast_irradiance_3h',
  'forecast_temp_1h',
  'actual_irradiance_wm2',
  'voltage_v',
  'current_a',
  // Padding to 18 dims
  'reserved_1',
  'reserved_2',
  'reserved_3',
  'reserved_4',
  'reserved_5',
];

export interface Observation {
  soc_kwh: number;
  soc_capacity_kwh: number;
  pv_gen_kw: number;
  load_kw: number;
  net_kw: number;
  battery_power_kw: number;
  price_signal: number;
  forecast_irradiance_1h?: number;
  forecast_irradiance_3h?: number;
  forecast_temp_1h?: number;
  actual_irradiance_wm2?: number;
  voltage_v?: number;
  current_a?: number;
  [key: string]: number | undefined;
}

export interface InferenceResult {
  action_index: number;
  action_name: string;
  action_kw: number;
  logits: number[];
  safety_applied: boolean;
  original_kw?: number;
}

export interface ModelInfo {
  model_name: string;
  obs_dim: number;
  act_dim: number;
  obs_keys: string[];
  normalization: string;
  target_platform: string;
  metrics?: Record<string, number>;
}

class InferenceService {
  private session: ort.InferenceSession | null = null;
  private normMeans: Float32Array | null = null;
  private normStds: Float32Array | null = null;
  private modelInfo: ModelInfo | null = null;
  private isInitialized = false;

  async initialize(): Promise<void> {
    if (this.isInitialized) return;

    try {
      const modelPath = path.resolve(__dirname, '../../', config.modelPath);
      const normPath = path.resolve(__dirname, '../../', config.normParamsPath);
      const modelCardPath = modelPath.replace('.onnx', '').replace('cql_policy', 'model_card.json');

      // Load model card if exists
      const modelCardDir = path.dirname(modelPath);
      const cardPath = path.join(modelCardDir, 'model_card.json');
      if (fs.existsSync(cardPath)) {
        this.modelInfo = JSON.parse(fs.readFileSync(cardPath, 'utf-8'));
        logger.info(`Loaded model card: ${this.modelInfo?.model_name}`);
      }

      // Load ONNX model
      if (fs.existsSync(modelPath)) {
        this.session = await ort.InferenceSession.create(modelPath);
        logger.info(`ONNX model loaded from ${modelPath}`);
      } else {
        logger.warn(`Model file not found at ${modelPath} - inference disabled`);
        return;
      }

      // Load normalization params (if exists and needed)
      // Note: wrapped policies have normalization baked in
      // Only load if we need manual normalization
      if (fs.existsSync(normPath)) {
        // For npz files we would need a proper parser
        // For now, assume model has baked-in normalization
        logger.info('Normalization params available (using baked-in normalization)');
      }

      this.isInitialized = true;
      logger.info('Inference service initialized');

    } catch (error) {
      logger.error('Failed to initialize inference service:', error);
      throw error;
    }
  }

  getModelInfo(): ModelInfo | null {
    return this.modelInfo;
  }

  isReady(): boolean {
    return this.isInitialized && this.session !== null;
  }

  /**
   * Convert observation object to float32 array
   */
  private observationToArray(obs: Observation): Float32Array {
    const arr = new Float32Array(18);

    arr[0] = obs.soc_kwh ?? 0;
    arr[1] = obs.soc_capacity_kwh ?? 4.0;
    arr[2] = obs.pv_gen_kw ?? 0;
    arr[3] = obs.load_kw ?? 0;
    arr[4] = obs.net_kw ?? (obs.pv_gen_kw ?? 0) - (obs.load_kw ?? 0);
    arr[5] = obs.battery_power_kw ?? 0;
    arr[6] = obs.price_signal ?? 0.1;
    arr[7] = obs.forecast_irradiance_1h ?? 0;
    arr[8] = obs.forecast_irradiance_3h ?? 0;
    arr[9] = obs.forecast_temp_1h ?? 25;
    arr[10] = obs.actual_irradiance_wm2 ?? 0;
    arr[11] = obs.voltage_v ?? 230;
    arr[12] = obs.current_a ?? 0;
    // Reserved dims (padding)
    arr[13] = 0;
    arr[14] = 0;
    arr[15] = 0;
    arr[16] = 0;
    arr[17] = 0;

    return arr;
  }

  /**
   * Apply safety clip to action
   */
  private safetyClip(
    actionKw: number,
    soc: number,
    socCap: number,
    options: {
      socMinFrac?: number;
      socMaxFrac?: number;
      maxCharge?: number;
      maxDischarge?: number;
      dt?: number;
    } = {}
  ): { clipped: number; applied: boolean } {
    const {
      socMinFrac = 0.10,
      socMaxFrac = 0.95,
      maxCharge = 3.0,
      maxDischarge = 3.0,
      dt = 5.0 / 60.0,
    } = options;

    let capped = Math.max(-maxDischarge, Math.min(maxCharge, actionKw));
    const original = capped;

    const newSoc = soc + capped * dt;

    if (newSoc < socMinFrac * socCap) {
      capped = (socMinFrac * socCap - soc) / dt;
    } else if (newSoc > socMaxFrac * socCap) {
      capped = (socMaxFrac * socCap - soc) / dt;
    }

    return {
      clipped: capped,
      applied: capped !== original,
    };
  }

  /**
   * Run inference on a single observation
   */
  async infer(obs: Observation, applySafety = true): Promise<InferenceResult> {
    if (!this.session) {
      throw new Error('Inference service not initialized');
    }

    // Convert observation to tensor
    const obsArray = this.observationToArray(obs);
    const inputTensor = new ort.Tensor('float32', obsArray, [1, 18]);

    // Run inference
    const results = await this.session.run({ observation: inputTensor });
    const logits = Array.from(results[Object.keys(results)[0]].data as Float32Array);

    // Get action with highest logit
    const actionIndex = logits.indexOf(Math.max(...logits));
    const action = DISCRETE_ACTIONS[actionIndex] || { name: 'idle', kw: 0 };

    let actionKw = action.kw;
    let safetyApplied = false;
    let originalKw: number | undefined;

    // Apply safety clip if requested
    if (applySafety) {
      const safetyResult = this.safetyClip(
        actionKw,
        obs.soc_kwh,
        obs.soc_capacity_kwh
      );
      if (safetyResult.applied) {
        originalKw = actionKw;
        actionKw = safetyResult.clipped;
        safetyApplied = true;
      }
    }

    return {
      action_index: actionIndex,
      action_name: action.name,
      action_kw: actionKw,
      logits,
      safety_applied: safetyApplied,
      original_kw: originalKw,
    };
  }

  /**
   * Run batch inference
   */
  async inferBatch(observations: Observation[], applySafety = true): Promise<InferenceResult[]> {
    return Promise.all(
      observations.map(obs => this.infer(obs, applySafety))
    );
  }

  /**
   * Validate observation vector
   */
  validateObservation(obs: Observation): { valid: boolean; errors: string[] } {
    const errors: string[] = [];

    if (obs.soc_kwh === undefined) errors.push('soc_kwh is required');
    if (obs.soc_capacity_kwh === undefined) errors.push('soc_capacity_kwh is required');
    if (obs.pv_gen_kw === undefined) errors.push('pv_gen_kw is required');
    if (obs.load_kw === undefined) errors.push('load_kw is required');

    if (obs.soc_kwh !== undefined && obs.soc_kwh < 0) {
      errors.push('soc_kwh must be non-negative');
    }
    if (obs.soc_capacity_kwh !== undefined && obs.soc_capacity_kwh <= 0) {
      errors.push('soc_capacity_kwh must be positive');
    }

    return {
      valid: errors.length === 0,
      errors,
    };
  }
}

export const inferenceService = new InferenceService();
