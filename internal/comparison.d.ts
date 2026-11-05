import type {
  Assessment,
  Counter,
  Observation,
  SliceResult,
} from "../src/types/history";

declare const comparison: {
  runs(a: Observation, b: Observation): Assessment;
  slice(
    a: Observation,
    b: Observation,
    left?: SliceResult,
    right?: SliceResult,
  ): Assessment;
  counter(
    a: Observation,
    b: Observation,
    leftSlice: SliceResult | undefined,
    rightSlice: SliceResult | undefined,
    left: Counter | undefined,
    right: Counter | undefined,
    denominatorA: Counter | undefined,
    denominatorB: Counter | undefined,
    perInstruction: boolean,
  ): Assessment;
};
export = comparison;
