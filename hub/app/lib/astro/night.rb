module Astro
  # One night at one site on a regular time grid: local noon-to-noon window
  # centred on local midnight, with the Sun, Moon and sidereal time computed
  # once and shared by every object evaluated on it.
  class Night
    STEP_MINUTES = 5

    attr_reader :site, :date, :times, :twilight

    def self.for(site, date, step_minutes: STEP_MINUTES)
      @cache ||= {}
      key = [ site.cache_key, date, step_minutes ]
      @cache.shift if @cache.size > 64
      @cache[key] ||= new(site, date, step_minutes: step_minutes)
    end

    def initialize(site, date, step_minutes: STEP_MINUTES)
      @site = site
      @date = date
      @step_minutes = step_minutes
      midnight = site.time_zone.local(date.year, date.month, date.day) + 1.day
      count = (24 * 60 / step_minutes)
      @times = (0..count).map { |i| midnight - 12.hours + (i * step_minutes).minutes }
      @jd = @times.map { |t| Coordinates.julian_day(t) }
      @lst = @jd.map { |jd| Coordinates.lst(jd, site.longitude) }
      @twilight = Twilight.new(site, date)
      @midnight_jd = Coordinates.julian_day(midnight)
    end

    def step_hours
      @step_minutes / 60.0
    end

    def darkness
      @twilight.darkness
    end

    def dark_flags
      @dark_flags ||= begin
        window = darkness
        @times.map { |t| window ? window.cover?(t) : false }
      end
    end

    def sun_altitudes
      @sun_altitudes ||= @times.map { |t| @twilight.sun_altitude(t) }
    end

    def moon_positions
      @moon_positions ||= @times.map { |t| Ephemeris.moon_alt_az(t, site.latitude, site.longitude) }
    end

    def moon_illumination
      @moon_illumination ||= Ephemeris.moon_illumination(@times[@times.size / 2])
    end

    # [[altitude, azimuth], ...] for a J2000 object on this night's grid.
    def track(ra, dec)
      ra_date, dec_date = Coordinates.precess(ra.to_f, dec.to_f, @midnight_jd)
      @lst.map { |lst| Coordinates.alt_az(ra_date, dec_date, site.latitude, lst) }
    end
  end
end
