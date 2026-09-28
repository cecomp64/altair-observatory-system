module AstroChartsHelper
  MONTHS = %w[Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec].freeze
  COLORS = %w[#6366f1 #0ea5e9 #f59e0b #10b981 #ec4899 #8b5cf6 #14b8a6 #f97316].freeze

  # Altitude through the night for one or more objects (results from
  # Astro::Visibility#for_night on the same night), with the telescope's
  # effective limit (horizon mask + minimum altitude), the Moon, and twilight
  # shading. `labels` names each result.
  #
  # The limit depends on where the object is, so one shaded limit (the first
  # object's) only suits objects close together, like mosaic panels. With
  # `each_limit: true` every object gets its own dashed limit in its colour.
  # `previews` ([{ key:, label:, result: }]) are drawn hidden, with their
  # limits, for the horizon-preview controller to show on hover. With no
  # results the chart still draws the night, for previews to appear on.
  def altitude_chart(results, labels:, night:, height: "h-72", now_line: true, each_limit: false, previews: [])
    results = results.is_a?(Array) ? results : [ results ] # (Array() would splat a Struct)
    return tag.p("No coordinates to chart.", class: "text-sm text-slate-400") if results.empty? && previews.empty?

    # A category axis labelled in the telescope's local time, so the chart
    # reads the same wherever the viewer is.
    zone = night.site.time_zone
    times = night.times
    axis_labels = times.map { |t| t.in_time_zone(zone).strftime("%H:%M") }
    index_of = ->(time) { time && ((time - times.first) / (night.step_hours * 3600)).round.clamp(0, times.size - 1) }
    series = ->(values) { values.map { |a| a.round(2) } }

    datasets = results.each_with_index.map do |result, i|
      { label: labels[i], data: series.(result.altitudes), borderColor: COLORS[i % COLORS.size],
        borderWidth: 2, pointRadius: 0, tension: 0.2 }
    end
    if each_limit && results.size > 1
      results.each_with_index do |result, i|
        datasets << { label: "#{labels[i]} limit", data: series.(result.limits), borderColor: COLORS[i % COLORS.size],
                      borderDash: [ 4, 4 ], borderWidth: 1, pointRadius: 0, hideInLegend: true }
      end
      # A legend entry for the limits, which draws nothing.
      datasets << { label: "Limits (horizon / min altitude, dashed)", data: [], borderColor: "#94a3b8", borderDash: [ 4, 4 ], borderWidth: 1 }
    elsif results.any?
      datasets << { label: "Limit (horizon / min altitude)", data: series.(results.first.limits),
                    borderColor: "#94a3b8", borderDash: [ 4, 4 ], borderWidth: 1, pointRadius: 0, fill: "start",
                    backgroundColor: "rgba(148, 163, 184, 0.15)" }
    end
    previews.each do |preview|
      shared = { hidden: true, hideInLegend: true, previewKey: preview[:key], borderColor: "#e11d48", pointRadius: 0 }
      datasets << shared.merge(label: preview[:label], data: series.(preview[:result].altitudes), borderWidth: 2, tension: 0.2)
      datasets << shared.merge(label: "#{preview[:label]} limit", data: series.(preview[:result].limits), borderWidth: 1, borderDash: [ 4, 4 ])
    end
    datasets << { label: "Moon (#{(night.moon_illumination * 100).round}%)", data: night.moon_positions.map { |alt, _| alt.round(2) },
                  borderColor: "#000000", backgroundColor: "#000000", borderWidth: 1.5, pointRadius: 0, hidden: results.size > 3 }

    tw = night.twilight.times
    band = lambda do |from, to, alpha|
      next nil unless from && to

      { type: "box", xMin: index_of.(from), xMax: index_of.(to), backgroundColor: "rgba(15, 23, 42, #{alpha})",
        borderWidth: 0, drawTime: "beforeDatasetsDraw" }
    end
    annotations = {
      civil: band.(tw[:sunset], tw[:sunrise], 0.04), nautical: band.(tw[:civil_dusk], tw[:civil_dawn], 0.05),
      astro: band.(tw[:nautical_dusk], tw[:nautical_dawn], 0.06), dark: band.(tw[:astronomical_dusk], tw[:astronomical_dawn], 0.08)
    }.compact

    tag.div(class: height) do
      tag.canvas("", data: {
        controller: "chart", chart_type_value: "line", chart_now_line_value: now_line, horizon_preview_target: "chart",
        chart_time_start_value: (times.first.to_f * 1000).to_i, chart_time_step_value: (night.step_hours * 3_600_000).to_i,
        chart_data_value: { labels: axis_labels, datasets: datasets }.to_json,
        chart_options_value: {
          interaction: { mode: "index", intersect: false },
          scales: {
            x: { ticks: { maxTicksLimit: 13, maxRotation: 0 }, title: { display: true, text: "Local time (#{zone.tzinfo.name})" } },
            y: { min: 0, max: 90, title: { display: true, text: "Altitude (°)" } }
          },
          plugins: { annotation: { annotations: annotations }, legend: { labels: { boxWidth: 12 } } }
        }.to_json
      })
    end
  end

  def best_viewing_chart(best, height: "h-40")
    months = best[:months]
    peak = best[:peak_season]
    colors = months.map { |m| in_season?(m[:month], peak) ? "#6366f1" : "#c7d2fe" }
    tag.div(class: height) do
      chart_tag(type: "bar", data: {
        labels: MONTHS, datasets: [ { label: "Score", data: months.map { |m| m[:score] }, backgroundColor: colors } ]
      }, options: {
        plugins: { legend: { display: false }, tooltip: { callbacks: {} } },
        scales: { y: { beginAtZero: true, title: { display: true, text: "Imaging score" } } }
      })
    end
  end

  def peak_season_text(best)
    peak = best[:peak_season]
    return "Never well placed from here" unless peak

    start_name = Date::MONTHNAMES[peak[:start_month]]
    end_name = Date::MONTHNAMES[peak[:end_month]]
    peak[:start_month] == peak[:end_month] ? "Best in #{start_name}" : "Best #{start_name} – #{end_name}"
  end

  # A tiny inline SVG of altitude through the night (dark hours shaded).
  def altitude_sparkline(result, night, width: 120, height: 28)
    alts = result.altitudes
    dark = night.dark_flags
    n = alts.size - 1
    x = ->(i) { (i.to_f / n * width).round(1) }
    y = ->(alt) { (height - (alt.clamp(0, 90) / 90.0 * height)).round(1) }
    first_dark = dark.index(true)
    last_dark = dark.rindex(true)
    shade = first_dark ? tag.rect(x: x.(first_dark), y: 0, width: x.(last_dark) - x.(first_dark), height: height, fill: "#e2e8f0") : "".html_safe
    limit = tag.polyline(points: result.limits.each_with_index.map { |a, i| "#{x.(i)},#{y.(a)}" }.join(" "), fill: "none", stroke: "#94a3b8", "stroke-dasharray": "2 2", "stroke-width": 0.75)
    line = tag.polyline(points: alts.each_with_index.map { |a, i| "#{x.(i)},#{y.(a)}" }.join(" "), fill: "none", stroke: "#6366f1", "stroke-width": 1.5)
    tag.svg(safe_join([ shade, limit, line ]), width: width, height: height, viewBox: "0 0 #{width} #{height}", class: "inline-block", "aria-label": "Altitude tonight")
  end

  # The telescope's horizon by azimuth: the horizon mask (filled) and the
  # minimum imaging altitude (dashed). `paths` overlays objects' paths across
  # the sky during the night's darkness, with a dot on each local hour
  # ([{ label:, result: }], results of Astro::Visibility#for_night on `night`).
  def horizon_chart(site, night: nil, paths: [], height: "h-64")
    horizon = site.horizon(min_altitude: 0)
    datasets = []
    if horizon.mask?
      datasets << { label: "Horizon", borderColor: "#64748b", backgroundColor: "rgba(100, 116, 139, 0.3)", borderWidth: 1,
                    fill: "start", pointRadius: 0,
                    data: (0..360).step(2).map { |az| alt = horizon.mask_altitude(az).round(1); { x: az, y: alt, tip: "Horizon #{alt}° at azimuth #{az}°" } } }
    end
    min = site.min_altitude.round(1)
    datasets << { label: "Minimum altitude (#{min}°)", borderColor: "#94a3b8", borderDash: [ 4, 4 ], borderWidth: 1, pointRadius: 0,
                  data: [ { x: 0, y: min }, { x: 360, y: min } ] }
    paths.each_with_index do |path, i|
      datasets << sky_path_dataset(path[:result], night, path[:label], COLORS[i % COLORS.size])
    end

    tag.div(class: height) do
      tag.canvas("", data: {
        controller: "chart", chart_type_value: "line",
        chart_data_value: { datasets: datasets }.to_json,
        chart_options_value: {
          interaction: { mode: "nearest", intersect: false },
          scales: {
            x: { type: "linear", min: 0, max: 360, ticks: { stepSize: 45 }, title: { display: true, text: "Azimuth (°) · N 0 · E 90 · S 180 · W 270" } },
            y: { min: 0, max: 90, title: { display: true, text: "Altitude (°)" } }
          },
          plugins: { legend: { labels: { boxWidth: 12 } } }
        }.to_json
      })
    end
  end

  # A small inline SVG of a telescope's horizon for its cards: the horizon
  # mask filled from azimuth 0 to 360, the minimum altitude dashed.
  def horizon_silhouette(telescope)
    site = Astro::Site.for(telescope)
    horizon = site.horizon(min_altitude: 0)
    width = 360
    height = 60
    y = ->(alt) { (height - (alt.clamp(0, 90) / 90.0 * height)).round(1) }
    min_y = y.(site.min_altitude)
    min = format_angle(site.min_altitude)

    parts = [ 90, 180, 270 ].map { |az| tag.line(x1: az, x2: az, y1: 0, y2: height, stroke: "#e2e8f0", "vector-effect": "non-scaling-stroke") }
    if horizon.mask?
      alts = (0..360).step(3).map { |az| [ az, horizon.mask_altitude(az) ] }
      outline = alts.map { |az, alt| "#{az},#{y.(alt)}" }.join(" ")
      parts << tag.polygon(points: "0,#{height} #{outline} #{width},#{height}", fill: "#cbd5e1", stroke: "#94a3b8", "vector-effect": "non-scaling-stroke")
      low, high = alts.map(&:last).minmax.map { |a| format_angle(a) }
      caption = "Horizon #{low}–#{high}"
    else
      caption = "Flat horizon"
    end
    parts << tag.line(x1: 0, x2: width, y1: min_y, y2: min_y, stroke: "#6366f1", "stroke-dasharray": "4 3", "vector-effect": "non-scaling-stroke")

    tag.div(class: "w-full") do
      safe_join([
        tag.div(class: "flex justify-between text-[11px] text-slate-400") { safe_join([ tag.span(caption), tag.span("#{min} minimum", class: "text-indigo-500") ]) },
        tag.svg(safe_join(parts), viewBox: "0 0 #{width} #{height}", preserveAspectRatio: "none", class: "block w-full h-10 rounded bg-slate-50",
                role: "img", "aria-label": "#{caption}, minimum altitude #{min}, by azimuth from north"),
        tag.div(class: "flex justify-between text-[10px] leading-3 text-slate-400") { safe_join(%w[N E S W N].map { |d| tag.span(d) }) }
      ])
    end
  end

  private

  # One object's path across the sky while it is dark (astronomical darkness,
  # or nautical when there is none) and above 0°, split where it leaves the
  # sky or wraps past north.
  def sky_path_dataset(result, night, label, color)
    zone = night.site.time_zone
    shown = night.darkness ? night.dark_flags : night.sun_altitudes.map { |a| a < -12 }
    data = []
    radii = []
    previous = nil
    result.azimuths.each_with_index do |az, i|
      alt = result.altitudes[i]
      unless shown[i] && alt >= 0
        previous = nil
        next
      end

      if previous.nil? || (az - previous).abs > 180
        data << { x: az.round(1), y: nil }
        radii << 0
      end
      time = result.times[i].in_time_zone(zone)
      data << { x: az.round(1), y: alt.round(1), tip: "#{label} · #{time.strftime('%H:%M')} · #{alt.round}° up, azimuth #{az.round}°" }
      radii << (time.min.zero? ? 3 : 0)
      previous = az
    end
    { label: label, data: data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: radii, tension: 0.2 }
  end

  def in_season?(month, peak)
    return false unless peak

    s = peak[:start_month]
    e = peak[:end_month]
    s <= e ? month.between?(s, e) : (month >= s || month <= e)
  end
end
