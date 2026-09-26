"""A stand-in for PixInsight in tests. Called like the real thing
(``fake -n=5 --automation-mode … -r=<runner>,<job.json>``), it reads the job
and writes small FITS outputs and a result.json that follow the contract in
docs/pixinsight-cli.md.

Behaviour is steered by the JSON file named by ``--fake-control=<path>``:
``{"fail": "message"}``, ``{"no_result": true}``, ``{"exit": 3}``,
``{"sleep": 5}``, ``{"reject": [sha, …]}``, ``{"fwhm": {"<night>": 3.1}}``,
``{"overlap": {"<night>": 0.4}}`` and ``{"calls": "<path>"}`` (appends each job).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from astropy.io import fits


def image(path: Path, seed_text: str, *, zero_border: bool = False) -> str:
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:8], 16)
    data = np.random.default_rng(seed).normal(1000, 50, (32, 48)).astype(np.float32)
    if zero_border:
        data[:, :2] = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(data).writeto(path, overwrite=True)
    return str(path)


def main(argv: list[str]) -> int:
    arg = next(a for a in argv if a.startswith("-r="))
    job_path = Path(arg[3:].split(",", 1)[1])
    job = json.loads(job_path.read_text())
    control_arg = next((a for a in argv if a.startswith("--fake-control=")), None)
    control = json.loads(Path(control_arg.split("=", 1)[1]).read_text()) if control_arg else {}
    if control.get("calls"):
        with open(control["calls"], "a") as f:
            f.write(json.dumps({"kind": job["kind"], "phase": job.get("phase"), "job_id": job["job_id"]}) + "\n")
    if control.get("sleep"):
        time.sleep(control["sleep"])
    if control.get("exit"):
        return int(control["exit"])
    out_dir = Path(job["output_dir"])
    result = {"status": "ok", "outputs": [], "metrics": {}, "frames": [], "measurements": [],
              "software": {"pixinsight": "fake", "runner": "1"}}
    if control.get("no_result"):
        return 0
    if control.get("fail"):
        result = {"status": "error", "error": control["fail"]}
    elif job["kind"] == "CALIB_MASTER":
        shas = sorted(f["sha256"] for f in job["frames"])
        result["outputs"].append({"role": "master", "path": image(out_dir / "master.fits", "calib:" + ",".join(shas))})
        result["metrics"] = {"frames": len(shas)}
    elif job["kind"] == "PROJECT_REFERENCE":
        first = job["groups"][0]["lights"][0]
        result["outputs"].append({"role": "reference", "path": image(out_dir / "reference.fits", "ref:" + first["sha256"]),
                                  "source_sha256": first["sha256"]})
        result["metrics"] = {"filter": job["filters"][0], "psf_signal_weight": 0.01, "pixel_scale_arcsec": 1.41}
    elif job["kind"] == "NIGHT_STACK":
        lights = [light for g in job["groups"] for light in g["lights"]]
        rejected = set(control.get("reject", []))
        seed = job["night"] + ":" + ",".join(sorted(light["sha256"] for light in lights)) + ":" + (job["reference"] or {}).get("sha256", "")
        result["outputs"].append({"role": "master", "path": image(out_dir / "night_master.fits", seed, zero_border=True)})
        if job["stack_kind"] == "final" and job.get("keep_calibrated_frames"):
            for i, light in enumerate(lights):
                result["outputs"].append({"role": "calibrated_frame", "source_sha256": light["sha256"],
                                          "path": image(out_dir / f"{Path(light['path']).stem}_c.fits", "cal:" + light["sha256"])})
        fwhm = (control.get("fwhm") or {}).get(job["night"], 2.5)
        for light in lights:
            used = light["sha256"] not in rejected
            result["frames"].append({"sha256": light["sha256"], "used": used, "fwhm": fwhm, "eccentricity": 0.45, "stars": 900,
                                     "psf_signal_weight": 0.02, "weight": 0.02, "reason": None if used else "rejected by the fake"})
        used = [f for f in result["frames"] if f["used"]]
        result["metrics"] = {"frames": len(used), "rejected": len(lights) - len(used), "fwhm": fwhm, "eccentricity": 0.45,
                             "overlap_fraction": (control.get("overlap") or {}).get(job["night"], 0.96), "engine": "fake"}
    elif job["kind"] == "MERGE" and job.get("phase") == "measure":
        result["measurements"] = [{"sha256": n["sha256"], "psf_signal_weight": float(n["frames"] or 1), "noise_sigma": 0.1, "scale": 1.0}
                                  for n in job["nights"]]
    elif job["kind"] == "MERGE":
        assert job.get("weights"), "integrate needs weights"
        seed = ",".join(f"{n['sha256']}={job['weights'][n['sha256']]}" for n in job["nights"])
        result["outputs"].append({"role": "master", "path": image(out_dir / "multi_night_master.fits", seed)})
        result["outputs"].append({"role": "coverage", "path": image(out_dir / "coverage.fits", "cov:" + seed)})
        result["metrics"] = {"nights": len(job["nights"])}
    (Path(job["work_dir"]) / "result.json").write_text(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
