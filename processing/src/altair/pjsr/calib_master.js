// CALIB_MASTER: integrate one group of calibration subs into a master (SPEC §6.5, §8).
// Flats are calibrated first with their dark-flat or bias master.
#include "lib/json_io.js"
#include "lib/integrate.js"

function altairCalibMaster(job, result) {
   var m = job.master;
   var paths = job.frames.map(function (f) { return f.path; });
   var cal = job.calibrate_with || {};
   if (m.kind == "FLAT") {
      var dark = cal.darkflat || cal.dark || null;
      var bias = cal.bias || null;
      var C = new ImageCalibration;
      C.targetFrames = paths.map(function (p) { return [true, p]; });
      C.masterBiasEnabled = !!bias && !dark;
      if (bias && !dark) C.masterBiasPath = bias.path;
      C.masterDarkEnabled = !!dark;
      if (dark) C.masterDarkPath = dark.path;
      C.masterFlatEnabled = false;
      C.calibrateBias = false;
      C.optimizeDarks = false;
      C.outputDirectory = job.output_dir;
      C.outputExtension = ".xisf";
      C.outputPostfix = "_c";
      C.overwriteExistingFiles = true;
      if (!C.executeGlobal())
         throw new Error("ImageCalibration of the flats failed");
      paths = C.outputData.map(function (o) { return o[0]; }).filter(function (p) { return p && p.length > 0; });
   }
   var flat = m.kind == "FLAT";
   var w = altairIntegrate({
      images: paths,
      rejection: paths.length >= 8 ? ImageIntegration.prototype.WinsorizedSigmaClip : ImageIntegration.prototype.PercentileClip,
      normalization: flat ? ImageIntegration.prototype.Multiplicative : ImageIntegration.prototype.NoNormalization,
      rejectionNormalization: flat ? ImageIntegration.prototype.EqualizeFluxes : ImageIntegration.prototype.NoRejectionNormalization
   });
   var win = w.integration;
   var imagetyp = { BIAS: "Master Bias", DARK: "Master Dark", DARKFLAT: "Master Dark Flat", FLAT: "Master Flat" }[m.kind];
   altairSetKeyword(win, "IMAGETYP", "'" + imagetyp + "'", "Altair calibration master");
   if (m.exposure !== null) altairSetKeyword(win, "EXPTIME", m.exposure, "[s]");
   if (m.gain !== null) altairSetKeyword(win, "GAIN", m.gain, "");
   if (m.offset !== null) altairSetKeyword(win, "OFFSET", m.offset, "");
   if (m.sensor_temp !== null) altairSetKeyword(win, "CCD-TEMP", m.sensor_temp, "[C] median of the subs");
   if (m.filter) altairSetKeyword(win, "FILTER", "'" + m.filter + "'", "");
   if (m.focal_length) altairSetKeyword(win, "FOCALLEN", m.focal_length, "[mm]");
   if (m.rotator_pos !== null && m.rotator_pos !== undefined) altairSetKeyword(win, "ROTATOR", m.rotator_pos, m.rotator_units || "");
   altairSetKeyword(win, "ALTNSUBS", paths.length, "subs integrated");
   var out = altairSave(win, job.output_dir + "/master_" + m.kind.toLowerCase() + ".xisf");
   result.output("master", out);
   result.value.metrics.frames = paths.length;
   altairCloseAll(w);
}
