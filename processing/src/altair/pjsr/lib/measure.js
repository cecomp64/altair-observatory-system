// SubframeSelector measurements (SPEC §9.5 step 3, per-frame weights).
// Column layout of SubframeSelector.measurements as of PixInsight 1.8.9/1.9;
// verify on the installed version in the phase 0 spike.
#ifndef __ALTAIR_MEASURE__
#define __ALTAIR_MEASURE__

var SS_COL = { path: 3, fwhm: 5, eccentricity: 6, psfSignalWeight: 7, psfSNR: 8, noise: 21, stars: 23 };

// items: [{sha256, path}] -> [{sha256, fwhm, eccentricity, psf_signal_weight, snr, noise_sigma, stars}]
function altairMeasure(items) {
   var P = new SubframeSelector;
   P.routine = SubframeSelector.prototype.MeasureSubframes;
   P.subframes = items.map(function (i) { return [true, i.path, "", ""]; });
   P.fileCache = false;
   if (!P.executeGlobal())
      throw new Error("SubframeSelector failed");
   var byPath = {};
   for (var r = 0; r < P.measurements.length; ++r) {
      var row = P.measurements[r];
      byPath[File.unixPathToWindows(row[SS_COL.path])] = row;
      byPath[row[SS_COL.path]] = row;
   }
   return items.map(function (i) {
      var row = byPath[i.path] || byPath[File.unixPathToWindows(i.path)];
      if (!row)
         return { sha256: i.sha256, measured: false };
      return { sha256: i.sha256, measured: true, fwhm: row[SS_COL.fwhm], eccentricity: row[SS_COL.eccentricity],
               psf_signal_weight: row[SS_COL.psfSignalWeight], snr: row[SS_COL.psfSNR], noise_sigma: row[SS_COL.noise],
               stars: row[SS_COL.stars] };
   });
}

#endif
