// Altair PJSR helpers: job.json in, result.json out (docs/pixinsight-cli.md).
#ifndef __ALTAIR_JSON_IO__
#define __ALTAIR_JSON_IO__

var ALTAIR_RUNNER_VERSION = "1";

function altairReadJson(path) {
   return JSON.parse(File.readTextFile(path));
}

function altairWriteJson(path, value) {
   File.writeTextFile(path, JSON.stringify(value, null, 1));
}

function altairLog(message) {
   console.writeln("<b>[altair]</b> " + message);
   console.flush();
}

function altairSoftware() {
   return {
      pixinsight: CoreApplication.versionMajor + "." + CoreApplication.versionMinor + "." + CoreApplication.versionRelease,
      runner: ALTAIR_RUNNER_VERSION
   };
}

// A result being built; `write` is called exactly once, from success or catch.
function AltairResult(job) {
   this.job = job;
   this.value = { status: "ok", error: null, outputs: [], metrics: {}, frames: [], measurements: [], software: altairSoftware() };
   this.output = function (role, path, sourceSha) {
      this.value.outputs.push({ role: role, path: path, source_sha256: sourceSha || null });
   };
   this.fail = function (error) {
      this.value.status = "error";
      this.value.error = "" + error;
   };
   this.write = function () {
      altairWriteJson(this.job.work_dir + "/result.json", this.value);
   };
}

function altairEnsureDir(path) {
   if (!File.directoryExists(path))
      File.createDirectory(path, true);
}

// Opens the first image of a file; the caller closes the window.
function altairOpen(path) {
   var windows = ImageWindow.open(path);
   if (windows.length < 1)
      throw new Error("cannot open " + path);
   for (var i = 1; i < windows.length; ++i)
      windows[i].forceClose();
   return windows[0];
}

function altairSave(window, path) {
   if (!window.saveAs(path, false, false, false, false))
      throw new Error("cannot save " + path);
   return path;
}

function altairSetKeyword(window, name, value, comment) {
   var keywords = [];
   var existing = window.keywords;
   for (var i = 0; i < existing.length; ++i)
      if (existing[i].name != name)
         keywords.push(existing[i]);
   keywords.push(new FITSKeyword(name, "" + value, comment || ""));
   window.keywords = keywords;
}

function altairMedian(values) {
   var v = values.filter(function (x) { return x !== null && x !== undefined && isFinite(x); }).sort(function (a, b) { return a - b; });
   if (v.length == 0)
      return null;
   var m = Math.floor(v.length / 2);
   return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// Fraction of non-zero pixels: the overlap of a registered master with the
// reference geometry (uncovered pixels are exactly 0, SPEC §9.3).
function altairCoverage(window) {
   var image = window.mainView.image;
   var nonZero = 0, total = image.width * image.height;
   var row = new Float32Array(image.width);
   for (var y = 0; y < image.height; ++y) {
      image.getSamples(row, new Rect(0, y, image.width, y + 1), 0);
      for (var x = 0; x < image.width; ++x)
         if (row[x] != 0)
            ++nonZero;
   }
   return total ? nonZero / total : 0;
}

#endif
