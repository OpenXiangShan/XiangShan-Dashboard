export const SPEC_VERSIONS = ["06", "17", "26"] as const;
export type SpecVersion = (typeof SPEC_VERSIONS)[number];
export type SpecCategory = "int" | "fp";

interface SpecBenchmarkGroup {
  name: string;
  benchmarks: readonly string[];
}

export const SPEC_BENCHMARK_GROUPS: Record<
  SpecVersion,
  Record<SpecCategory, SpecBenchmarkGroup>
> = {
  "06": {
    int: {
      name: "SPECint06",
      benchmarks: [
        "400.perlbench",
        "401.bzip2",
        "403.gcc",
        "429.mcf",
        "445.gobmk",
        "456.hmmer",
        "458.sjeng",
        "462.libquantum",
        "464.h264ref",
        "471.omnetpp",
        "473.astar",
        "483.xalancbmk",
      ],
    },
    fp: {
      name: "SPECfp06",
      benchmarks: [
        "410.bwaves",
        "416.gamess",
        "433.milc",
        "434.zeusmp",
        "435.gromacs",
        "436.cactusADM",
        "437.leslie3d",
        "444.namd",
        "447.dealII",
        "450.soplex",
        "453.povray",
        "454.calculix",
        "459.GemsFDTD",
        "465.tonto",
        "470.lbm",
        "481.wrf",
        "482.sphinx3",
      ],
    },
  },
  "17": {
    int: {
      name: "SPECintrate17",
      benchmarks: [
        "500.perlbench_r",
        "502.gcc_r",
        "505.mcf_r",
        "520.omnetpp_r",
        "523.xalancbmk_r",
        "525.x264_r",
        "531.deepsjeng_r",
        "541.leela_r",
        "548.exchange2_r",
        "557.xz_r",
      ],
    },
    fp: {
      name: "SPECfprate17",
      benchmarks: [
        "503.bwaves_r",
        "507.cactuBSSN_r",
        "508.namd_r",
        "510.parest_r",
        "511.povray_r",
        "519.lbm_r",
        "521.wrf_r",
        "526.blender_r",
        "527.cam4_r",
        "538.imagick_r",
        "544.nab_r",
        "549.fotonik3d_r",
        "554.roms_r",
      ],
    },
  },
  "26": {
    int: {
      name: "SPECintrate26",
      benchmarks: [
        "706.stockfish_r",
        "707.ntest_r",
        "708.sqlite_r",
        "710.omnetpp_r",
        "714.cpython_r",
        "721.gcc_r",
        "723.llvm_r",
        "727.cppcheck_r",
        "729.abc_r",
        "734.vpr_r",
        "735.gem5_r",
        "750.sealcrypto_r",
        "753.ns3_r",
        "777.zstd_r",
      ],
    },
    fp: {
      name: "SPECfprate26",
      benchmarks: [
        "709.cactus_r",
        "722.palm_r",
        "731.astcenc_r",
        "736.ocio_r",
        "737.gmsh_r",
        "748.flightdm_r",
        "749.fotonik3d_r",
        "765.roms_r",
        "766.femflow_r",
        "767.nest_r",
        "772.marian_r",
        "782.lbm_r",
      ],
    },
  },
};

export function specVersionFromSubset(subset?: string): SpecVersion {
  const match = /(?:^|[/-])spec(17|26)(?:[/-]|$)/i.exec(subset || "");
  return match?.[1] === "17" || match?.[1] === "26" ? match[1] : "06";
}

export function specVersionFromText(text: string): SpecVersion | undefined {
  const match =
    /(?:^|[^a-z0-9])spec(?:(?:int|fp)?rate)?(?:20)?(06|17|26)(?=[^0-9]|$)/i.exec(
      text,
    );
  return match?.[1] as SpecVersion | undefined;
}

export function getSpecGroup(
  version: SpecVersion,
  category: SpecCategory,
): SpecBenchmarkGroup {
  return SPEC_BENCHMARK_GROUPS[version][category];
}

export function getSpecGeomeanName(
  _version: SpecVersion,
  category: SpecCategory,
): string {
  return `GEOMEAN(${category})`;
}
