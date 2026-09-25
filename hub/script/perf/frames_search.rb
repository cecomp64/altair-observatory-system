# Seeds synthetic frames (default 100k) and measures /frames search latency
# (§9 P3 exit: filtered search p95 < 300 ms, cone search p95 < 500 ms).
#
#   bin/rails runner script/perf/frames_search.rb [count]
#
# Uses the development database; seeded frames are tagged origin
# "legacy_index" with file names "perf-*" and removed at the end unless KEEP=1.
count = (ARGV.first || 100_000).to_i
telescope = Telescope.first or abort "Create a telescope first (bin/rails db:seed)"
train = telescope.default_optical_train
targets = Target.where(telescope: telescope).to_a
abort "Create a target first" if targets.empty?

if Frame.where("file_name LIKE 'perf-%'").count < count
  Frame.where("file_name LIKE 'perf-%'").delete_all
  rng = Random.new(42)
  filters = %w[L R G B Ha OIII SII]
  now = Time.current
  (0...count).each_slice(5_000) do |slice|
    rows = slice.map do |i|
      target = targets[i % targets.size]
      night = Date.new(2024, 1, 1) + rng.rand(900)
      { sha256: Digest::SHA256.hexdigest("perf-#{i}"), telescope_id: telescope.id, optical_train_id: train.id,
        target_id: (i % 17).zero? ? nil : target.id, project_id: (i % 17).zero? ? nil : target.project_id,
        assignment_source: (i % 17).zero? ? "unlinked" : "header_token", image_type: (i % 9).zero? ? "flat" : "light",
        night: night, date_obs: night.to_time + 20.hours + rng.rand(36_000), filter: filters[i % filters.size],
        exposure_s: [ 60, 120, 300, 600 ][i % 4], gain: 100, binning: "1x1",
        ra_deg: rng.rand(360.0).round(5), dec_deg: (rng.rand(150.0) - 60).round(5),
        file_name: "perf-#{i}.fits", logical_path: "raw/perf/#{i}.fits", status: "valid",
        storage: { "nas" => i.even?, "s3" => "STANDARD_IA" }, quality: {}, headers: {}, origin: "legacy_index",
        created_at: now, updated_at: now }
    end
    Frame.insert_all(rows)
  end
  ActiveRecord::Base.connection.execute("ANALYZE frames")
end

admin = User.admin.first || User.first
scope = FramePolicy::Scope.new(admin, Frame).resolve
measure = lambda do |label, runs, &block|
  times = runs.times.map do |i|
    t = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    block.call(i)
    (Process.clock_gettime(Process::CLOCK_MONOTONIC) - t) * 1000
  end.sort
  p95 = times[(times.size * 0.95).floor - 1]
  puts format("%-26s n=%d p50=%.1fms p95=%.1fms max=%.1fms", label, runs, times[times.size / 2], p95, times.last)
  p95
end

search = ->(params) { s = Frames::Search.new(scope, params); s.relation.order(date_obs: :desc, id: :desc).limit(50).to_a; s.relation.count; s.stats }
filters = [
  { filter: "Ha" }, { telescope: telescope.slug, image_type: "light" }, { night_from: "2025-01-01", night_to: "2025-03-31" },
  { status: "valid", filter: "OIII", exposure: "300" }, { unassigned: "1" }, { q: targets.first.name }, { storage: "nas", filter: "L" }
]
filtered = measure.call("filtered search", 40) { |i| search.call(filters[i % filters.size]) }
cone = measure.call("cone search (r=1 deg)", 40) { |i| search.call(ra: (i * 9.1 % 360).to_s, dec: ((i * 3.7 % 120) - 50).to_s, radius: "1") }
puts "frames: #{Frame.count}"
puts(filtered < 300 && cone < 500 ? "PASS" : "FAIL")

Frame.where("file_name LIKE 'perf-%'").delete_all unless ENV["KEEP"] == "1"
