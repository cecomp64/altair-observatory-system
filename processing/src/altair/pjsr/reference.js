// PROJECT_REFERENCE: calibrate the night's lights, pick the best frame by PSF
// Signal Weight from the preferred filter, save it (SPEC §9.2).
#include "lib/json_io.js"
#include "lib/measure.js"

function altairCalibrate(job, group, postfix) {
   var C = new ImageCalibration;
   C.targetFrames = group.lights.map(function (l) { return [true, l.path]; });
   C.masterBiasEnabled = false;
   C.masterDarkEnabled = !!group.dark;
   if (group.dark) C.masterDarkPath = group.dark.path;
   C.masterFlatEnabled = !!group.flat;
   if (group.flat) C.masterFlatPath = group.flat.path;
   C.optimizeDarks = false;
   C.outputDirectory = job.output_dir;
   C.outputExtension = ".xisf";
   C.outputPostfix = postfix || "_c";
   C.overwriteExistingFiles = true;
   if (!C.executeGlobal())
      throw new Error("ImageCalibration failed");
   var out = [];
   for (var i = 0; i < C.outputData.length; ++i)
      out.push({ sha256: group.lights[i].sha256, path: C.outputData[i][0] });
   return out.filter(function (o) { return o.path && o.path.length > 0; });
}

function altairDebayerIfOsc(job, items) {
   if (!job.camera || job.camera.type != "osc")
      return items;
   var D = new Debayer;
   D.cfaPattern = Debayer.prototype.Auto;
   D.inputHints = "";
   D.targetItems = items.map(function (i) { return [true, i.path]; });
   D.outputDirectory = job.output_dir;
   D.outputPostfix = "_d";
   D.overwriteExistingFiles = true;
   if (!D.executeGlobal())
      throw new Error("Debayer failed");
   return items.map(function (i, k) { return { sha256: i.sha256, path: D.outputFileData[k][0] }; });
}

function altairReference(job, result) {
   var filters = job.filters || [];
   var best = null, bestFilter = null, measuredAll = [];
   for (var f = 0; f < filters.length && best === null; ++f) {
      var groups = job.groups.filter(function (g) { return g.filter == filters[f]; });
      var items = [];
      for (var g = 0; g < groups.length; ++g)
         items = items.concat(altairCalibrate(job, groups[g], "_c"));
      items = altairDebayerIfOsc(job, items);
      var measured = altairMeasure(items);
      measuredAll = measuredAll.concat(measured);
      for (var i = 0; i < measured.length; ++i)
         if (measured[i].measured && (best === null || measured[i].psf_signal_weight > best.psf_signal_weight)) {
            best = measured[i];
            best.path = items[i].path;
            bestFilter = filters[f];
         }
   }
   if (best === null)
      throw new Error("no measurable light frame for the reference");
   var win = altairOpen(best.path);
   altairSetKeyword(win, "ALTREFV", job.version, "Altair project reference version");
   var solved = null;
   try {
      // Plate solve so overlap can be checked without star matching (§9.2).
      var solver = new ImageSolver();
      solver.Init(win, false);
      solver.solverCfg.showStars = false;
      solved = solver.SolveImage(win) ? true : false;
   } catch (e) {
      altairLog("plate solving skipped: " + e);
   }
   var out = altairSave(win, job.output_dir + "/reference_v" + job.version + ".xisf");
   win.forceClose();
   result.output("reference", out, best.sha256);
   result.value.metrics = { filter: bestFilter, psf_signal_weight: best.psf_signal_weight, fwhm: best.fwhm, solved: solved };
   result.value.frames = measuredAll;
}
