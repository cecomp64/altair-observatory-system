module AstroChartsHelper
  MONTHS = %w[Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec].freeze
  COLORS = %w[#6366f1 #0ea5e9 #f59e0b #10b981 #ec4899 #8b5cf6 #14b8a6 #f97316].freeze

  # Altitude through the night for one or more objects (results from
  # Astro::Visibility#for_night on the same night), with the telescope's
  # effective limit (horizon mask + minimum altitude), the Moon, and twilight
  # shading. `labels` names each result.
  def altitude_chart(results, labels:, night:, height: "h-72", now_line: true)
    results = results.is_a?(Array) ? results : [ results ] # (Array() would splat a Struct)
    return tag.p("No coordinates to chart.", class: "text-sm text-slate-400") if results.empty?

    # A category axis labelled in the telescope's local time, so the chart
    # reads the same wherever the viewer is.
    zone = night.site.time_zone
    times = results.first.times
    axis_labels = times.map { |t| t.in_time_zone(zone).strftime("%H:%M") }
    index_of = ->(time) { time && ((time - times.first) / (night.step_hours * 3600)).round.clamp(0, times.size - 1) }

    datasets = results.each_with_index.map do |result, i|
      { label: labels[i], data: result.altitudes.map { |a| a.round(2) }, borderColor: COLORS[i % COLORS.size],
        borderWidth: 2, pointRadius: 0, tension: 0.2 }
    end
    datasets << { label: "Limit (horizon / min altitude)", data: results.first.limits.map { |a| a.round(2) },
                  borderColor: "#94a3b8", borderDash: [ 4, 4 ], borderWidth: 1, pointRadius: 0, fill: "start",
                  backgroundColor: "rgba(148, 163, 184, 0.15)" }
    datasets << { label: "Moon (#{(night.moon_illumination * 100).round}%)", data: night.moon_positions.map { |alt, _| alt.round(2) },
                  borderColor: "#cbd5e1", borderWidth: 1, pointRadius: 0, hidden: results.size > 3 }

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
        controller: "chart", chart_type_value: "line", chart_now_line_value: now_line,
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

  private

  def in_season?(month, peak)
    return false unless peak

    s = peak[:start_month]
    e = peak[:end_month]
    s <= e ? month.between?(s, e) : (month >= s || month <= e)
  end
end
