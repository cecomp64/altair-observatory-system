module Astro
  # Visibility of an object from a site (port of astrophotography-database
  # visibility_service.py, upgraded to use the telescope's horizon mask).
  # "Clear" means above max(horizon mask at that azimuth, minimum altitude).
  class Visibility
    MONTH_SAMPLE_DAYS = [ 1, 15 ].freeze

    Result = Struct.new(
      :date, :times, :altitudes, :azimuths, :limits, :transit_time, :max_altitude, :max_altitude_in_darkness,
      :hours_clear_in_darkness, :hours_above_min_altitude, :rise_time, :set_time, :moon_separation,
      :moon_illumination, :twilight, keyword_init: true
    ) do
      def visible? = hours_clear_in_darkness >= 1.0
    end

    attr_reader :site

    def initialize(site)
      @site = site
    end

    def night(date)
      Night.for(site, date)
    end

    def for_night(ra, dec, date, min_altitude: site.min_altitude)
      night = night(date)
      horizon = site.horizon(min_altitude: min_altitude)
      track = night.track(ra, dec)
      altitudes = track.map(&:first)
      azimuths = track.map(&:last)
      limits = azimuths.map { |az| horizon.effective_min_altitude(az) }
      dark = night.dark_flags
      step = night.step_hours

      max_index = altitudes.each_with_index.max_by(&:first).last
      dark_altitudes = altitudes.each_index.select { |i| dark[i] }.map { |i| altitudes[i] }
      clear_dark = altitudes.each_index.count { |i| dark[i] && altitudes[i] >= limits[i] }
      above_min = altitudes.count { |a| a >= min_altitude }

      rise = set = nil
      altitudes.each_cons(2).with_index do |(a, b), i|
        rise ||= night.times[i + 1] if a < 0 && b >= 0
        set = night.times[i + 1] if a >= 0 && b < 0
      end

      moon_ra, moon_dec = Ephemeris.moon_ra_dec(night.times[night.times.size / 2])
      Result.new(
        date: date, times: night.times, altitudes: altitudes, azimuths: azimuths, limits: limits,
        transit_time: night.times[max_index], max_altitude: altitudes[max_index].round(1),
        max_altitude_in_darkness: (dark_altitudes.max || 0.0).round(1),
        hours_clear_in_darkness: (clear_dark * step).round(2), hours_above_min_altitude: (above_min * step).round(2),
        rise_time: rise, set_time: set,
        moon_separation: Coordinates.separation(ra.to_f, dec.to_f, moon_ra, moon_dec).round(1),
        moon_illumination: night.moon_illumination.round(2), twilight: night.twilight.times
      )
    end

    # astrophotography-database's imaging score: higher is better tonight.
    def self.imaging_score(result, progress: 0.0, priority: 0)
      return 0.0 unless result.visible?

      (result.max_altitude * 0.5 + result.hours_clear_in_darkness * 10 + (100 - progress.to_f) * 0.3 + priority.to_i * 5).round(1)
    end

    # Monthly scores for a year and the peak season (the old app's algorithm:
    # months scoring >= 80% of the best form the peak; the longest run of
    # consecutive peak months, wrapping around the year, is the season).
    def best_viewing(ra, dec, year: Date.current.year, min_altitude: site.min_altitude)
      Rails.cache.fetch([ "astro/best-viewing/v1", site.cache_key, ra.to_f.round(4), dec.to_f.round(4), year, min_altitude ]) do
        months = (1..12).map do |month|
          samples = MONTH_SAMPLE_DAYS.map { |day| for_night(ra, dec, Date.new(year, month, day), min_altitude: min_altitude) }
          hours = samples.sum(&:hours_clear_in_darkness) / samples.size
          altitude = samples.sum(&:max_altitude_in_darkness) / samples.size
          { month: month, score: (hours * 8 + altitude * 0.5).round(1), hours: hours.round(1), altitude: altitude.round(1) }
        end
        best = months.max_by { |m| m[:score] }
        { months: months, best_month: best[:score].positive? ? best[:month] : nil, peak_season: peak_season(months) }
      end
    end

    # Declination band that can reach `min_altitude` at transit.
    def declination_bounds(min_altitude = site.min_altitude)
      range = 90.0 - min_altitude
      [ [ site.latitude - range, -90.0 ].max, [ site.latitude + range, 90.0 ].min ]
    end

    private

    def peak_season(months)
      max = months.map { |m| m[:score] }.max.to_f
      peak = months.select { |m| m[:score].positive? && m[:score] >= max * 0.8 }.map { |m| m[:month] }
      return nil if peak.empty?

      set = peak.to_set
      best_start = best_end = peak.first
      best_length = 1
      peak.each do |start|
        length = 0
        current = start
        while set.include?(current) && length <= 12
          length += 1
          current = (current % 12) + 1
        end
        next unless length > best_length

        best_length = length
        best_start = start
        best_end = ((start + length - 2) % 12) + 1
      end
      { start_month: best_start, end_month: best_end }
    end
  end
end
