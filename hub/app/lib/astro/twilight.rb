module Astro
  # Sun-altitude crossings for a night: sunset, civil/nautical/astronomical
  # dusk and dawn, sunrise. Searches forward from local noon of `date` (so
  # "the night of Sept 24" is the evening of the 24th to the morning of the
  # 25th), like astroplan's `which="next"`.
  class Twilight
    HORIZONS = { sunset: 0, civil_dusk: -6, nautical_dusk: -12, astronomical_dusk: -18 }.freeze
    DAWNS = { astronomical_dawn: -18, nautical_dawn: -12, civil_dawn: -6, sunrise: 0 }.freeze
    STEP = 10.minutes

    attr_reader :site, :date

    def initialize(site, date)
      @site = site
      @date = date
      @noon = site.time_zone.local(date.year, date.month, date.day, 12)
    end

    def times
      @times ||= HORIZONS.to_h { |name, h| [ name, crossing(h, :setting) ] }
                         .merge(DAWNS.to_h { |name, h| [ name, crossing(h, :rising) ] })
    end

    def astronomical_dusk = times[:astronomical_dusk]
    def astronomical_dawn = times[:astronomical_dawn]

    # Astronomical darkness window, or nil where the Sun never gets 18 deg down.
    def darkness
      dusk = astronomical_dusk
      dawn = astronomical_dawn
      dusk && dawn && dawn > dusk ? (dusk..dawn) : nil
    end

    def sun_altitude(time)
      Ephemeris.sun_alt_az(time, site.latitude, site.longitude).first
    end

    private

    # First moment after local noon (within 36 h) the Sun crosses `horizon`
    # going down (:setting) or up (:rising), refined to a second.
    def crossing(horizon, direction)
      t0 = @noon
      a0 = sun_altitude(t0) - horizon
      (1..(36.hours / STEP)).each do |i|
        t1 = @noon + i * STEP
        a1 = sun_altitude(t1) - horizon
        if (direction == :setting && a0 >= 0 && a1 < 0) || (direction == :rising && a0 < 0 && a1 >= 0)
          return bisect(t0, t1, horizon)
        end
        t0 = t1
        a0 = a1
      end
      nil
    end

    def bisect(lo, hi, horizon)
      lo_sign = sun_altitude(lo) >= horizon
      while hi - lo > 1
        mid = lo + (hi - lo) / 2
        if (sun_altitude(mid) >= horizon) == lo_sign
          lo = mid
        else
          hi = mid
        end
      end
      Time.at(((lo.to_f + hi.to_f) / 2).round).in_time_zone(site.time_zone)
    end
  end
end
