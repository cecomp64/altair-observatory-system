// MERGE (SPEC §9.5). Phase "measure": all night masters in one
// SubframeSelector pass, so weights share one scale. Phase "integrate":
// write ALTNWGHT into temporary copies, LocalNormalization to the heaviest
// master, ImageIntegration with keyword weights and rangeLow = 0 (uncovered
// pixels excluded), coverage map, optional autocrop.
#include "lib/json_io.js"
#include "lib/measure.js"
#include "lib/integrate.js"

function altairMergeMeasure(job, result) {
   var measured = altairMeasure(job.nights.map(function (n) { return { sha256: n.sha256, path: n.path }; }));
   result.value.measurements = measured.map(function (m) {
      return { sha256: m.sha256, psf_signal_weight: m.psf_signal_weight, noise_sigma: m.noise_sigma, scale: 1.0, fwhm: m.fwhm };
   });
}

function altairCoverageMap(job, paths) {
   // Number of nights covering each pixel.
   var first = altairOpen(paths[0]);
   var width = first.mainView.image.width, height = first.mainView.image.height;
   first.forceClose();
   var cov = new ImageWindow(width, height, 1, 32, true, false, "coverage");
   var target = cov.mainView;
   target.beginProcess(UndoFlag_NoSwapFile);
   target.image.fill(0);
   var row = new Float32Array(width), acc = new Float32Array(width);
   for (var k = 0; k < paths.length; ++k) {
      var w = altairOpen(paths[k]);
      for (var y = 0; y < height; ++y) {
         w.mainView.image.getSamples(row, new Rect(0, y, width, y + 1), 0);
         target.image.getSamples(acc, new Rect(0, y, width, y + 1), 0);
         for (var x = 0; x < width; ++x)
            if (row[x] != 0) acc[x] += 1;
         target.image.setSamples(acc, new Rect(0, y, width, y + 1), 0);
      }
      w.forceClose();
   }
   target.endProcess();
   return cov;
}

function altairMergeIntegrate(job, result) {
   var copies = [];
   for (var i = 0; i < job.nights.length; ++i) {
      var n = job.nights[i];
      var w = altairOpen(n.path);
      altairSetKeyword(w, "ALTNWGHT", job.weights[n.sha256], "Altair night weight (" + job.weighting + ")");
      copies.push(altairSave(w, job.work_dir + "/weighted_" + n.night + ".xisf"));   // never the canonical file
      w.forceClose();
   }
   var reference = null;
   for (var j = 0; j < job.nights.length; ++j)
      if (job.nights[j].sha256 == job.normalization_reference)
         reference = copies[j];
   var ln = [];
   var normalization = ImageIntegration.prototype.AdditiveWithScaling;
   if (job.normalization == "local") {
      var L = new LocalNormalization;
      L.referencePathOrViewId = reference;
      L.referenceIsView = false;
      L.targetItems = copies.map(function (p) { return [true, p]; });
      L.outputDirectory = job.work_dir;
      L.generateNormalizedImages = LocalNormalization.prototype.GenerateNormalizedImages_Never;
      L.overwriteExistingFiles = true;
      if (!L.executeGlobal())
         throw new Error("LocalNormalization failed");
      ln = copies.map(function (p) { return File.changeExtension(p, ".xnml"); });
      normalization = ImageIntegration.prototype.LocalNormalization;
   }
   var w = altairIntegrate({
      images: copies, lnFiles: ln,
      weightMode: ImageIntegration.prototype.KeywordWeight, weightKeyword: "ALTNWGHT",
      normalization: normalization,
      rejection: job.rejection == "winsorized_sigma" ? ImageIntegration.prototype.WinsorizedSigmaClip : ImageIntegration.prototype.NoRejection,
      rangeClipLow: true, rejectionMaps: job.rejection != "none"
   });
   var coverage = altairCoverageMap(job, copies);
   result.output("coverage", altairSave(coverage, job.output_dir + "/coverage.xisf"));
   if (job.autocrop) {
      // Crop to the pixels covered by at least min_coverage_nights nights.
      var need = job.min_coverage_nights == "all" ? copies.length : job.min_coverage_nights;
      var img = coverage.mainView.image, r = new Rect(img.width, img.height, 0, 0);
      var row = new Float32Array(img.width);
      for (var y = 0; y < img.height; ++y) {
         img.getSamples(row, new Rect(0, y, img.width, y + 1), 0);
         for (var x = 0; x < img.width; ++x)
            if (row[x] >= need) {
               if (x < r.x0) r.x0 = x; if (y < r.y0) r.y0 = y;
               if (x + 1 > r.x1) r.x1 = x + 1; if (y + 1 > r.y1) r.y1 = y + 1;
            }
      }
      if (r.x1 > r.x0 && r.y1 > r.y0) {
         var view = w.integration.mainView;
         view.beginProcess();
         view.image.cropTo(r);
         view.endProcess();
      }
   }
   coverage.forceClose();
   result.output("master", altairSave(w.integration, job.output_dir + "/multi_night_master.xisf"));
   if (w.low) result.output("rejection_low", altairSave(w.low, job.output_dir + "/rejection_low.xisf"));
   if (w.high) result.output("rejection_high", altairSave(w.high, job.output_dir + "/rejection_high.xisf"));
   altairCloseAll(w);
   result.value.metrics = { nights: copies.length };
}
