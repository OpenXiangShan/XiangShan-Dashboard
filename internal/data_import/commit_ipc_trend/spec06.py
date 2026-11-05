"""SPEC06 workload names used by local import and history recovery."""

BENCHMARKS = {
    "perlbench": "400.perlbench",
    "bzip2": "401.bzip2",
    "gcc": "403.gcc",
    "mcf": "429.mcf",
    "gobmk": "445.gobmk",
    "hmmer": "456.hmmer",
    "sjeng": "458.sjeng",
    "libquantum": "462.libquantum",
    "h264ref": "464.h264ref",
    "omnetpp": "471.omnetpp",
    "astar": "473.astar",
    "xalancbmk": "483.xalancbmk",
    "bwaves": "410.bwaves",
    "gamess": "416.gamess",
    "milc": "433.milc",
    "zeusmp": "434.zeusmp",
    "gromacs": "435.gromacs",
    "cactusADM": "436.cactusADM",
    "leslie3d": "437.leslie3d",
    "namd": "444.namd",
    "dealII": "447.dealII",
    "soplex": "450.soplex",
    "povray": "453.povray",
    "calculix": "454.calculix",
    "GemsFDTD": "459.GemsFDTD",
    "tonto": "465.tonto",
    "lbm": "470.lbm",
    "wrf": "481.wrf",
    "sphinx3": "482.sphinx3",
}


def benchmark_for(workload: str) -> str:
    prefix = workload.split("_", 1)[0]
    try:
        return BENCHMARKS[prefix]
    except KeyError as error:
        raise ValueError(f"no SPEC06 benchmark mapping for workload: {workload}") from error
